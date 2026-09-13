"""Requires explicitly authorized disposable PostgreSQL/MinIO. No paid transport."""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from app.core import storage
from app.db.rls import set_rls_context
from app.db.session import SessionLocal
from app.models.ai_call import AiCall
from app.models.storage_quota import StorageWrite, StorageWriteAction
from app.modules.providers import response_guard
from app.modules.storage_audit import quota
from .test_documents import _auth, _tenant_token
from .test_storage_quota import BASE, setup, request_recount, run_scan


async def proposal(client, headers, write):
    response = await client.post(f"{BASE}/writes/{write.id}/cancel-preview", headers=headers, json={"reason": "Carga abandonada comprobada"})
    assert response.status_code == 200, response.text
    return {"reason": response.json()["reason"], "client_id": str(uuid.uuid4()), "preview_hash": response.json()["preview_hash"], "authorize": True}


async def test_cancel_unused_is_idempotent_audited_and_allows_fenced_recount(client, monkeypatch):
    tid, headers, bucket = await setup(client, monkeypatch)
    write = await quota.reserve_independent(bucket, f"{tid}/never-sent", 100)
    body = await proposal(client, headers, write)
    for method in ("put_object", "delete_object", "abort_multipart_upload"):
        monkeypatch.setattr(storage._s3(), method, lambda **kwargs: pytest.fail("Cancellation must not mutate S3"))
    scan = await request_recount(client, headers); await run_scan(tid, scan)
    assert (await client.get(BASE, headers=headers)).json()["phase"] == "draining"
    first = await client.post(f"{BASE}/writes/{write.id}/cancel-unused", headers=headers, json=body)
    assert first.status_code == 200, first.text
    second = await client.post(f"{BASE}/writes/{write.id}/cancel-unused", headers=headers, json=body)
    assert second.status_code == 200 and first.json()["id"] == second.json()["id"]
    assert first.json()["released_bytes"] == 100 and first.json()["evidence"]["storage_changed"] is False
    with pytest.raises(HTTPException): await quota.begin(bucket, write.storage_key, 100, write.id)
    history = (await client.get(f"{BASE}/actions", headers=headers)).json()
    assert len(history) == 1 and history[0]["write_id"] == str(write.id)
    retried = await client.post(f"{BASE}/recount/retry", headers=headers)
    assert retried.status_code == 202, retried.text
    await run_scan(tid, scan)
    state = (await client.get(BASE, headers=headers)).json()
    assert state["phase"] == "ready" and state["reserved_bytes"] == 0 and state["used_bytes"] == 0, state


async def test_sending_after_preview_prevents_release_and_records_no_false_audit(client, monkeypatch):
    tid, headers, bucket = await setup(client, monkeypatch)
    write = await quota.reserve_independent(bucket, f"{tid}/started", 20)
    body = await proposal(client, headers, write)
    await quota.begin(bucket, write.storage_key, 20, write.id)
    denied = await client.post(f"{BASE}/writes/{write.id}/cancel-unused", headers=headers, json=body)
    assert denied.status_code == 409, denied.text
    assert (await client.get(BASE, headers=headers)).json()["reserved_bytes"] == 20
    assert (await client.get(f"{BASE}/actions", headers=headers)).json() == []


async def test_parallel_cancel_and_begin_have_only_one_winner(client, monkeypatch):
    tid, headers, bucket = await setup(client, monkeypatch)
    write = await quota.reserve_independent(bucket, f"{tid}/race", 20); body = await proposal(client, headers, write)
    response, started = await asyncio.wait_for(asyncio.gather(
        client.post(f"{BASE}/writes/{write.id}/cancel-unused", headers=headers, json=body),
        quota.begin(bucket, write.storage_key, 20, write.id), return_exceptions=True), timeout=15)
    async with SessionLocal() as db:
        await set_rls_context(db, tenant_id=tid)
        row = await db.get(StorageWrite, write.id)
        actions = list((await db.scalars(select(StorageWriteAction))).all())
    if response.status_code == 200:
        assert isinstance(started, HTTPException) and row.state == "cancelled" and len(actions) == 1
    else:
        assert response.status_code == 409 and isinstance(started, StorageWrite) and row.state == "sending" and not actions


async def test_ai_guard_blocks_reconciliation_then_requires_explicit_late_result_ack(client, monkeypatch):
    tid, headers, bucket = await setup(client, monkeypatch)
    cid = uuid.uuid4(); key = f"{tid}/ai-calls/{cid}/response.json"
    async with SessionLocal() as db:
        await set_rls_context(db, tenant_id=tid)
        write = await quota.reserve(db, tid, bucket, key, 16 * 1024**2); write.ai_guard_version = 1
        call = AiCall(id=cid, tenant_id=tid, response_write_id=write.id, task_type="inference", operation_key=uuid.uuid4().hex * 2,
            request_hash="a" * 64, profile={}, state="inflight", reserved_microusd=1,
            created_at=datetime.now(timezone.utc) - timedelta(hours=1))
        db.add(call); await db.commit()
    reconciliation = {"cost_microusd": 1, "evidence": "Comprobación sintética del proveedor"}
    async with response_guard.hold(tid, cid, write.id):
        denied = await client.post(f"/api/providers/ai-calls/{cid}/reconcile", headers=headers, json=reconciliation)
        assert denied.status_code == 409 and "ejecución" in denied.json()["detail"], denied.text
    reconciled = await client.post(f"/api/providers/ai-calls/{cid}/reconcile", headers=headers, json=reconciliation)
    assert reconciled.status_code == 200, reconciled.text
    body = await proposal(client, headers, write)
    assert (await client.post(f"{BASE}/writes/{write.id}/cancel-unused", headers=headers, json=body)).status_code == 409
    body["acknowledge_late_response_loss"] = True
    cancelled = await client.post(f"{BASE}/writes/{write.id}/cancel-unused", headers=headers, json=body)
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["evidence"]["late_response_loss_ack"] is True
    with pytest.raises(HTTPException):
        async with response_guard.hold(tid, cid, write.id): pytest.fail("Reconciled call may not dispatch again")


async def test_other_tenant_and_viewer_cannot_cancel_or_read_foreign_audit(client, monkeypatch):
    tid, owner, bucket = await setup(client, monkeypatch)
    write = await quota.reserve_independent(bucket, f"{tid}/private-ticket", 20); body = await proposal(client, owner, write)
    other = _auth(await _tenant_token(client, "other-quota@example.com", "Other quota"))
    for action in ("cancel-preview", "cancel-unused"):
        denied = await client.post(f"{BASE}/writes/{write.id}/{action}", headers=other, json={"reason": body["reason"]} if action == "cancel-preview" else body)
        assert denied.status_code == 404, denied.text
    registered = await client.post("/api/auth/register", json={"email": "quota-viewer@example.com", "password": "supersecret123"})
    member = await client.post("/api/tenants/members", headers=owner, json={"email": "quota-viewer@example.com", "role": "viewer"})
    assert member.status_code == 201, member.text
    switched = await client.post(f"/api/auth/switch/{tid}", headers=_auth(registered.json()["access_token"]))
    viewer = _auth(switched.json()["access_token"])
    assert (await client.post(f"{BASE}/writes/{write.id}/cancel-preview", headers=viewer, json={"reason": body["reason"]})).status_code == 403
    assert (await client.post(f"{BASE}/writes/{write.id}/cancel-unused", headers=viewer, json=body)).status_code == 403
    assert (await client.post(f"{BASE}/writes/{write.id}/cancel-unused", headers=owner, json=body)).status_code == 200
    assert (await client.get(f"{BASE}/actions", headers=other)).json() == []
    assert len((await client.get(f"{BASE}/actions", headers=viewer)).json()) == 1
