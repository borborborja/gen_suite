"""No live services or paid calls: unused-reservation review/guard contracts."""
import asyncio
import importlib.util
import unittest
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException
from pydantic import ValidationError
from app.models.storage_quota import StorageWriteAction
from app.modules.storage_audit import quota, write_actions as api
from app.modules.providers import admission, response_guard as guard
from app.recovery import database as recovery
from test_storage_quota import manager, write_fixture, ledger
from test_ai_admission import credential


def ticket(**changes):
    write = write_fixture(state="reserved")
    write.updated_at = datetime.now(timezone.utc); write.ai_guard_version = None
    for key, value in changes.items(): setattr(write, key, value)
    return write


def paid_call(write, **changes):
    return SimpleNamespace(**{**dict(id=uuid.uuid4(), tenant_id=write.tenant_id, response_write_id=write.id,
        state="reconciled", result_key=None, updated_at=datetime.now(timezone.utc)), **changes})


class PreviewTests(unittest.IsolatedAsyncioTestCase):
    def test_audit_migration_revokes_inherited_defaults_and_adds_restrictive_policies(self):
        path = Path(__file__).resolve().parents[1] / "alembic/versions/0050_storage_action_immutability.py"
        spec = importlib.util.spec_from_file_location("audit_acl_migration", path)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        with patch.object(module.op, "execute") as execute: module.upgrade()
        statements = [call.args[0] for call in execute.call_args_list]
        self.assertTrue(any(value.startswith("REVOKE UPDATE, DELETE, TRUNCATE ON storage_write_actions FROM ") and "PUBLIC" in value for value in statements))
        self.assertTrue(any("AS RESTRICTIVE FOR UPDATE USING (false) WITH CHECK (false)" in value for value in statements))
        self.assertTrue(any("AS RESTRICTIVE FOR DELETE USING (false)" in value for value in statements))

    async def test_preview_is_readonly_and_exposes_no_private_storage_identifiers(self):
        write = ticket(); db = MagicMock(scalar=AsyncMock(return_value=None))
        value = await api.preview_data(db, write.tenant_id, write, "Cancelar carga abandonada")
        self.assertEqual(value["reserved_bytes"], write.reserved_bytes); self.assertFalse(value["storage_changed"])
        self.assertFalse(value["requires_late_response_loss_ack"])
        for name in ("storage_key", "bucket", "location_hash", "upload_id"): self.assertNotIn(name, value)
        db.commit.assert_not_called(); db.add.assert_not_called()

    async def test_sent_unknown_restored_or_partially_persisted_receipts_are_not_unused(self):
        for change in ({"state": "sending"}, {"state": "unknown"}, {"state": "quarantined"},
                       {"upload_id": "u"}, {"actual_bytes": 0}, {"version_id": "v"}, {"location_hash": "0" * 64}):
            write = ticket(**change); db = MagicMock(scalar=AsyncMock(return_value=None))
            with self.subTest(change=change), self.assertRaises(HTTPException):
                await api.preview_data(db, write.tenant_id, write, "Motivo comprobable")
            db.add.assert_not_called()

    async def test_ai_requires_new_guard_reconciliation_and_no_saved_result(self):
        write = ticket(ai_guard_version=1); db = MagicMock(scalar=AsyncMock())
        for version, state, result, allowed in ((None, "reconciled", None, False), (1, "inflight", None, False),
                (1, "unknown", None, False), (1, "succeeded", "stored", False), (1, "reconciled", "stored", False), (1, "reconciled", None, True)):
            write.ai_guard_version = version; db.scalar.return_value = paid_call(write, state=state, result_key=result)
            with self.subTest(version=version, state=state, result=result):
                if allowed:
                    value = await api.preview_data(db, write.tenant_id, write, "Cierre de llamada conciliada")
                    self.assertTrue(value["requires_late_response_loss_ack"])
                else:
                    with self.assertRaises(HTTPException): await api.preview_data(db, write.tenant_id, write, "Cierre de llamada conciliada")

    def test_strict_approval_and_no_target_overrides(self):
        body = dict(reason="Carga abandonada", client_id=uuid.uuid4(), preview_hash="a" * 64, authorize=True)
        api.Approval(**body)
        for change in ({"authorize": 1}, {"authorize": "true"}, {"reason": " "}, {"preview_hash": "invalid"},
                       {"bucket": "private"}, {"acknowledge_late_response_loss": "true"}):
            with self.subTest(change=change), self.assertRaises(ValidationError): api.Approval(**{**body, **change})


class ActionTests(unittest.IsolatedAsyncioTestCase):
    async def prepare(self, ai=False):
        write = ticket(ai_guard_version=1 if ai else None); account = ledger(tenant_id=write.tenant_id, reserved_bytes=write.reserved_bytes)
        db = MagicMock(scalar=AsyncMock(return_value=paid_call(write) if ai else None), get=AsyncMock(return_value=None), flush=AsyncMock())
        principal = SimpleNamespace(tenant_id=write.tenant_id, user_id=uuid.uuid4())
        value = await api.preview_data(db, write.tenant_id, write, "Cancelar carga abandonada")
        body = api.Approval(reason=value["reason"], client_id=uuid.uuid4(), preview_hash=value["preview_hash"], authorize=True,
            acknowledge_late_response_loss=ai)
        return write, account, db, principal, value, body

    async def test_confirmation_records_release_in_same_transaction_without_s3_or_job_mutation(self):
        write, account, db, principal, value, body = await self.prepare()
        async def cancel(db, tid, wid):
            account.reserved_bytes -= write.reserved_bytes; write.state = "cancelled"; return True
        with patch.object(quota, "account", AsyncMock(return_value=account)), patch.object(api, "owned", AsyncMock(return_value=write)), \
             patch.object(quota, "cancel_unused", cancel):
            result = await api.cancel(write.id, body, principal, db)
        self.assertEqual(account.reserved_bytes, 0); self.assertEqual(account.used_bytes, 10)
        action = db.add.call_args.args[0]; self.assertIsInstance(action, StorageWriteAction)
        self.assertEqual(action.created_by, principal.user_id); self.assertEqual(result["released_bytes"], 20)
        self.assertFalse(action.evidence["job_cancelled"]); db.commit.assert_not_called()

    async def test_changed_preview_or_counting_phase_never_release(self):
        for changed in ("preview", "phase"):
            write, account, db, principal, value, body = await self.prepare()
            if changed == "preview": write.reserved_bytes += 1
            else: account.phase = "counting"
            with self.subTest(changed=changed), patch.object(quota, "account", AsyncMock(return_value=account)), \
                 patch.object(api, "owned", AsyncMock(return_value=write)), patch.object(quota, "cancel_unused", AsyncMock()) as cancel:
                with self.assertRaises(HTTPException): await api.cancel(write.id, body, principal, db)
                cancel.assert_not_awaited(); db.add.assert_not_called()

    async def test_ai_cancellation_needs_idle_guard_and_separate_loss_ack(self):
        for mode in ("busy", "no_ack", "okay"):
            write, account, db, principal, value, body = await self.prepare(ai=True)
            if mode == "no_ack": body.acknowledge_late_response_loss = False
            with self.subTest(mode=mode), patch.object(quota, "account", AsyncMock(return_value=account)), \
                 patch.object(api, "owned", AsyncMock(return_value=write)), \
                 patch.object(api, "preview_data", AsyncMock(return_value=value)), \
                 patch.object(api, "review_lock", AsyncMock(side_effect=HTTPException(409, "busy") if mode == "busy" else None)) as lock, \
                 patch.object(quota, "cancel_unused", AsyncMock(return_value=True)) as cancel:
                if mode == "okay": await api.cancel(write.id, body, principal, db); cancel.assert_awaited_once()
                else:
                    with self.assertRaises(HTTPException): await api.cancel(write.id, body, principal, db)
                    cancel.assert_not_awaited(); db.add.assert_not_called()
                lock.assert_awaited_once_with(db, write.id)

    async def test_repeated_request_returns_same_audit_without_releasing_twice(self):
        write, account, db, principal, value, body = await self.prepare()
        digest = api.inventory.fingerprint({"write_id": str(write.id), **body.model_dump(mode="json")})
        previous = SimpleNamespace(id=uuid.uuid4(), write_id=write.id, created_by=principal.user_id, created_at=None,
            reason=body.reason, released_bytes=20, evidence=value, request_hash=digest)
        db.get.return_value = previous
        with patch.object(quota, "account", AsyncMock(return_value=account)), patch.object(api, "owned", AsyncMock()) as owned:
            self.assertEqual((await api.cancel(write.id, body, principal, db))["id"], previous.id)
            body.reason = "Un motivo distinto"
            with self.assertRaises(HTTPException): await api.cancel(write.id, body, principal, db)
        owned.assert_not_awaited(); db.add.assert_not_called()


class ResponseGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_acquisition_commit_failure_still_unlocks_before_returning_connection(self):
        connection = MagicMock(scalar=AsyncMock(return_value=True), commit=AsyncMock(side_effect=[OSError("commit failed"), None]),
            rollback=AsyncMock(), execute=AsyncMock(), invalidate=AsyncMock())
        with patch.object(guard, "engine", MagicMock(connect=MagicMock(return_value=manager(connection)))), self.assertRaises(OSError):
            async with guard.hold(uuid.uuid4(), uuid.uuid4(), uuid.uuid4()): self.fail("not admitted")
        self.assertIn("pg_advisory_unlock", str(connection.execute.call_args.args[0])); connection.invalidate.assert_not_awaited()

    async def test_unlock_failure_invalidates_connection_instead_of_pooling_a_lock(self):
        connection = MagicMock(scalar=AsyncMock(return_value=True), commit=AsyncMock(), rollback=AsyncMock(),
            execute=AsyncMock(side_effect=OSError("connection failed")), invalidate=AsyncMock())
        with patch.object(guard, "engine", MagicMock(connect=MagicMock(return_value=manager(connection)))), \
             patch.object(guard, "validate_dispatch", AsyncMock()), self.assertRaises(OSError):
            async with guard.hold(uuid.uuid4(), uuid.uuid4(), uuid.uuid4()): pass
        connection.invalidate.assert_awaited_once()

    async def test_guard_is_held_before_validation_until_result_or_failure_and_always_unlocks(self):
        connection = MagicMock(scalar=AsyncMock(return_value=True), commit=AsyncMock(), rollback=AsyncMock(), execute=AsyncMock())
        engine = MagicMock(connect=MagicMock(return_value=manager(connection)))
        tid, cid, wid = (uuid.uuid4() for _ in range(3))
        for failure in (False, True):
            connection.reset_mock()
            with self.subTest(failure=failure), patch.object(guard, "engine", engine), patch.object(guard, "validate_dispatch", AsyncMock()) as validate:
                try:
                    async with guard.hold(tid, cid, wid):
                        validate.assert_awaited_once_with(tid, cid, wid); connection.execute.assert_not_awaited()
                        if failure: raise ValueError("Synthetic failure")
                except ValueError: pass
                self.assertIn("pg_advisory_unlock", str(connection.execute.call_args.args[0])); connection.rollback.assert_awaited_once()

    async def test_busy_guard_never_validates_or_unlocks_another_owner(self):
        connection = MagicMock(scalar=AsyncMock(return_value=False), commit=AsyncMock(), execute=AsyncMock())
        engine = MagicMock(connect=MagicMock(return_value=manager(connection)))
        with patch.object(guard, "engine", engine), patch.object(guard, "validate_dispatch", AsyncMock()) as validate:
            with self.assertRaises(HTTPException):
                async with guard.hold(uuid.uuid4(), uuid.uuid4(), uuid.uuid4()): self.fail("not admitted")
        validate.assert_not_awaited(); connection.execute.assert_not_awaited()

    async def test_reconciled_cancelled_legacy_or_other_tenant_never_dispatches(self):
        write = ticket(ai_guard_version=1); call = paid_call(write, state="inflight")
        db = MagicMock(get=AsyncMock())
        for mode in ("okay", "reconciled", "cancelled", "legacy", "other"):
            write.state = "reserved"; write.ai_guard_version = 1; call.state = "inflight"; call.tenant_id = write.tenant_id
            if mode == "reconciled": call.state = "reconciled"
            if mode == "cancelled": write.state = "cancelled"
            if mode == "legacy": write.ai_guard_version = None
            if mode == "other": call.tenant_id = uuid.uuid4()
            db.get.side_effect = [call, write]
            with self.subTest(mode=mode), patch.object(guard, "SessionLocal", return_value=manager(db)), patch.object(guard, "set_rls_context", AsyncMock()):
                if mode == "okay": await guard.validate_dispatch(write.tenant_id, call.id, write.id)
                else:
                    with self.assertRaises(HTTPException): await guard.validate_dispatch(write.tenant_id, call.id, write.id)

    async def test_financial_review_uses_the_same_nonblocking_guard(self):
        write = ticket(ai_guard_version=1); call = paid_call(write)
        for available in (False, True):
            db = MagicMock(get=AsyncMock(return_value=write), scalar=AsyncMock(return_value=available))
            with self.subTest(available=available):
                if available: await guard.review_call(db, call)
                else:
                    with self.assertRaises(HTTPException): await guard.review_call(db, call)
                self.assertIn("pg_try_advisory_xact_lock", str(db.scalar.call_args.args[0]))
                self.assertEqual(db.scalar.call_args.args[1]["key"], guard.lock_key(write.id))

    async def test_guard_denial_prevents_transport_even_after_committed_admission(self):
        rc = credential(); call = SimpleNamespace(id=uuid.uuid4(), response_write_id=uuid.uuid4())
        denied = MagicMock(__aenter__=AsyncMock(side_effect=HTTPException(409, "reviewed")), __aexit__=AsyncMock())
        transport = MagicMock()
        with patch.object(admission, "_reserve", AsyncMock(return_value=(call, True))), patch.object(guard, "hold", return_value=denied):
            with self.assertRaises(HTTPException): await admission.execute(rc, kind="test", request="source", transport=transport)
        transport.assert_not_called()

    async def test_restore_preserves_audit_and_quarantines_receipts(self):
        connection = MagicMock(execute=AsyncMock(return_value="UPDATE 1")); connection.transaction.return_value = manager(connection)
        await recovery.quarantine(connection, {"private": "new-p", "public": "new-u"},
            {"database": {"revisions": ["0050"]}, "buckets": {"private": "old-p", "public": "old-u"}})
        sql = [call.args[0] for call in connection.execute.call_args_list]
        self.assertTrue(any("UPDATE storage_writes SET state='quarantined'" in value for value in sql))
        self.assertFalse(any("storage_write_actions" in value for value in sql))


if __name__ == "__main__": unittest.main()
