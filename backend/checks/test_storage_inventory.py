"""Read-only storage inventories with synthetic S3 metadata and no external services."""
import unittest
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import quote
import boto3
from botocore.exceptions import ClientError
from botocore.stub import Stubber
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql
from app.models.storage_inventory import StorageScan
from app.modules.storage_audit import inventory as inv, service
from app.modules.storage_audit.router import NewInventory
from app.tasks import storage_inventory_tasks as task
from app.recovery import database as recovery


def scan_fixture(**changes):
    buckets, location_hash = inv.configuration()
    return SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4(), job_id=uuid.uuid4(), buckets=buckets,
        location_hash=location_hash, request_hash="a" * 64, phase="versions", bucket_slot=0, cursor={},
        reason="Test", created_by=uuid.uuid4(), created_at=datetime.now(timezone.utc),
        started_at=None, completed_at=None, invalidated_at=None, error=None, **changes)


def header(bucket, tid, *, uploads=False, **values):
    return {"Bucket" if uploads else "Name": bucket, "Prefix": quote(inv.tenant_prefix(tid), safe=""),
        "IsTruncated": False, "EncodingType": "url", **values}


def version(key, version_id="null", size=4, latest=True):
    return {"Key": quote(key, safe=""), "VersionId": version_id, "Size": size, "IsLatest": latest}


class MetadataParserTests(unittest.TestCase):
    def test_versions_include_literal_null_history_and_markers_without_reading_objects(self):
        tid = uuid.uuid4(); key = f"{tid}/{uuid.uuid4()}/pages/1.jpg"
        client = MagicMock(list_object_versions=MagicMock(return_value=header("private", tid,
            Versions=[version(key), version(key, "old", 7, False)],
            DeleteMarkers=[{"Key": quote(key, safe=""), "VersionId": "marker", "IsLatest": False}])))
        rows, following = inv.versions_page(client, "private", tid, {})
        self.assertEqual([r["version_id"] for r in rows], ["null", "old", "marker"])
        self.assertEqual([r["byte_size"] for r in rows], [4, 7, 0]); self.assertEqual(following, {})
        self.assertEqual([r["category"] for r in rows], ["documents"] * 3)
        self.assertEqual([call[0] for call in client.mock_calls], ["list_object_versions"])

    def test_decodes_unicode_and_percent_exactly_once_without_path_normalization(self):
        tid = uuid.uuid4(); key = f"{tid}/orfe/../Àvia %2F+100%.jpg"
        client = MagicMock(list_object_versions=MagicMock(return_value=header("p", tid, Versions=[version(key)])))
        rows, _ = inv.versions_page(client, "p", tid, {})
        self.assertEqual(rows[0]["storage_key"], key); self.assertEqual(rows[0]["category"], "other")
        self.assertEqual(inv.decoded("x%252F+y"), "x%2F+y")

    def test_categories_count_unknown_objects_without_database_references(self):
        tid, doc = uuid.uuid4(), uuid.uuid4()
        for suffix, category in ((f"{doc}/pages/1.jpg", "documents"), (f"{doc}/exports/result.zip", "exports"),
                ("ingestion/unknown", "staging"), ("person_media/unknown", "media"),
                ("ai-calls/unknown", "ai_responses"), ("old-import.jpg", "other")):
            with self.subTest(category=category): self.assertEqual(inv.category_for(tid, f"{tid}/{suffix}"), category)

    def test_rejects_neighbors_null_characters_and_oversized_utf8_keys(self):
        tid = uuid.uuid4()
        for key in (f"{uuid.uuid4()}/a", f"{tid}extra/a", f"{tid}/a\x00", f"{tid}/" + "é" * 600, None):
            with self.subTest(key=key), self.assertRaises(ValueError): inv.validate_key(tid, key)

    def test_rejects_ambiguous_empty_invalid_sizes_missing_identity_or_unsupported_listing(self):
        tid = uuid.uuid4(); key = f"{tid}/a"; base = header("p", tid, Versions=[version(key)])
        for response in ({}, {**base, "Name": "foreign"}, {**base, "Prefix": ""},
                {**base, "IsTruncated": 1}, {**base, "EncodingType": None}, {**base, "Versions": {}},
                {**base, "CommonPrefixes": [{"Prefix": "unknown"}]},
                {**base, "Versions": [version(key, size=-1)]}, {**base, "Versions": [version(key, size=True)]},
                {**base, "Versions": [version(key, latest=None)]}, {**base, "Versions": [version(key, version_id=None)]},
                {**base, "Versions": [version(key)] * 101}, {**base, "Versions": [version(key + "%FF")] , "Prefix": "%FF"}):
            with self.subTest(response=response), self.assertRaises(ValueError):
                inv.versions_page(MagicMock(list_object_versions=MagicMock(return_value=response)), "p", tid, {})

    def test_version_pagination_preserves_both_markers_and_rejects_cursor_loops(self):
        tid = uuid.uuid4(); key = f"{tid}/a"
        response = header("p", tid, Versions=[version(key)], IsTruncated=True, NextKeyMarker=quote(key, safe=""), NextVersionIdMarker="next")
        client = MagicMock(list_object_versions=MagicMock(return_value=response))
        _, following = inv.versions_page(client, "p", tid, {})
        self.assertEqual(following, {"KeyMarker": key, "VersionIdMarker": "next"})
        for changed in (response, {**response, "KeyMarker": quote(key, safe=""), "VersionIdMarker": "next"},
                        {**response, "Versions": []}, {**response, "NextVersionIdMarker": None}):
            client.list_object_versions.return_value = changed
            with self.subTest(changed=changed), self.assertRaises(ValueError): inv.versions_page(client, "p", tid, following)
        client.list_object_versions.return_value = header("p", tid, KeyMarker=quote(key, safe=""), VersionIdMarker="next")
        self.assertEqual(inv.versions_page(client, "p", tid, following), ([], {}))

    def test_multipart_list_keeps_two_uploads_with_same_key_and_opaque_ids(self):
        tid = uuid.uuid4(); key = f"{tid}/large.pdf"
        client = MagicMock(list_multipart_uploads=MagicMock(return_value=header("p", tid, uploads=True,
            Uploads=[{"Key": quote(key, safe=""), "UploadId": "z/+=="}], IsTruncated=True,
            NextKeyMarker=quote(key, safe=""), NextUploadIdMarker="z/+==")))
        rows, cursor = inv.uploads_page(client, "p", tid, {})
        self.assertEqual(rows[0]["upload_state"], "pending"); self.assertEqual(cursor["UploadIdMarker"], "z/+==")
        client.list_multipart_uploads.return_value = header("p", tid, uploads=True, KeyMarker=quote(key, safe=""),
            UploadIdMarker="z/+==", Uploads=[{"Key": quote(key, safe=""), "UploadId": "a=="}])
        rows, cursor = inv.uploads_page(client, "p", tid, cursor)
        self.assertEqual(rows[0]["upload_id"], "a=="); self.assertEqual(cursor, {})

    def test_empty_prefixed_multipart_response_uses_bounded_tenant_only_fallback(self):
        tid = uuid.UUID(int=0); key = f"{tid}/large.pdf"; foreign = f"{uuid.UUID(int=(1 << 128) - 1)}/private.pdf"
        client = MagicMock(list_multipart_uploads=MagicMock(side_effect=[
            header("p", tid, uploads=True),
            {"Bucket": "p", "Prefix": "", "KeyMarker": "",
             "UploadIdMarker": "", "EncodingType": "url", "IsTruncated": False,
             "Uploads": [{"Key": quote(key, safe=""), "UploadId": "own"},
                         {"Key": quote(key, safe=""), "UploadId": "foreign-bucket"},
                         {"Key": quote(foreign, safe=""), "UploadId": "must-not-leak"}]},
        ]))
        def parts(**values):
            if values["UploadId"] == "foreign-bucket":
                raise ClientError({"Error": {"Code": "NoSuchUpload"}}, "ListParts")
            return {"Bucket": "p", "Key": key, "UploadId": "own", "PartNumberMarker": 0,
                    "IsTruncated": False, "Parts": []}
        client.list_parts.side_effect = parts
        rows, cursor = inv.uploads_page(client, "p", tid, {})
        self.assertEqual([(row["storage_key"], row["upload_id"]) for row in rows], [(key, "own")])
        self.assertEqual(cursor, {})
        self.assertNotIn(foreign, repr(rows))
        self.assertNotIn("foreign-bucket", repr(rows))
        self.assertEqual(client.list_multipart_uploads.call_args_list[1].kwargs["MaxUploads"], inv.FALLBACK_UPLOAD_LIMIT)
        self.assertNotIn("KeyMarker", client.list_multipart_uploads.call_args_list[1].kwargs)

    def test_unversioned_cursor_accepts_explicit_empty_version_not_missing_identity(self):
        tid = uuid.uuid4(); key = f"{tid}/a"
        marker = key + "[minio_cache:v2,return:]"
        response = header("p", tid, Versions=[version(key)], IsTruncated=True,
            NextKeyMarker=quote(marker, safe=""), NextVersionIdMarker="")
        client = MagicMock(list_object_versions=MagicMock(return_value=response))
        _, cursor = inv.versions_page(client, "p", tid, {})
        self.assertEqual(cursor, {"KeyMarker": marker, "VersionIdMarker": ""})
        client.list_object_versions.return_value = header("p", tid,
            KeyMarker=quote(marker, safe=""), VersionIdMarker="", Versions=[version(f"{tid}/b")])
        rows, following = inv.versions_page(client, "p", tid, cursor)
        self.assertEqual(len(rows), 1); self.assertEqual(following, {})
        for value in (None, 0, False):
            client.list_object_versions.return_value = {**response, "NextVersionIdMarker": value}
            with self.subTest(value=value), self.assertRaises(ValueError):
                inv.versions_page(client, "p", tid, {})
        with self.assertRaises(ValueError):
            inv.validate_cursor(tid, {"KeyMarker": key, "UploadIdMarker": ""}, "UploadIdMarker")

    def test_part_listing_paginates_numbers_not_assumed_object_sizes(self):
        tid = uuid.uuid4(); key = f"{tid}/a"
        upload = SimpleNamespace(storage_key=key, upload_id="upload", parts_cursor=0)
        response = {"Bucket": "p", "Key": key, "UploadId": "upload", "PartNumberMarker": 0, "IsTruncated": True,
            "Parts": [{"PartNumber": 1, "Size": 1024**3}, {"PartNumber": 5, "Size": 9}], "NextPartNumberMarker": 5}
        client = MagicMock(list_parts=MagicMock(return_value=response))
        rows, cursor = inv.parts_page(client, "p", tid, upload)
        self.assertEqual(sum(r["byte_size"] for r in rows), 1024**3 + 9); self.assertEqual(cursor, 5)
        for changed in ({**response, "NextPartNumberMarker": 4}, {**response, "Parts": []},
                {**response, "UploadId": "other"}, {**response, "PartNumberMarker": 3},
                {**response, "Parts": [{"PartNumber": 1, "Size": 1}] * 2}):
            client.list_parts.return_value = changed
            with self.subTest(changed=changed), self.assertRaises(ValueError): inv.parts_page(client, "p", tid, upload)

    def test_item_identity_includes_bucket_version_upload_and_part(self):
        scan = uuid.uuid4(); row = {
            "kind": "version", "storage_key": "key", "version_id": "null", "upload_id": None, "part_number": None}
        identities = {inv.item_id(scan, 0, row), inv.item_id(scan, 1, row), inv.item_id(scan, 0, {**row, "version_id": "old"}),
            inv.item_id(scan, 0, {**row, "kind": "part", "upload_id": "one", "part_number": 1}),
            inv.item_id(scan, 0, {**row, "kind": "part", "upload_id": "two", "part_number": 1})}
        self.assertEqual(len(identities), 5)

    def test_actual_sdk_accepts_explicit_url_encoded_listing_without_network(self):
        client = boto3.client("s3", aws_access_key_id="fixture", aws_secret_access_key="fixture", region_name="us-east-1")
        tid = uuid.uuid4(); key = f"{tid}/Àvia%2F.jpg"
        with Stubber(client) as stub:
            stub.add_response("list_object_versions", header("test-bucket", tid, Versions=[version(key)]),
                {"Bucket": "test-bucket", "Prefix": inv.tenant_prefix(tid), "MaxKeys": 100, "EncodingType": "url"})
            rows, _ = inv.versions_page(client, "test-bucket", tid, {})
            self.assertEqual(rows[0]["storage_key"], key)
            stub.assert_no_pending_responses()

    def test_api_rejects_arbitrary_buckets_prefixes_whitespace_and_large_body(self):
        valid = {"client_id": str(uuid.uuid4()), "reason": " Inventario "}
        self.assertEqual(NewInventory(**valid).reason, "Inventario")
        for changed in ({"reason": " "}, {"reason": "x" * 2001}, {"bucket": "other"}, {"prefix": "/"}):
            with self.subTest(changed=changed), self.assertRaises(ValidationError): NewInventory(**{**valid, **changed})


class InventoryWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_is_idempotent_with_durable_dispatch_and_no_inline_storage_or_commit(self):
        principal = SimpleNamespace(tenant_id=uuid.uuid4(), user_id=uuid.uuid4())
        body = NewInventory(client_id=uuid.uuid4(), reason="Inventario")
        db = MagicMock(scalar=AsyncMock(side_effect=[principal.tenant_id, None]), get=AsyncMock(return_value=None), flush=AsyncMock())
        with patch.object(service, "schedule", AsyncMock()) as schedule, patch.object(service, "scan_out", AsyncMock(return_value={"id": "scan"})), patch.object(task.storage, "_s3") as external:
            await service.create(db, principal, body)
            existing = db.add.call_args_list[-1].args[0]; self.assertIsInstance(existing, StorageScan)
            schedule.assert_awaited_once(); self.assertEqual(schedule.call_args.args[2], "inventory_storage")
            db.get = AsyncMock(return_value=existing); db.scalar = AsyncMock(return_value=principal.tenant_id); db.add.reset_mock()
            await service.create(db, principal, body); db.add.assert_not_called()
            with self.assertRaises(HTTPException): await service.create(db, principal, body.model_copy(update={"reason": "Changed"}))
            external.assert_not_called(); db.commit.assert_not_called()

    async def test_second_active_inventory_rejected_before_s3_or_job_creation(self):
        db = MagicMock(scalar=AsyncMock(side_effect=[uuid.uuid4(), uuid.uuid4()]), get=AsyncMock(return_value=None))
        with self.assertRaises(HTTPException) as error:
            await service.create(db, SimpleNamespace(tenant_id=uuid.uuid4(), user_id=uuid.uuid4()), NewInventory(client_id=uuid.uuid4(), reason="Test"))
        self.assertEqual(error.exception.status_code, 409); db.add.assert_not_called()

    async def test_worker_checkpoints_each_bucket_and_both_part_pages_without_writes(self):
        scan = scan_fixture(); key = f"{scan.tenant_id}/a"
        upload = SimpleNamespace(id=uuid.uuid4(), bucket_slot=0, storage_key=key, upload_id="u", parts_cursor=0, upload_state="pending")
        db = MagicMock(scalar=AsyncMock(side_effect=[upload, upload, None]))
        log, rows_saved = [], []
        async def save(db, scan, slot, rows): rows_saved.extend(rows); log.append("save")
        async def checkpoint(db, scan): log.append("commit:" + scan.phase)
        client = MagicMock()
        client.list_object_versions.side_effect = [header(scan.buckets[0], scan.tenant_id, Versions=[version(key)]), header(scan.buckets[1], scan.tenant_id)]
        client.list_multipart_uploads.side_effect = [header(scan.buckets[0], scan.tenant_id, uploads=True, Uploads=[{"Key": quote(key, safe=""), "UploadId": "u"}]),
            header(scan.buckets[1], scan.tenant_id, uploads=True),
            {"Bucket": scan.buckets[1], "Prefix": "", "KeyMarker": "",
             "UploadIdMarker": "", "EncodingType": "url", "IsTruncated": False, "Uploads": []}]
        client.list_parts.side_effect = [
            {"Bucket": scan.buckets[0], "Key": key, "UploadId": "u", "PartNumberMarker": 0, "IsTruncated": True, "NextPartNumberMarker": 1, "Parts": [{"PartNumber": 1, "Size": 8}]},
            {"Bucket": scan.buckets[0], "Key": key, "UploadId": "u", "PartNumberMarker": 1, "IsTruncated": False, "Parts": [{"PartNumber": 2, "Size": 3}]}]
        with patch.object(task, "running", AsyncMock()), patch.object(task, "checkpoint", checkpoint), \
             patch.object(task, "save_page", save), patch.object(task, "update_active_job", AsyncMock(return_value=True)), patch.object(task, "commit_keep_rls", AsyncMock()):
            await task.process(db, scan, client)
        self.assertEqual(scan.phase, "completed"); self.assertEqual(upload.upload_state, "listed")
        self.assertEqual([r["kind"] for r in rows_saved], ["version", "upload", "part", "part"])
        self.assertEqual(len([v for v in log if v.startswith("commit:")]), 6)
        self.assertEqual(set(call[0] for call in client.mock_calls), {"list_object_versions", "list_multipart_uploads", "list_parts"})

    async def test_disappearing_upload_is_reported_uninspected_not_zeroed_or_deleted(self):
        scan = scan_fixture(); scan.phase = "parts"
        upload = SimpleNamespace(bucket_slot=0, upload_state="pending", parts_cursor=100)
        db = MagicMock(scalar=AsyncMock(side_effect=[upload, None]))
        with patch.object(task, "running", AsyncMock()), patch.object(task, "checkpoint", AsyncMock()), \
             patch.object(task.inventory, "parts_page", side_effect=ClientError({"Error": {"Code": "NoSuchUpload"}}, "ListParts")), \
             patch.object(task, "save_page", AsyncMock()) as save, patch.object(task, "update_active_job", AsyncMock(return_value=True)), patch.object(task, "commit_keep_rls", AsyncMock()):
            await task.process(db, scan, MagicMock())
        self.assertEqual(upload.upload_state, "unavailable"); self.assertEqual(upload.parts_cursor, 100); save.assert_not_awaited()

    async def test_permission_failure_does_not_turn_upload_into_zero_or_complete_scan(self):
        scan = scan_fixture(); scan.phase = "parts"
        upload = SimpleNamespace(bucket_slot=0, upload_state="pending")
        db = MagicMock(scalar=AsyncMock(return_value=upload))
        with patch.object(task, "running", AsyncMock()), patch.object(task.inventory, "parts_page", side_effect=ClientError({"Error": {"Code": "AccessDenied"}}, "ListParts")), self.assertRaises(ClientError):
            await task.process(db, scan, MagicMock())
        self.assertEqual(upload.upload_state, "pending"); self.assertEqual(scan.phase, "parts")

    async def test_cancelled_job_cannot_commit_checkpoint_or_become_completed(self):
        scan = scan_fixture(); db = MagicMock()
        with patch.object(task, "running", AsyncMock()), patch.object(task, "update_active_job", AsyncMock(return_value=False)), patch.object(task, "commit_keep_rls", AsyncMock()) as commit:
            with self.assertRaises(task.InventoryStopped): await task.checkpoint(db, scan)
            scan.phase = "parts"; db.scalar = AsyncMock(return_value=None)
            with self.assertRaises(task.InventoryStopped): await task.process(db, scan, MagicMock())
            commit.assert_not_awaited(); self.assertEqual(scan.phase, "parts")

    async def test_page_insert_does_not_ignore_conflicts_and_cursor_commits_with_rows(self):
        scan = scan_fixture(); db = MagicMock(execute=AsyncMock())
        await task.save_page(db, scan, 0, [inv.entry(scan.tenant_id, f"{scan.tenant_id}/a", "version", version_id="null", is_latest=True, byte_size=1)])
        sql = str(db.execute.call_args.args[0].compile(dialect=postgresql.dialect()))
        self.assertIn("INSERT INTO storage_inventory_items", sql); self.assertNotIn("ON CONFLICT", sql); db.commit.assert_not_called()

    async def test_retry_requires_real_worker_lock_and_does_not_create_another_job(self):
        scan = scan_fixture(); db = MagicMock(scalar=AsyncMock(side_effect=[scan.tenant_id, False]))
        with patch.object(service, "owned", AsyncMock(return_value=scan)), self.assertRaises(HTTPException) as error:
            await service.control(db, scan.tenant_id, scan.id, "retry")
        self.assertEqual(error.exception.status_code, 409); db.add.assert_not_called()
        self.assertIn("pg_try_advisory_xact_lock", str(db.scalar.call_args.args[0]))

    async def test_retry_resumes_same_cursor_job_and_dispatch_generation(self):
        scan = scan_fixture(); scan.cursor = {"KeyMarker": f"{scan.tenant_id}/a", "VersionIdMarker": "old"}
        job = SimpleNamespace(status="error", error="failure", finished_at=datetime.now(timezone.utc))
        dispatch = SimpleNamespace(generation=2)
        db = MagicMock(scalar=AsyncMock(side_effect=[scan.tenant_id, True, job, None]), get=AsyncMock(return_value=dispatch), flush=AsyncMock())
        with patch.object(service, "owned", AsyncMock(return_value=scan)), patch.object(service, "scan_out", AsyncMock()):
            await service.control(db, scan.tenant_id, scan.id, "retry")
        self.assertEqual(job.status, "queued"); self.assertEqual(dispatch.generation, 3)
        self.assertEqual(scan.cursor["VersionIdMarker"], "old"); db.add.assert_not_called(); db.commit.assert_not_called()

    async def test_summary_includes_unknown_and_public_objects_but_never_keys_or_quota_claim(self):
        scan = scan_fixture(); scan.phase = "completed"
        db = MagicMock(get=AsyncMock(return_value=SimpleNamespace(status="completed", error=None)), execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [
            (0, "documents", "version", True, None, 2, 10), (0, "documents", "version", False, None, 1, 7),
            (1, "other", "version", True, None, 1, 3), (0, "other", "upload", None, "unavailable", 1, 0),
            (0, "other", "part", None, None, 1, 8), (1, "documents", "delete_marker", True, None, 1, 0)])))
        result = await service.scan_out(db, scan)
        self.assertEqual(result["observed_payload_bytes"], 28); self.assertEqual(result["counts"]["uninspected_uploads"], 1)
        self.assertFalse(result["consistent_snapshot"]); self.assertFalse(result["quota_enforced"]); self.assertFalse(result["upload_inspection_complete"])
        self.assertNotIn("storage_key", str(result)); self.assertNotIn("buckets", result)

    async def test_restored_or_reconfigured_inventory_is_historical_and_not_resumable(self):
        scan = scan_fixture(); self.assertTrue(inv.matches_configuration(scan))
        scan.invalidated_at = datetime.now(timezone.utc); self.assertFalse(inv.matches_configuration(scan))
        scan.invalidated_at = None; scan.location_hash = "b" * 64; self.assertFalse(inv.matches_configuration(scan))
        connection = MagicMock(execute=AsyncMock(return_value="UPDATE 1"))
        connection.transaction.return_value = MagicMock(__aenter__=AsyncMock(), __aexit__=AsyncMock(return_value=False))
        await recovery.quarantine(connection, {"private": "new-p", "public": "new-u"},
            {"database": {"revisions": ["0047"]}, "buckets": {"private": "old-p", "public": "old-u"}})
        statements = [call.args[0] for call in connection.execute.call_args_list]
        self.assertTrue(any("UPDATE storage_scans SET invalidated_at=now()" in sql for sql in statements))
        self.assertTrue(any("inventory_epoch=inventory_epoch+1" in sql for sql in statements))
        self.assertFalse(any("DELETE FROM storage_inventory_items" in sql for sql in statements))


if __name__ == "__main__": unittest.main()
