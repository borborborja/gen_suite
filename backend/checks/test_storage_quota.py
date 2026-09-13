"""Storage admission/fencing contracts, entirely synthetic; no live DB, S3 or paid AI."""
import asyncio
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
from app.core import storage, storage_transfers as transfer
from app.models.ai_call import AiCall
from app.modules.providers import admission
from app.modules.storage_audit import inventory, quota, service as inventory_service
from app.modules.jobs import service as job_service
from app.modules.storage_audit.quota_router import RecountRequest
from app.tasks import storage_quota_tasks as task
from app.recovery import database as recovery
from test_ai_admission import credential


def ledger(**changes):
    return SimpleNamespace(**{**dict(tenant_id=uuid.uuid4(), phase="ready", used_bytes=10, reserved_bytes=3,
        location_hash=inventory.configuration()[1], baseline_at=datetime.now(timezone.utc), scan_id=None), **changes})


def write_fixture(**changes):
    tid = uuid.uuid4()
    return SimpleNamespace(**{**dict(id=uuid.uuid4(), tenant_id=tid, job_id=None, kind="put", state="sending",
        bucket=inventory.configuration()[0][0], storage_key=f"{tid}/result", location_hash=inventory.configuration()[1],
        reserved_bytes=20, actual_bytes=None, target_version_id=None, version_id=None, upload_id=None), **changes})


def manager(db):
    return MagicMock(__aenter__=AsyncMock(return_value=db), __aexit__=AsyncMock(return_value=False))


class QuotaAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_counts_both_used_and_outstanding_and_does_not_guess_overwrite_credit(self):
        row = ledger(); bucket = inventory.configuration()[0][0]; key = f"{row.tenant_id}/file"
        for size, admitted in ((7, True), (8, False)):
            row.reserved_bytes = 3
            db = MagicMock(scalar=AsyncMock(return_value=None), flush=AsyncMock())
            with self.subTest(size=size), patch.object(quota, "account", AsyncMock(return_value=row)), patch.object(quota.settings, "storage_tenant_quota_bytes", 20):
                if admitted:
                    receipt = await quota.reserve(db, row.tenant_id, bucket, key, size)
                    self.assertEqual(receipt.reserved_bytes, 7); self.assertEqual(row.reserved_bytes, 10)
                    db.commit.assert_not_called()
                else:
                    with self.assertRaises(HTTPException) as error: await quota.reserve(db, row.tenant_id, bucket, key, size)
                    self.assertEqual(error.exception.status_code, 413); db.add.assert_not_called()

    async def test_recount_fence_blocks_new_writes_including_zero_byte_deletions(self):
        row = ledger(); db = MagicMock(); bucket = inventory.configuration()[0][0]
        for phase in ("draining", "counting"):
            row.phase = phase
            with self.subTest(phase=phase), patch.object(quota, "account", AsyncMock(return_value=row)), self.assertRaises(HTTPException):
                await quota.reserve(db, row.tenant_id, bucket, f"{row.tenant_id}/a", 0, kind="delete")
        db.add.assert_not_called()

    async def test_empty_bootstrap_reads_four_bounded_lists_and_never_assigns_old_data_zero(self):
        db = MagicMock(); row = ledger(phase="uninitialized", used_bytes=0, reserved_bytes=0, location_hash=None)
        with patch.object(inventory, "versions_page", return_value=([], {})) as versions, \
             patch.object(inventory, "uploads_page", return_value=([], {})) as uploads, patch.object(storage, "_s3", return_value=MagicMock()):
            await quota.initialize_empty(db, row)
            self.assertEqual(row.phase, "ready"); self.assertEqual(versions.call_count, 2); self.assertEqual(uploads.call_count, 2)
        row.phase = "uninitialized"; row.used_bytes = 77
        with patch.object(inventory, "versions_page", return_value=([{"byte_size": 77}], {})), patch.object(storage, "_s3", return_value=MagicMock()), self.assertRaises(HTTPException):
            await quota.initialize_empty(db, row)
        self.assertEqual(row.phase, "uninitialized"); self.assertEqual(row.used_bytes, 77)

    async def test_duplicate_uncertain_key_cannot_start_another_upload(self):
        row = ledger(); db = MagicMock(scalar=AsyncMock(return_value=uuid.uuid4()))
        with patch.object(quota, "account", AsyncMock(return_value=row)), self.assertRaises(HTTPException):
            await quota.reserve(db, row.tenant_id, inventory.configuration()[0][0], f"{row.tenant_id}/a", 1)
        db.add.assert_not_called(); self.assertEqual(row.reserved_bytes, 3)

    async def test_prepaid_response_can_finish_draining_but_not_start_after_counting_or_restore(self):
        write = write_fixture(state="reserved"); row = ledger(tenant_id=write.tenant_id, phase="draining")
        db = MagicMock(scalar=AsyncMock(return_value=write), commit=AsyncMock())
        with patch.object(quota, "SessionLocal", return_value=manager(db)), patch.object(quota, "set_rls_context", AsyncMock()), patch.object(quota, "account", AsyncMock(return_value=row)):
            await quota.begin(write.bucket, write.storage_key, 4, write.id); self.assertEqual(write.state, "sending")
            for phase, state in (("counting", "reserved"), ("ready", "quarantined"), ("ready", "unknown")):
                row.phase = phase; write.state = state
                with self.subTest(phase=phase, state=state), self.assertRaises(HTTPException):
                    await quota.begin(write.bucket, write.storage_key, 4, write.id)

    async def test_settlement_is_once_and_only_credits_actual_new_payload(self):
        write = write_fixture(reserved_bytes=20); row = ledger(tenant_id=write.tenant_id, used_bytes=50, reserved_bytes=20)
        db = MagicMock(scalar=AsyncMock(return_value=write), commit=AsyncMock())
        with patch.object(quota, "SessionLocal", return_value=manager(db)), patch.object(quota, "set_rls_context", AsyncMock()), patch.object(quota, "account", AsyncMock(return_value=row)):
            await quota.finish(write, 4, "version"); await quota.finish(write, 4, "version")
        self.assertEqual(row.used_bytes, 54); self.assertEqual(row.reserved_bytes, 0); self.assertEqual(write.state, "succeeded")
        self.assertEqual(db.commit.await_count, 1)

    async def test_only_never_sent_storage_reservation_can_be_cancelled(self):
        for state, released in (("reserved", True), ("sending", False), ("unknown", False), ("succeeded", False)):
            write = write_fixture(state=state); row = ledger(reserved_bytes=20)
            db = MagicMock(scalar=AsyncMock(return_value=write))
            with self.subTest(state=state), patch.object(quota, "account", AsyncMock(return_value=row)):
                self.assertEqual(await quota.cancel_unused(db, write.tenant_id, write.id), released)
            self.assertEqual(row.reserved_bytes, 0 if released else 20)

    async def test_storage_and_money_reservations_are_atomic_before_paid_transport(self):
        rc = credential(); db = MagicMock(scalar=AsyncMock(side_effect=[SimpleNamespace(monthly_budget_cents=1000), None]), commit=AsyncMock())
        ticket = SimpleNamespace(id=uuid.uuid4())
        with patch.object(admission, "SessionLocal", return_value=manager(db)), patch.object(admission, "set_rls_context", AsyncMock()), \
             patch.object(admission, "totals", AsyncMock(return_value=dict(unknown_calls=0, settled_microusd=0, reserved_microusd=0))), \
             patch.object(quota, "reserve", AsyncMock(return_value=ticket)) as reserve:
            call, fresh = await admission._reserve(rc, admission.snapshot(rc), "a" * 64, "b" * 64, 1)
        self.assertTrue(fresh); self.assertEqual(call.response_write_id, ticket.id)
        self.assertIs(reserve.call_args.args[0], db); self.assertEqual(reserve.call_args.args[4], admission.MAX_RESULT_BYTES)
        self.assertIsInstance(db.add.call_args.args[0], AiCall); db.commit.assert_awaited_once()

    async def test_no_paid_dispatch_when_response_storage_reservation_missing(self):
        rc = credential(); call = SimpleNamespace(id=uuid.uuid4(), response_write_id=None)
        transport = MagicMock()
        with patch.object(admission, "_reserve", AsyncMock(return_value=(call, True))), self.assertRaises(HTTPException):
            await admission.execute(rc, kind="test", request="text", transport=transport)
        transport.assert_not_called()


class TransferTests(unittest.IsolatedAsyncioTestCase):
    async def test_put_orders_durable_reserve_send_receipt_and_settlement(self):
        write = write_fixture(reserved_bytes=3, state="reserved"); log = []
        async def reserve(*args, **kwargs): log.append("reserve"); return write
        async def begin(*args): log.append("sending"); write.state = "sending"; return write
        def put(**kwargs):
            log.append("put"); self.assertEqual(kwargs["Metadata"]["gensuite-write-id"], str(write.id)); return {"VersionId": "v"}
        def head(**kwargs): log.append("head"); return {"VersionId": "v", "ContentLength": 3, "Metadata": {"gensuite-write-id": str(write.id)}}
        async def finish(*args): log.append("finish")
        client = MagicMock(put_object=MagicMock(side_effect=put), head_object=MagicMock(side_effect=head))
        with patch.object(quota, "reserve_independent", reserve), patch.object(quota, "begin", begin), patch.object(quota, "finish", finish):
            await transfer.put(client, write.bucket, write.storage_key, b"abc", "text/plain")
        self.assertEqual(log, ["reserve", "sending", "put", "head", "finish"])

    async def test_failed_put_or_receipt_retains_reservation_without_retry(self):
        write = write_fixture()
        for failure in ("put", "head"):
            client = MagicMock(put_object=MagicMock(side_effect=TimeoutError() if failure == "put" else None),
                head_object=MagicMock(side_effect=TimeoutError()))
            client.put_object.return_value = {}
            with self.subTest(failure=failure), patch.object(quota, "reserve_independent", AsyncMock(return_value=write)), patch.object(quota, "begin", AsyncMock(return_value=write)), \
                 patch.object(quota, "unknown", AsyncMock()) as unknown, patch.object(quota, "finish", AsyncMock()) as finish:
                with self.assertRaises(TimeoutError): await transfer.put(client, write.bucket, write.storage_key, b"a", "text/plain")
            self.assertEqual(client.put_object.call_count, 1); unknown.assert_awaited_once(); finish.assert_not_awaited()

    async def test_quota_rejection_precedes_any_s3_write(self):
        write = write_fixture(); client = MagicMock()
        with patch.object(quota, "reserve_independent", AsyncMock(side_effect=HTTPException(413, "full"))), self.assertRaises(HTTPException):
            await transfer.put(client, write.bucket, write.storage_key, b"a", "text/plain")
        client.put_object.assert_not_called(); client.head_object.assert_not_called()

    async def test_multipart_uses_bounded_parts_and_records_upload_before_sending(self):
        write = write_fixture(); write.kind = "upload"; log = []
        client = MagicMock(create_multipart_upload=MagicMock(return_value={"UploadId": "u"}), complete_multipart_upload=MagicMock(return_value={"VersionId": "v"}))
        async def record(write, uid): log.append("record"); write.upload_id = uid
        async def part(write, offset, length, number):
            self.assertEqual(write.upload_id, "u"); self.assertLessEqual(length, transfer.CHUNK); log.append((offset, length, number)); return {"ETag": f"p{number}"}
        with patch.object(quota, "record_upload", record):
            await transfer.multipart(client, write, transfer.CHUNK + 9, "application/pdf", part)
        self.assertEqual(log, ["record", (0, transfer.CHUNK, 1), (transfer.CHUNK, 9, 2)])
        self.assertEqual(len(client.complete_multipart_upload.call_args.kwargs["MultipartUpload"]["Parts"]), 2)
        client.abort_multipart_upload.assert_not_called()

    async def test_copy_pins_source_version_and_etag_before_reserving_destination(self):
        write = write_fixture(kind="copy", reserved_bytes=3)
        client = MagicMock(head_object=MagicMock(return_value={"ContentLength": 3, "ETag": "etag", "VersionId": "source-v", "Metadata": {"old": "kept"}}),
            get_object=MagicMock(return_value={"ContentLength": 3, "Body": io.BytesIO(b"abc")}),
            put_object=MagicMock(return_value={"VersionId": "dst-v"}))
        async def execute(client, bucket, key, size, kind, send):
            self.assertEqual(size, 3); return await send(write)
        with patch.object(transfer, "execute", execute):
            await transfer.copy(client, inventory.configuration()[0][0], inventory.configuration()[0][1], write.storage_key)
        options = client.get_object.call_args.kwargs
        self.assertEqual(options["VersionId"], "source-v"); self.assertEqual(options["IfMatch"], "etag")
        self.assertEqual(options["Range"], "bytes=0-2")
        metadata = client.put_object.call_args.kwargs["Metadata"]
        self.assertEqual(metadata["old"], "kept"); self.assertEqual(metadata["gensuite-write-id"], str(write.id))
        self.assertEqual(client.put_object.call_args.kwargs["Body"], b"abc")
        self.assertTrue(client.get_object.return_value["Body"].closed)

    async def test_copy_rejects_changed_small_source_before_admission_and_closes_body(self):
        write = write_fixture(kind="copy")
        for size, data in ((4, b"abc"), (3, b"ab"), (3, b"abcd")):
            body = io.BytesIO(data)
            client = MagicMock(head_object=MagicMock(return_value={"ContentLength": 3, "ETag": "etag"}),
                get_object=MagicMock(return_value={"ContentLength": size, "Body": body}))
            with self.subTest(size=size, data=data), patch.object(transfer, "execute", AsyncMock()) as execute:
                with self.assertRaises(ValueError):
                    await transfer.copy(client, inventory.configuration()[0][0], inventory.configuration()[0][1], write.storage_key)
                execute.assert_not_awaited(); client.put_object.assert_not_called(); self.assertTrue(body.closed)

    async def test_large_copy_limits_all_ranges_and_records_upload_before_parts(self):
        size = transfer.CHUNK + 9; write = write_fixture(kind="copy", reserved_bytes=size)
        client = MagicMock(head_object=MagicMock(return_value={"ContentLength": size, "ETag": "etag", "VersionId": "source-v"}),
            create_multipart_upload=MagicMock(return_value={"UploadId": "u"}),
            upload_part_copy=MagicMock(return_value={"CopyPartResult": {"ETag": "part"}}),
            complete_multipart_upload=MagicMock(return_value={"VersionId": "destination-v"}))
        async def execute(client, bucket, key, amount, kind, send):
            self.assertEqual(amount, size); return await send(write)
        async def record(write, upload_id):
            client.upload_part_copy.assert_not_called(); write.upload_id = upload_id
        with patch.object(transfer, "execute", execute), patch.object(quota, "record_upload", record):
            await transfer.copy(client, inventory.configuration()[0][0], write.bucket, write.storage_key)
        ranges = [call.kwargs["CopySourceRange"] for call in client.upload_part_copy.call_args_list]
        self.assertEqual(ranges, [f"bytes=0-{transfer.CHUNK - 1}", f"bytes={transfer.CHUNK}-{size - 1}"])
        for call in client.upload_part_copy.call_args_list:
            self.assertEqual(call.kwargs["CopySource"]["VersionId"], "source-v")
            self.assertEqual(call.kwargs["CopySourceIfMatch"], "etag"); self.assertEqual(call.kwargs["UploadId"], "u")
        client.get_object.assert_not_called(); client.put_object.assert_not_called()

    async def test_prefix_rejects_entire_malformed_page_before_any_mutation(self):
        write = write_fixture(); prefix = f"{write.tenant_id}/"; good = {"Key": prefix + "a"}
        for objects in (None, {}, [good] * 101, [good, {"Key": "outside/a"}], [good, good], [{"Key": 1}]):
            client = MagicMock(list_objects_v2=MagicMock(return_value={"Name": write.bucket, "Prefix": prefix,
                "IsTruncated": False, "Contents": objects}))
            with self.subTest(objects_type=type(objects).__name__), patch.object(storage, "_s3", return_value=client), \
                 patch.object(storage, "delete_object", AsyncMock()) as delete:
                with self.assertRaises(ValueError): await storage.delete_prefix(write.bucket, prefix)
                delete.assert_not_awaited()

    async def test_prefix_cycle_cannot_repeat_copy_even_if_tokens_keep_changing(self):
        write = write_fixture(); prefix = f"{write.tenant_id}/"
        pages = [{"Name": write.bucket, "Prefix": prefix, "IsTruncated": True, "NextContinuationToken": str(i),
            "Contents": [{"Key": prefix + suffix}]} for i, suffix in enumerate(("a", "b", "a"))]
        client = MagicMock(list_objects_v2=MagicMock(side_effect=pages))
        with patch.object(storage, "_s3", return_value=client), patch.object(storage, "copy_object", AsyncMock()) as copy:
            with self.assertRaises(ValueError): await storage.copy_prefix(write.bucket, inventory.configuration()[0][1], prefix)
        self.assertEqual([call.args[2] for call in copy.call_args_list], [prefix + "a", prefix + "b"])
        self.assertEqual(client.list_objects_v2.call_count, 3)

    async def test_absent_put_cannot_release_space_but_exact_deleted_version_can(self):
        client = MagicMock(head_object=MagicMock(side_effect=ClientError({"Error": {"Code": "404"}}, "HeadObject")))
        write = write_fixture(state="unknown")
        with patch.object(storage, "_s3", return_value=client), patch.object(quota, "finish", AsyncMock()) as finish:
            with self.assertRaises(ClientError): await quota.recheck(write)
            finish.assert_not_awaited()
            write.kind = "delete_version"; write.target_version_id = "old-v"
            await quota.recheck(write); finish.assert_awaited_once_with(write, 0)

    async def test_recheck_does_not_accept_a_different_write_identity(self):
        write = write_fixture(state="unknown")
        client = MagicMock(head_object=MagicMock(return_value={"ContentLength": 2, "Metadata": {"gensuite-write-id": "other"}}))
        with patch.object(storage, "_s3", return_value=client), patch.object(quota, "finish", AsyncMock()) as finish, self.assertRaises(ValueError):
            await quota.recheck(write)
        finish.assert_not_awaited(); client.put_object.assert_not_called()


class RecountTests(unittest.IsolatedAsyncioTestCase):
    async def test_observational_control_lookup_excludes_protected_recounts(self):
        db = MagicMock(scalar=AsyncMock(return_value=None))
        with self.assertRaises(HTTPException): await inventory_service.owned(db, uuid.uuid4(), uuid.uuid4())
        sql = str(db.scalar.call_args.args[0].compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        self.assertIn("jobs.type = 'inventory_storage'", sql)

    async def test_generic_job_cancellation_cannot_leave_a_hidden_quota_fence(self):
        db = MagicMock(); job = SimpleNamespace(type="recount_storage", status="queued")
        with patch.object(job_service, "get_job", AsyncMock(return_value=job)), self.assertRaises(HTTPException) as error:
            await job_service.cancel_job(db, uuid.uuid4())
        self.assertEqual(error.exception.status_code, 409); self.assertEqual(job.status, "queued")
        db.flush.assert_not_called()

    def test_confirmation_requires_two_strict_true_values(self):
        body = dict(client_id=uuid.uuid4(), reason="Recuento", authorize_write_pause=True, external_writers_stopped=True)
        RecountRequest(**body)
        for change in ({"authorize_write_pause": 1}, {"external_writers_stopped": "true"}, {"reason": " "}, {"prefix": "/"}):
            with self.subTest(change=change), self.assertRaises(ValidationError): RecountRequest(**{**body, **change})

    async def test_fenced_baseline_only_activates_current_scan_with_no_pending_or_uninspected(self):
        row = ledger(phase="counting", reserved_bytes=0); scan_id = uuid.uuid4(); row.scan_id = scan_id
        scan = SimpleNamespace(id=scan_id, tenant_id=row.tenant_id, job_id=uuid.uuid4(), location_hash=row.location_hash)
        for pending, unavailable in ((1, 0), (0, 1), (0, 0)):
            db = MagicMock(scalar=AsyncMock(side_effect=[pending, unavailable, 50]))
            row.phase = "counting"
            with self.subTest(pending=pending, unavailable=unavailable), patch.object(quota, "account", AsyncMock(return_value=row)), \
                 patch.object(inventory, "matches_configuration", return_value=True), patch.object(task, "update_active_job", AsyncMock(return_value=True)), patch.object(task, "commit_keep_rls", AsyncMock()) as commit:
                if pending or unavailable:
                    with self.assertRaises(ValueError): await task.finalize(db, scan)
                    commit.assert_not_awaited()
                else:
                    await task.finalize(db, scan); self.assertEqual(row.used_bytes, 50); self.assertEqual(row.phase, "ready")

    async def test_cancelled_or_other_observational_scan_never_activates_baseline(self):
        row = ledger(phase="ready", scan_id=uuid.uuid4()); scan = SimpleNamespace(tenant_id=row.tenant_id, id=uuid.uuid4())
        db = MagicMock()
        with patch.object(quota, "account", AsyncMock(return_value=row)), self.assertRaises(task.InventoryStopped): await task.finalize(db, scan)
        db.scalar.assert_not_called()

    async def test_restore_quarantines_source_receipts_and_requires_new_baseline(self):
        connection = MagicMock(execute=AsyncMock(return_value="UPDATE 1"))
        connection.transaction.return_value = manager(connection)
        await recovery.quarantine(connection, {"private": "new-p", "public": "new-u"}, {"database": {"revisions": ["0048"]}, "buckets": {"private": "old-p", "public": "old-u"}})
        sql = [call.args[0] for call in connection.execute.call_args_list]
        self.assertTrue(any("UPDATE storage_writes SET state='quarantined'" in v for v in sql))
        self.assertTrue(any("UPDATE storage_quotas SET phase='uninitialized'" in v for v in sql))
        self.assertFalse(any("DELETE FROM storage_writes" in v for v in sql))


if __name__ == "__main__": unittest.main()
