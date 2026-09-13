"""Real PostgreSQL admission/RLS tests; disposable DB only, provider and S3 mocked."""
import asyncio
import io
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.rls import set_rls_context
from app.db.session import SessionLocal
from app.models.ai_call import AiCall
from app.models.job import Job
from app.modules.providers import admission as a
from app.modules.providers.service import ResolvedCredential
from app.settings import settings
from app.modules.storage_audit import inventory as storage_inventory, quota as storage_quota


async def setup_ledger(budget_cents=1):
    tenant_id = uuid.uuid4()
    admin = create_async_engine(settings.admin_database_url)
    try:
        async with admin.begin() as conn:
            await conn.execute(text("INSERT INTO tenants(id,name,slug,monthly_budget_cents) VALUES (:id,'AI test',:slug,:budget)"),
                               {"id": tenant_id, "slug": tenant_id.hex, "budget": budget_cents})
            # This module mocks S3. Establish an explicitly synthetic empty baseline;
            # production bootstrap and real object accounting have separate S3 tests.
            await conn.execute(text("INSERT INTO storage_quotas(tenant_id,phase,location_hash,baseline_at) VALUES (:id,'ready',:hash,now())"),
                               {"id": tenant_id, "hash": storage_inventory.configuration()[1]})
    finally:
        await admin.dispose()
    return ResolvedCredential(engine="openai", model="synthetic", api_key="not-real", base_url="https://provider.invalid/v1",
        params={"budget_policy": dict(model="synthetic", context_tokens=1000, max_output_tokens=0,
            input_usd_per_million="1", output_usd_per_million="0",
            valid_until=(datetime.now(timezone.utc) + timedelta(days=30)).isoformat(), source="Synthetic integration test")},
        tenant_id=tenant_id, credential_id=uuid.uuid4(), task_type="embedding")


async def test_parallel_admission_respects_exact_budget():
    rc = await setup_ledger()  # 10,000 micro-USD: exactly ten reservations of 1,000.
    async def reserve(index):
        return await a._reserve(rc, a.snapshot(rc), f"{index:064x}", f"{index:064x}", 1000)
    results = await asyncio.gather(*(reserve(i) for i in range(20)), return_exceptions=True)
    assert sum(isinstance(value, tuple) for value in results) == 10
    assert sum(isinstance(value, HTTPException) and value.status_code == 402 for value in results) == 10
    async with SessionLocal() as session:
        await set_rls_context(session, tenant_id=rc.tenant_id)
        balance = await a.totals(session, rc.tenant_id)
        assert balance == dict(settled_microusd=0, reserved_microusd=10000, unknown_calls=0)


async def test_duplicate_admission_and_caller_fk_lock_do_not_deadlock():
    rc = await setup_ledger()
    # A real business transaction holds a tenant foreign-key KEY SHARE lock until commit.
    async with SessionLocal() as caller:
        await set_rls_context(caller, tenant_id=rc.tenant_id)
        caller.add(Job(tenant_id=rc.tenant_id, type="test", status="running"))
        await caller.flush()
        results = await asyncio.wait_for(asyncio.gather(*(
            a._reserve(rc, a.snapshot(rc), "a" * 64, "a" * 64, 1000) for _ in range(6))), timeout=10)
        assert sum(fresh for _, fresh in results) == 1
        assert len({row.id for row, _ in results}) == 1
        await caller.rollback()
    async with SessionLocal() as session:
        await set_rls_context(session, tenant_id=rc.tenant_id)
        assert (await a.totals(session, rc.tenant_id))["reserved_microusd"] == 1000


async def test_unknown_reservation_survives_month_rollover_and_blocks_new_calls():
    rc = await setup_ledger()
    row, _ = await a._reserve(rc, a.snapshot(rc), "a" * 64, "a" * 64, 1000)
    await a._unknown(rc.tenant_id, row.id, "timeout")
    async with SessionLocal() as session:
        await set_rls_context(session, tenant_id=rc.tenant_id)
        await session.execute(text("UPDATE ai_calls SET created_at=now()-interval '40 days' WHERE id=:id"), {"id": row.id})
        await session.commit()
    with pytest.raises(HTTPException) as error:
        await a._reserve(rc, a.snapshot(rc), "b" * 64, "b" * 64, 1000)
    assert error.value.status_code == 409
    async with SessionLocal() as session:
        await set_rls_context(session, tenant_id=rc.tenant_id)
        assert (await a.totals(session, rc.tenant_id))["reserved_microusd"] == 1000


async def test_committed_response_replay_does_not_call_provider_again(monkeypatch):
    rc = await setup_ledger()
    saved = {}
    async def put(bucket, key, body, content_type, *, write_id):
        write = await storage_quota.begin(bucket, key, len(body), write_id)
        saved[key] = body
        await storage_quota.finish(write, len(body), "null")
    async def open_result(bucket, key): return {"Body": io.BytesIO(saved[key]), "ContentLength": len(saved[key])}
    monkeypatch.setattr(a.storage, "put_object", put)
    monkeypatch.setattr(a.storage, "open_object", open_result)
    calls = []
    def transport(): calls.append(1); return ["synthetic-vector"], {"prompt": 2, "completion": 0}
    first = await a.execute(rc, kind="test", request="same", transport=transport)
    second = await a.execute(rc, kind="test", request="same", transport=transport)
    assert first == second and len(calls) == 1
    async with SessionLocal() as session:
        await set_rls_context(session, tenant_id=rc.tenant_id)
        row = await session.scalar(select(AiCall))
        assert row.cost_microusd == 2 and row.state == "succeeded"
        assert (await a.totals(session, rc.tenant_id))["reserved_microusd"] == 0


async def test_cancelled_job_cannot_admit_new_paid_request():
    rc = await setup_ledger()
    rc.job_id = uuid.uuid4()
    async with SessionLocal() as session:
        await set_rls_context(session, tenant_id=rc.tenant_id)
        session.add(Job(id=rc.job_id, tenant_id=rc.tenant_id, type="test", status="cancelled"))
        await session.commit()
    with pytest.raises(HTTPException) as error:
        await a._reserve(rc, a.snapshot(rc), "a" * 64, "a" * 64, 1000)
    assert error.value.status_code == 409


async def test_http_reconciliation_recovery_and_cross_tenant_denial(client, monkeypatch):
    from .test_research import _tenant_token
    from .test_tree import _auth
    first = _auth(await _tenant_token(client, "ledger1@example.com", "First ledger"))
    second = _auth(await _tenant_token(client, "ledger2@example.com", "Second ledger"))
    # Resolve the authenticated tenant through its own endpoint, not by assuming list order.
    from app.core.security import decode_token
    tenant_id = uuid.UUID(decode_token(first["Authorization"].split()[1], expected_type="access")["tid"])
    rc = ResolvedCredential(engine="openai", model="synthetic", api_key="not-real", base_url="https://provider.invalid/v1",
        params={"budget_policy": dict(model="synthetic", context_tokens=1000, max_output_tokens=0,
            input_usd_per_million="1", output_usd_per_million="0",
            valid_until=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(), source="Synthetic HTTP fixture")},
        tenant_id=tenant_id, credential_id=uuid.uuid4(), task_type="embedding")
    ids = [uuid.uuid4() for _ in range(3)]
    async with SessionLocal() as session:
        await set_rls_context(session, tenant_id=tenant_id)
        for index, call_id in enumerate(ids):
            session.add(AiCall(id=call_id, tenant_id=tenant_id, task_type="embedding",
                operation_key=f"{index:064x}", request_hash=f"{index:064x}", profile=a.snapshot(rc),
                state="inflight" if index == 2 else "unknown", reserved_microusd=1000))
        await session.commit()
    evidence = {"cost_microusd": 123, "evidence": "Synthetic invoice confirms charge", "provider_request_id": "test-request"}
    assert (await client.post(f"/api/providers/ai-calls/{ids[0]}/reconcile", headers=second, json=evidence)).status_code == 404
    assert (await client.get("/api/providers/ai-calls", headers=second)).json() == []
    listed = (await client.get("/api/providers/ai-calls", headers=first)).json()
    assert len(listed) == 3 and "result_key" not in listed[0]
    result = await client.post(f"/api/providers/ai-calls/{ids[0]}/reconcile", headers=first, json=evidence)
    assert result.status_code == 200, result.text
    assert result.json()["state"] == "reconciled"
    assert (await client.post(f"/api/providers/ai-calls/{ids[0]}/reconcile", headers=first, json=evidence)).status_code == 409
    assert (await client.post(f"/api/providers/ai-calls/{ids[2]}/reconcile", headers=first, json=evidence)).status_code == 409
    envelope = {"data": ["saved"], "usage": {"prompt": 3, "completion": 0, "request_id": "saved-req"}, "error": None}
    content = a.canonical(envelope)
    async def open_saved(bucket, key):
        assert key == f"{tenant_id}/ai-calls/{ids[1]}/response.json"
        return {"Body": io.BytesIO(content), "ContentLength": len(content)}
    monkeypatch.setattr(a.storage, "open_object", open_saved)
    recovered = await client.post(f"/api/providers/ai-calls/{ids[1]}/recover", headers=first)
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["cost_microusd"] == 3
    spend = (await client.get("/api/providers/spend", headers=first)).json()
    assert spend["settled_microusd"] == 126 and spend["reserved_microusd"] == 1000
