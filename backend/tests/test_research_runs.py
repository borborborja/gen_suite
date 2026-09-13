"""Disposable PostgreSQL tests: API controls, real execution locks, budgets and outbox."""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import select, text
from app.core.security import decode_token
from app.db.rls import set_rls_context
from app.db.session import SessionLocal, engine
from app.models.document import Document
from app.models.job import Job
from app.models.task_dispatch import TaskDispatch
from app.models.research_run import ResearchRun
from app.modules.providers import admission
from app.modules.providers.service import ResolvedCredential
from app.modules.research.runs import run_balance
from app.tasks.research_run_tasks import tick_run
from app.tasks.maintenance_tasks import reap_stale_jobs
from .test_research import _tenant_token
from .test_tree import _auth


async def tick(tenant_id, run_id):
    async with SessionLocal() as session:
        await set_rls_context(session, tenant_id=tenant_id)
        await tick_run(session, tenant_id, run_id)
        await session.commit()


async def test_plan_pause_resume_same_job_waits_for_actual_worker_lock(client):
    token = await _tenant_token(client, "runner@example.com", "Run workspace")
    headers = _auth(token)
    tenant_id = uuid.UUID(decode_token(token, "access")["tid"])
    document_id = uuid.uuid4()
    async with SessionLocal() as session:
        await set_rls_context(session, tenant_id=tenant_id)
        session.add(Document(id=document_id, tenant_id=tenant_id, title="Synthetic", doc_type="image_set",
            storage_bucket="test-only", storage_prefix=f"{tenant_id}/{document_id}/", page_count=1))
        await session.commit()
    body = dict(client_id=str(uuid.uuid4()), title="Preserve only", document_ids=[str(document_id)], budget_microusd=0,
                transcribe=False, extract=False, index=False, discover_all_tree=False, pdf=True, archive=False)
    response = await client.post("/api/research/runs", headers=headers, json=body)
    assert response.status_code == 201, response.text
    run = response.json(); run_id = uuid.UUID(run["id"])
    assert run["status"] == "planned" and run["profiles"] == {}
    repeated = await client.post("/api/research/runs", headers=headers, json=body)
    assert repeated.json()["id"] == run["id"]
    assert (await client.post("/api/research/runs", headers=headers, json={**body, "title": "changed"})).status_code == 409
    assert (await client.get(f"/api/research/runs/{run_id}/stages", headers=headers)).json()[0]["job_id"] is None
    run = (await client.post(f"/api/research/runs/{run_id}/start", headers=headers, json={"expected_version": run["version"]})).json()
    await tick(tenant_id, run_id)
    run = (await client.get(f"/api/research/runs/{run_id}", headers=headers)).json()
    stage = (await client.get(f"/api/research/runs/{run_id}/stages", headers=headers)).json()[0]
    job_id = uuid.UUID(stage["job_id"])
    async with SessionLocal() as session:
        await set_rls_context(session, tenant_id=tenant_id)
        job = await session.get(Job, job_id)
        job.created_at = datetime.now(timezone.utc) - timedelta(days=3)
        await session.commit()
    key = int.from_bytes(job_id.bytes[:8], "big", signed=True)
    async with engine.connect() as worker_connection:
        assert await worker_connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})
        await worker_connection.commit()
        try:
            run = (await client.post(f"/api/research/runs/{run_id}/pause", headers=headers, json={"expected_version": run["version"]})).json()
            assert run["status"] == "paused"
            run = (await client.post(f"/api/research/runs/{run_id}/resume", headers=headers, json={"expected_version": run["version"]})).json()
            await tick(tenant_id, run_id)
            async with SessionLocal() as session:
                await set_rls_context(session, tenant_id=tenant_id)
                assert (await session.get(Job, job_id)).status == "paused"
                assert (await session.get(TaskDispatch, job_id)).generation == 0
        finally:
            await worker_connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
            await worker_connection.commit()
    await tick(tenant_id, run_id)
    async with SessionLocal() as session:
        await set_rls_context(session, tenant_id=tenant_id)
        job = await session.get(Job, job_id)
        assert job.status == "queued"
        dispatch = await session.get(TaskDispatch, job_id)
        assert dispatch.generation == 1
        dispatch.status = "dispatched"
        await session.commit()
    await reap_stale_jobs({})  # An old creation timestamp cannot expire a freshly resumed attempt.
    async with SessionLocal() as session:
        await set_rls_context(session, tenant_id=tenant_id)
        job = await session.get(Job, job_id)
        assert job.status == "queued"
        # Simulated document task completion: this test covers orchestration, not S3/PDF rendering.
        job.status = "completed"; job.result = {"export_id": str(uuid.uuid4())}
        await session.commit()
    await tick(tenant_id, run_id); await tick(tenant_id, run_id)
    result = (await client.get(f"/api/research/runs/{run_id}", headers=headers)).json()
    assert result["status"] == "completed" and result["stage_counts"] == {"completed": 1}
    assert result["spent_microusd"] == 0
    other = _auth(await _tenant_token(client, "other-runner@example.com", "Other workspace"))
    assert (await client.get(f"/api/research/runs/{run_id}", headers=other)).status_code == 404
    assert (await client.get(f"/api/research/runs/{run_id}/stages", headers=other)).status_code == 404


async def test_parallel_calls_cannot_exceed_lifetime_run_budget(client):
    token = await _tenant_token(client, "run-budget@example.com", "Budget workspace")
    tenant_id = uuid.UUID(decode_token(token, "access")["tid"])
    await client.put("/api/providers/budget", headers=_auth(token), json={"monthly_budget_cents": 10000})
    run_id, job_id = uuid.uuid4(), uuid.uuid4()
    policy = dict(model="synthetic", context_tokens=100, max_output_tokens=0,
        input_usd_per_million="1", output_usd_per_million="0",
        valid_until=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(), source="Synthetic tariff fixture")
    async with SessionLocal() as session:
        await set_rls_context(session, tenant_id=tenant_id)
        session.add(ResearchRun(id=run_id, tenant_id=tenant_id, title="Budget", created_by=uuid.uuid4(),
            status="running", version=1, budget_microusd=1000, request_hash="a" * 64, config={}, profiles={}))
        session.add(Job(id=job_id, tenant_id=tenant_id, type="embedding", status="running", params={"research_run_id": str(run_id)}))
        await session.commit()
    rc = ResolvedCredential(engine="openai", model="synthetic", api_key="not-real", base_url="https://example.invalid",
        params={"budget_policy": policy}, tenant_id=tenant_id, job_id=job_id, research_run_id=run_id,
        credential_id=uuid.uuid4(), task_type="embedding")
    results = await asyncio.gather(*(admission._reserve(rc, admission.snapshot(rc), f"{i:064x}", f"{i:064x}", 100) for i in range(15)), return_exceptions=True)
    assert sum(isinstance(result, tuple) for result in results) == 10
    assert sum(isinstance(result, HTTPException) and result.status_code == 402 for result in results) == 5
    async with SessionLocal() as session:
        await set_rls_context(session, tenant_id=tenant_id)
        assert await run_balance(session, tenant_id, run_id) == {"spent_microusd": 0, "reserved_microusd": 1000}
