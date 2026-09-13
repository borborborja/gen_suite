"""Synthetic staging versions, real byte checks, no live database/storage or deletions."""
import hashlib
import io
import unittest
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from botocore.exceptions import ClientError
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql
from app.modules.documents import cleanup as api, ingestion
from app.tasks import ingest_cleanup_tasks as task
from app.recovery import database as recovery


def identity():
    tid, sid, fid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    key = f"{tid}/ingestion/{sid}/{fid}/0000000000000000-" + "a" * 64
    return tid, sid, fid, key


def listing(bucket, tid, sid, versions=(), markers=(), **extra):
    return {"Name": bucket, "Prefix": task.staging_prefix(tid, sid), "IsTruncated": False,
            "Versions": list(versions), "DeleteMarkers": list(markers), **extra}


class DeletionBoundaryTests(unittest.TestCase):
    def test_exact_staging_namespace_excludes_originals_neighbors_and_path_tricks(self):
        tid, sid, fid, key = identity()
        self.assertEqual(task.validate_staging_key(tid, sid, key), fid)
        for bad in (f"{tid}/{sid}/original.pdf", key.replace(str(tid), str(uuid.uuid4())),
                    key.replace(str(sid), str(uuid.uuid4())), key + "/extra", key.replace("/ingestion/", "/ingestion/../"),
                    key.replace("0000000000000000", "0000000000000001"), key.replace(str(fid), "original")):
            with self.subTest(key=bad), self.assertRaises(ValueError): task.validate_staging_key(tid, sid, bad)

    def test_inventory_keeps_null_historical_versions_and_delete_markers(self):
        tid, sid, _, key = identity()
        client = MagicMock(list_object_versions=MagicMock(return_value=listing("private", tid, sid,
            versions=[{"Key": key, "VersionId": "null", "Size": 4}, {"Key": key, "VersionId": "older", "Size": 3}],
            markers=[{"Key": key, "VersionId": "marker"}])))
        values, cursor = task.version_page(client, "private", tid, sid, {})
        self.assertEqual([row["version_id"] for row in values], ["null", "older", "marker"])
        self.assertEqual([row["byte_size"] for row in values], [4, 3, 0]); self.assertEqual(cursor, {})
        client.list_object_versions.assert_called_once_with(Bucket="private", Prefix=task.staging_prefix(tid, sid), MaxKeys=100)

    def test_inventory_rejects_ambiguous_empty_wrong_bucket_missing_version_and_loop(self):
        tid, sid, _, key = identity()
        base = listing("private", tid, sid, versions=[{"Key": key, "VersionId": "null", "Size": 4}])
        for changed in ({}, {**base, "Name": "public"}, {**base, "Versions": [{"Key": key, "Size": 4}]},
                        {**base, "Versions": [{"Key": key, "VersionId": "null", "Size": task.CHUNK_BYTES + 1}]},
                        {**base, "IsTruncated": True}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                task.version_page(MagicMock(list_object_versions=MagicMock(return_value=changed)), "private", tid, sid, {})
        cursor = {"KeyMarker": key, "VersionIdMarker": "v"}
        response = {**base, "IsTruncated": True, "NextKeyMarker": key, "NextVersionIdMarker": "v"}
        with self.assertRaises(ValueError): task.version_page(MagicMock(list_object_versions=MagicMock(return_value=response)), "private", tid, sid, cursor)

    def test_delete_is_version_specific_idempotent_and_never_bypasses_retention(self):
        tid, sid, _, key = identity(); client = MagicMock()
        item = SimpleNamespace(storage_key=key, version_id="null")
        task.delete_version(client, "private", tid, sid, item)
        client.delete_object.assert_called_once_with(Bucket="private", Key=key, VersionId="null")
        client.delete_object.side_effect = ClientError({"Error": {"Code": "NoSuchVersion"}}, "DeleteObject")
        task.delete_version(client, "private", tid, sid, item)
        client.delete_object.side_effect = ClientError({"Error": {"Code": "AccessDenied"}}, "DeleteObject")
        with self.assertRaises(ClientError): task.delete_version(client, "private", tid, sid, item)
        item.storage_key = f"{tid}/{sid}/original.pdf"
        client.reset_mock()
        with self.assertRaises(ValueError): task.delete_version(client, "private", tid, sid, item)
        client.delete_object.assert_not_called()

    def test_opaque_unversioned_cursor_is_never_treated_as_a_deletion_key(self):
        tid, sid, _, key = identity()
        marker = key + "[minio_cache:v2,return:]"
        client = MagicMock(list_object_versions=MagicMock(return_value=listing("p", tid, sid,
            versions=[{"Key": key, "VersionId": "null", "Size": 1}], IsTruncated=True,
            NextKeyMarker=marker, NextVersionIdMarker="")))
        rows, cursor = task.version_page(client, "p", tid, sid, {})
        self.assertEqual(rows[0]["storage_key"], key)
        self.assertEqual(cursor, {"KeyMarker": marker, "VersionIdMarker": ""})
        client.list_object_versions.return_value = listing("p", tid, sid, KeyMarker=marker, VersionIdMarker="")
        self.assertEqual(task.version_page(client, "p", tid, sid, cursor), ([], {}))
        with self.assertRaises(ValueError): task.validate_staging_key(tid, sid, marker)
        for invalid in ({"KeyMarker": key, "VersionIdMarker": None},
                {"KeyMarker": f"{tid}/original.pdf", "VersionIdMarker": ""}, {"KeyMarker": key}):
            with self.subTest(cursor=invalid), self.assertRaises(ValueError):
                task.validate_staging_cursor(tid, sid, invalid)

    def test_cleanup_requires_two_strict_confirmations(self):
        body = dict(preview_hash="a" * 64, reason="Test", authorize_permanent_staging_deletion=True, local_originals_confirmed=True)
        api.CleanupApproval(**body)
        for changed in ({"local_originals_confirmed": False}, {"authorize_permanent_staging_deletion": 1},
                        {"local_originals_confirmed": "true"}, {"bucket": "public"}, {"prefix": "/"}):
            with self.subTest(changed=changed), self.assertRaises(ValidationError): api.CleanupApproval(**{**body, **changed})


class OriginalVerificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_preserved_original_is_hashed_with_bounded_reads_and_closed(self):
        tid, book_id = uuid.uuid4(), uuid.uuid4(); data = b"original-synthetic"
        book = SimpleNamespace(id=book_id, kind="image_set")
        item = SimpleNamespace(storage_key=f"{tid}/{uuid.uuid5(book_id, 'document')}/pages/1.png", position=1,
            content_type="image/png", checksum=hashlib.sha256(data).hexdigest(), byte_size=len(data))
        for corrupt in (False, True):
            body = MagicMock(wraps=io.BytesIO(b"x" * len(data) if corrupt else data))
            with self.subTest(corrupt=corrupt), patch.object(task.storage, "open_object", AsyncMock(return_value={"Body": body, "ContentLength": len(data)})):
                if corrupt:
                    with self.assertRaises(ValueError): await task.verify_original("private", tid, book, item, AsyncMock())
                else: await task.verify_original("private", tid, book, item, AsyncMock())
            self.assertTrue(all(call.args == (1024 ** 2,) for call in body.read.call_args_list))
            body.close.assert_called_once()

    async def test_foreign_original_path_rejected_before_storage(self):
        item = SimpleNamespace(storage_key="outside/original.pdf", checksum="a" * 64)
        with patch.object(task.storage, "open_object", AsyncMock()) as read, self.assertRaises(ValueError):
            await task.verify_original("private", uuid.uuid4(), SimpleNamespace(id=uuid.uuid4(), kind="pdf"), item, AsyncMock())
        read.assert_not_awaited()

    async def test_corrupt_original_stops_before_inventory_or_deletion(self):
        row = SimpleNamespace(phase="verify_originals", session_id=uuid.uuid4(), tenant_id=uuid.uuid4(), verified_after=None, mode="compact", bucket="private")
        db = MagicMock(scalar=AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4(), book_id=uuid.uuid4(), status="verified")),
            get=AsyncMock(return_value=SimpleNamespace(tenant_id=row.tenant_id, status="completed", document_id=uuid.uuid4())))
        client = MagicMock()
        with patch.object(task, "running", AsyncMock()), patch.object(task, "verify_original", AsyncMock(side_effect=ValueError("corrupt"))), self.assertRaises(ValueError):
            await task.process(db, row, client)
        client.list_object_versions.assert_not_called(); client.delete_object.assert_not_called()


class CleanupWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_approval_fences_once_and_commits_with_outbox_without_storage_calls(self):
        tid, sid, uid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        principal = SimpleNamespace(tenant_id=tid, user_id=uid)
        row = SimpleNamespace(id=sid, title="Test", status="uploading", updated_at=datetime.now(timezone.utc), job_id=None,
            reserved_bytes=100, received_bytes=5)
        db = MagicMock(scalar=AsyncMock(side_effect=[1, 0, tid, 1, 0]), get=AsyncMock(return_value=None), flush=AsyncMock())
        with patch.object(api, "owned", AsyncMock(return_value=row)), patch.object(api, "schedule", AsyncMock()) as schedule, \
             patch.object(api, "cleanup_out", AsyncMock(return_value={"id": "confirmed"})), patch.object(task.storage, "_s3") as external:
            compared, _ = await api.preview_data(db, tid, sid)
            body = api.CleanupApproval(preview_hash=compared["preview_hash"], reason="Keep my local originals",
                authorize_permanent_staging_deletion=True, local_originals_confirmed=True)
            result = await api.approve(sid, body, principal, db)
            self.assertEqual(result["id"], "confirmed"); self.assertEqual(row.status, "cleaning")
            self.assertEqual(db.add.call_count, 2); schedule.assert_awaited_once()
            self.assertEqual(schedule.call_args.args[2], "cleanup_ingestion")
            existing = db.add.call_args_list[-1].args[0]
            db.get = AsyncMock(return_value=existing); db.scalar = AsyncMock(return_value=tid); db.add.reset_mock()
            await api.approve(sid, body, principal, db)
            db.add.assert_not_called(); self.assertEqual(schedule.await_count, 1)
            with self.assertRaises(HTTPException):
                await api.approve(sid, body.model_copy(update={"reason": "different"}), principal, db)
            external.assert_not_called(); db.commit.assert_not_called()

    async def test_stale_comparison_does_not_close_session_or_enqueue_deletion(self):
        tid, sid = uuid.uuid4(), uuid.uuid4()
        principal = SimpleNamespace(tenant_id=tid, user_id=uuid.uuid4())
        row = SimpleNamespace(id=sid, title="Test", status="uploading", updated_at=datetime.now(timezone.utc), job_id=None,
            reserved_bytes=100, received_bytes=5)
        db = MagicMock(scalar=AsyncMock(side_effect=[tid, 1, 0]), get=AsyncMock(return_value=None))
        body = api.CleanupApproval(preview_hash="0" * 64, reason="Test", authorize_permanent_staging_deletion=True, local_originals_confirmed=True)
        with patch.object(api, "owned", AsyncMock(return_value=row)), patch.object(api, "schedule", AsyncMock()) as schedule:
            with self.assertRaises(HTTPException) as error: await api.approve(sid, body, principal, db)
        self.assertEqual(error.exception.status_code, 409); self.assertEqual(row.status, "uploading")
        db.add.assert_not_called(); schedule.assert_not_awaited()

    async def test_closed_session_rejects_replay_before_body_or_storage(self):
        row = SimpleNamespace(status="cleaning")
        db = MagicMock()
        with patch.object(ingestion, "owned", AsyncMock(return_value=row)), patch.object(ingestion.storage, "put_object", AsyncMock()) as put:
            with self.assertRaises(HTTPException): await ingestion.put_chunk(db, uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), 0, "a" * 64, None)
            with self.assertRaises(HTTPException): await ingestion.add_manifest(db, uuid.uuid4(), uuid.uuid4(), [])
        put.assert_not_awaited(); db.scalar.assert_not_called()

    async def test_quota_is_released_only_after_completed_cleanup(self):
        db = MagicMock(scalar=AsyncMock(return_value=123))
        self.assertEqual(await api.admission_bytes(db, uuid.uuid4()), 123)
        sql = str(db.scalar.call_args.args[0].compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        self.assertIn("CASE WHEN (ingest_cleanups.phase = 'completed')", sql)
        self.assertIn("ELSE ingest_sessions.reserved_bytes", sql)

    async def test_preview_retains_charge_for_any_processing_attempt_or_missing_old_job(self):
        tid, sid = uuid.uuid4(), uuid.uuid4()
        row = SimpleNamespace(id=sid, title="Test", status="uploading", updated_at=datetime.now(timezone.utc), job_id=None,
            reserved_bytes=100, received_bytes=5)
        for state, has_job, kept in (("uploading", False, 0), ("completed", False, 100), ("partial", True, 100)):
            row.status = state; row.job_id = uuid.uuid4() if has_job else None
            db = MagicMock(scalar=AsyncMock(side_effect=[1, 0]), get=AsyncMock(return_value=SimpleNamespace(status="error")))
            with self.subTest(state=state), patch.object(api, "owned", AsyncMock(return_value=row)):
                preview, _ = await api.preview_data(db, tid, sid)
            self.assertEqual(preview["retained_reservation_bytes"], kept)
            self.assertEqual(preview["released_reservation_bytes"], 100 - kept)
            db.add.assert_not_called(); db.commit.assert_not_called()

    async def test_live_worker_lock_prevents_cleanup_even_if_job_looks_dead(self):
        db = MagicMock(scalar=AsyncMock(return_value=False))
        with self.assertRaises(HTTPException) as error: await api.idle_job(db, uuid.uuid4(), uuid.uuid4())
        self.assertEqual(error.exception.status_code, 409)
        self.assertIn("pg_try_advisory_xact_lock", str(db.scalar.call_args.args[0]))

    async def test_inventory_and_reference_detachment_commit_before_exact_deletion(self):
        tid, sid, _, key = identity(); log = []
        row = SimpleNamespace(id=uuid.uuid4(), phase="inventory", tenant_id=tid, session_id=sid, inventory_cursor={}, inventory_epoch=1,
            bucket="private", job_id=uuid.uuid4(), mode="discard", retained_reservation_bytes=0)
        item = SimpleNamespace(storage_key=key, version_id="null", byte_size=4, is_delete_marker=False, deleted_at=None)
        db = MagicMock(scalar=AsyncMock(side_effect=[tid, SimpleNamespace(id=sid, status="cleaning")]),
            scalars=AsyncMock(side_effect=[SimpleNamespace(all=lambda: [item]), SimpleNamespace(all=lambda: [])]), execute=AsyncMock(return_value=SimpleNamespace(one=lambda: (1, 4))))
        client = MagicMock(list_object_versions=MagicMock(side_effect=[listing("private", tid, sid, versions=[{"Key": key, "VersionId": "null", "Size": 4}]), listing("private", tid, sid)]))
        async def checkpoint(*args): log.append("commit:" + row.phase)
        def deleting(**kwargs):
            self.assertIn("commit:delete", log)
            self.assertTrue(any("UPDATE ingest_files" in str(call.args[0]) for call in db.execute.call_args_list))
            log.append("delete")
        client.delete_object.side_effect = deleting
        async def reserved_delete(client, bucket, key, size, kind, transport, **kwargs):
            self.assertEqual(kind, "delete_version"); self.assertEqual(kwargs["target_version_id"], "null")
            return await transport(None)
        with patch.object(task, "running", AsyncMock()), patch.object(task, "checkpoint", checkpoint), \
             patch("app.modules.storage_audit.quota.known_deletion", AsyncMock(return_value=False)), \
             patch("app.core.storage_transfers.execute", reserved_delete), \
             patch.object(task, "update_active_job", AsyncMock()), patch.object(task, "commit_keep_rls", AsyncMock()):
            await task.process(db, row, client)
        self.assertEqual(row.phase, "completed"); self.assertIsNotNone(item.deleted_at)
        client.delete_object.assert_called_once_with(Bucket="private", Key=key, VersionId="null")

    async def test_uninventoried_version_does_not_expand_deletion_or_release_quota(self):
        tid, sid, _, key = identity()
        row = SimpleNamespace(phase="verify_empty", bucket="private", tenant_id=tid, session_id=sid)
        client = MagicMock(list_object_versions=MagicMock(return_value=listing("private", tid, sid, versions=[{"Key": key, "VersionId": "late", "Size": 4}])))
        db = MagicMock()
        with patch.object(task, "running", AsyncMock()), self.assertRaises(ValueError): await task.process(db, row, client)
        client.delete_object.assert_not_called(); db.scalar.assert_not_called()
        self.assertEqual(row.phase, "verify_empty")

    async def test_restore_starts_new_inventory_epoch_without_erasing_old_receipts(self):
        connection = MagicMock(execute=AsyncMock(return_value="UPDATE 1"))
        connection.transaction.return_value = MagicMock(__aenter__=AsyncMock(), __aexit__=AsyncMock(return_value=False))
        await recovery.quarantine(connection, {"private": "new-private", "public": "new-public"},
            {"database": {"revisions": ["0046"]}, "buckets": {"private": "old-private", "public": "old-public"}})
        statements = [call.args[0] for call in connection.execute.call_args_list]
        self.assertTrue(any("inventory_epoch=inventory_epoch+1" in sql and "verified_after=NULL" in sql for sql in statements))
        self.assertFalse(any("DELETE FROM ingest_cleanup_objects" in sql for sql in statements))


if __name__ == "__main__": unittest.main()
