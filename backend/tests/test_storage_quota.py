"""Real PostgreSQL + MinIO only in explicitly authorized disposable fixtures."""
import asyncio
import uuid
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from app.core import storage
from app.core.security import decode_token
from app.db.rls import set_rls_context
from app.db.session import SessionLocal
from app.models.job import Job
from app.models.storage_quota import StorageQuota, StorageWrite
from app.modules.storage_audit import quota
from app.settings import settings
from app.tasks import storage_quota_tasks as task
from .test_documents import _auth, _tenant_token

BASE = "/api/storage/quota"


async def setup(client, monkeypatch):
    assert settings.minio_bucket_private.startswith(("gensuite-ci-", "gensuite-test-"))
    assert settings.minio_bucket_public.startswith(("gensuite-ci-", "gensuite-test-"))
    code = uuid.uuid4().hex
    monkeypatch.setattr(settings, "minio_bucket_private", f"gensuite-test-quota-p-{code}")
    monkeypatch.setattr(settings, "minio_bucket_public", f"gensuite-test-quota-u-{code}")
    await storage.ensure_buckets()
    token = await _tenant_token(client, "quota@example.com", "Quota fixture")
    tid = uuid.UUID(decode_token(token, "access")["tid"])
    return tid, _auth(token), settings.minio_bucket_private


async def request_recount(client, headers):
    body = {"client_id": str(uuid.uuid4()), "reason": "Synthetic protected baseline", "authorize_write_pause": True, "external_writers_stopped": True}
    response = await client.post(f"{BASE}/recount", headers=headers, json=body)
    assert response.status_code == 202, response.text
    again = await client.post(f"{BASE}/recount", headers=headers, json=body)
    assert again.status_code == 202 and again.json()["scan"]["id"] == response.json()["scan"]["id"]
    return response.json()["scan"]


async def run_scan(tid, scan):
    await task.recount_storage({}, tenant_id=tid, job_id=scan["job_id"], scan_id=scan["id"])


async def test_existing_data_requires_fenced_baseline_and_counts_versions_copies_and_orphans(client, monkeypatch):
    tid, headers, private = await setup(client, monkeypatch)
    s3 = storage._s3(); key = f"{tid}/{uuid.uuid4()}/original.pdf"
    await asyncio.to_thread(s3.put_bucket_versioning, Bucket=private, VersioningConfiguration={"Status": "Enabled"})
    # Simulate objects created before quotas existed, not an application admission bypass.
    for body in (b"first", b"second"):
        await asyncio.to_thread(s3.put_object, Bucket=private, Key=key, Body=body)
    await asyncio.to_thread(s3.put_object, Bucket=private, Key=f"{tid}/orphan.jpg", Body=b"orphan")
    with pytest.raises(HTTPException) as error: await storage.put_object(private, f"{tid}/new", b"x", "text/plain")
    assert error.value.status_code == 409
    scan = await request_recount(client, headers)
    await run_scan(tid, scan)
    state = (await client.get(BASE, headers=headers)).json()
    assert state["phase"] == "ready" and state["used_bytes"] == 17, state
    await storage.copy_object(private, settings.minio_bucket_public, key)
    assert (await client.get(BASE, headers=headers)).json()["used_bytes"] == 23
    # Deleting the current key merely adds a marker: no credit is granted.
    await storage.delete_object(private, key)
    assert (await client.get(BASE, headers=headers)).json()["used_bytes"] == 23
    scan = await request_recount(client, headers); await run_scan(tid, scan)
    assert (await client.get(BASE, headers=headers)).json()["used_bytes"] == 23


async def test_parallel_reservations_and_caller_fk_transaction_cannot_exceed_limit_or_deadlock(client, monkeypatch):
    tid, _, bucket = await setup(client, monkeypatch)
    monkeypatch.setattr(settings, "storage_tenant_quota_bytes", 10)
    async with SessionLocal() as caller:
        await set_rls_context(caller, tenant_id=tid)
        caller.add(Job(tenant_id=tid, type="test", status="running")); await caller.flush()
        results = await asyncio.wait_for(asyncio.gather(*(
            quota.reserve_independent(bucket, f"{tid}/{i}", 6) for i in range(5)), return_exceptions=True), timeout=20)
        assert sum(isinstance(v, StorageWrite) for v in results) == 1, results
        assert sum(isinstance(v, HTTPException) and v.status_code == 413 for v in results) == 4, results
        await caller.rollback()
    async with SessionLocal() as db:
        await set_rls_context(db, tenant_id=tid)
        assert (await db.get(StorageQuota, tid)).reserved_bytes == 6


async def test_lost_put_ack_retains_space_and_recheck_never_repeats_write(client, monkeypatch):
    tid, headers, bucket = await setup(client, monkeypatch)
    s3 = storage._s3(); real_put = s3.put_object; calls = []
    def lose_ack(**kwargs):
        calls.append(kwargs["Key"]); real_put(**kwargs); raise TimeoutError("Synthetic lost response")
    monkeypatch.setattr(s3, "put_object", lose_ack)
    with pytest.raises(TimeoutError): await storage.put_object(bucket, f"{tid}/one", b"abc", "text/plain")
    state = (await client.get(BASE, headers=headers)).json()
    assert state["reserved_bytes"] == 3 and state["used_bytes"] == 0, state
    with pytest.raises(HTTPException): await storage.put_object(bucket, f"{tid}/one", b"abc", "text/plain")
    rows = (await client.get(f"{BASE}/writes", headers=headers)).json()
    assert len(rows) == 1 and rows[0]["state"] == "unknown", rows
    reconciled = await client.post(f"{BASE}/writes/{rows[0]['id']}/recheck", headers=headers)
    assert reconciled.status_code == 200, reconciled.text
    assert reconciled.json()["state"] == "succeeded" and calls == [f"{tid}/one"]
    state = (await client.get(BASE, headers=headers)).json()
    assert state["reserved_bytes"] == 0 and state["used_bytes"] == 3
    assert s3.meta.config.retries["total_max_attempts"] == 1


async def test_existing_reservation_drains_while_new_writes_block_then_baseline_activates(client, monkeypatch):
    tid, headers, bucket = await setup(client, monkeypatch)
    key = f"{tid}/ai-calls/{uuid.uuid4()}/response.json"
    ticket = await quota.reserve_independent(bucket, key, 16 * 1024**2)
    scan = await request_recount(client, headers); await run_scan(tid, scan)
    state = (await client.get(BASE, headers=headers)).json()
    assert state["phase"] == "draining" and state["scan"]["job_status"] == "error", state
    with pytest.raises(HTTPException): await storage.put_object(bucket, f"{tid}/new", b"no", "text/plain")
    await storage.put_object(bucket, key, b"{}", "application/json", write_id=ticket.id)
    retried = await client.post(f"{BASE}/recount/retry", headers=headers)
    assert retried.status_code == 202, retried.text
    await run_scan(tid, scan)
    state = (await client.get(BASE, headers=headers)).json()
    assert state["phase"] == "ready" and state["used_bytes"] == 2 and state["reserved_bytes"] == 0, state


async def test_cancel_during_scan_cannot_replace_previous_baseline(client, monkeypatch):
    tid, headers, bucket = await setup(client, monkeypatch)
    await storage.put_object(bucket, f"{tid}/one", b"a", "text/plain")
    scan = await request_recount(client, headers)
    entered, release = asyncio.Event(), asyncio.Event()
    async def pause_process(db, row, s3, *, finalize):
        entered.set(); await release.wait(); await finalize(db, row)
    monkeypatch.setattr(task, "process", pause_process)
    worker = asyncio.create_task(run_scan(tid, scan))
    try:
        await asyncio.wait_for(entered.wait(), 10)
        with pytest.raises(HTTPException): await storage.put_object(bucket, f"{tid}/blocked", b"b", "text/plain")
        cancelled = await client.post(f"{BASE}/recount/cancel", headers=headers)
        assert cancelled.status_code == 202, cancelled.text
    finally:
        release.set(); await asyncio.wait_for(worker, 10)
    state = (await client.get(BASE, headers=headers)).json()
    assert state["phase"] == "ready" and state["used_bytes"] == 1 and state["scan"]["job_status"] == "cancelled", state


async def test_paid_provider_is_not_called_when_response_cannot_fit(monkeypatch):
    from .test_ai_admission import setup_ledger
    from app.modules.providers import admission
    from unittest.mock import MagicMock
    rc = await setup_ledger(1000)
    monkeypatch.setattr(settings, "storage_tenant_quota_bytes", admission.MAX_RESULT_BYTES - 1)
    transport = MagicMock()
    with pytest.raises(HTTPException) as error:
        await admission.execute(rc, kind="test", request="synthetic", transport=transport)
    assert error.value.status_code == 413; transport.assert_not_called()
    async with SessionLocal() as db:
        await set_rls_context(db, tenant_id=rc.tenant_id)
        assert not (await db.scalars(select(StorageWrite))).all()
        assert (await admission.totals(db, rc.tenant_id))["reserved_microusd"] == 0


async def test_observational_routes_cannot_cancel_or_resume_a_protected_recount(client, monkeypatch):
    tid, headers, _ = await setup(client, monkeypatch)
    scan = await request_recount(client, headers)
    observational = "/api/storage/inventories"
    listed = await client.get(observational, headers=headers)
    assert listed.status_code == 200 and listed.json() == []
    assert (await client.get(f"{observational}/{scan['id']}", headers=headers)).status_code == 404
    for action in ("cancel", "retry"):
        response = await client.post(f"{observational}/{scan['id']}/{action}", headers=headers)
        assert response.status_code == 404, response.text
    state = (await client.get(BASE, headers=headers)).json()
    assert state["phase"] == "draining" and state["scan"]["job_status"] == "queued", state
    generic = await client.post(f"/api/jobs/{scan['job_id']}/cancel", headers=headers)
    assert generic.status_code == 409, generic.text
    await run_scan(tid, scan)
    assert (await client.get(BASE, headers=headers)).json()["phase"] == "ready"
