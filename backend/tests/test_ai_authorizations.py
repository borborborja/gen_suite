"""Disposable PostgreSQL/API tests only. No requests to an AI provider."""
import asyncio
import uuid
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from app.core.security import decode_token
from app.db.rls import set_rls_context
from app.db.session import SessionLocal
from app.models.ai_authorization import AiBillingAuthorization
from app.models.ai_call import AiCall
from app.models.embedding_index import EmbeddingGeneration
from app.models.job import Job
from app.models.research_run import ResearchRun
from app.models.storage_quota import StorageQuota
from app.modules.storage_audit.inventory import configuration as storage_configuration
from app.modules.providers import admission as a
from app.modules.providers.service import ProviderService, ResolvedCredential
from .test_research import _tenant_token
from .test_tree import _auth


async def setup(client, scope):
    token = await _tenant_token(client, f"renew-{scope}@example.com", "Renewal fixture")
    tid = uuid.UUID(decode_token(token, "access")["tid"]); headers = _auth(token)
    response = await client.post("/api/providers/credentials", headers=headers, json={"scope": "tenant", "provider_key": "openai",
        "label": "Synthetic only", "base_url": "https://provider.invalid/v1", "model_default": "synthetic-model", "api_key": "synthetic-not-a-real-key"})
    assert response.status_code == 201, response.text
    await client.put("/api/providers/budget", headers=headers, json={"monthly_budget_cents": 10000})
    cid = uuid.UUID(response.json()["id"]); target, jid = uuid.uuid4(), uuid.uuid4()
    if scope == "job": target = jid
    task = "embedding" if scope == "embedding_generation" else "inference"
    policy = dict(model="synthetic-model", context_tokens=1000, max_output_tokens=0 if task == "embedding" else 100,
        input_usd_per_million="1", output_usd_per_million="2", valid_until="2000-01-01T00:00:00Z", source="Synthetic expired tariff")
    rc = ResolvedCredential(engine="openai", model="synthetic-model", api_key="synthetic-not-a-real-key", base_url="https://provider.invalid/v1",
        params={"budget_policy": policy}, tenant_id=tid, credential_id=cid, job_id=jid, task_type=task,
        research_run_id=target if scope == "research_run" else None,
        embedding_generation_id=target if scope == "embedding_generation" else None)
    profile = a.snapshot(rc); original_id = uuid.uuid4()
    async with SessionLocal() as session:
        await set_rls_context(session, tenant_id=tid)
        session.add(StorageQuota(tenant_id=tid, phase="ready", location_hash=storage_configuration()[1], used_bytes=0, reserved_bytes=0))
        params = {"research_run_id": str(target)} if scope == "research_run" else {"index_generation_id": str(target)} if scope == "embedding_generation" else {}
        session.add(Job(id=jid, tenant_id=tid, type=task, status="running", params=params))
        if scope == "research_run":
            session.add(ResearchRun(id=target, tenant_id=tid, title="Synthetic run", created_by=uuid.uuid4(), status="paused", version=1,
                budget_microusd=1000000, request_hash="a" * 64, config={}, profiles={task: profile}))
        if scope == "embedding_generation":
            session.add(EmbeddingGeneration(id=target, tenant_id=tid, title="Synthetic index", purpose="full", status="ready",
                version=1, profile=profile, space_key="a" * 64, is_active=True, job_id=jid))
        await session.flush()
        session.add(AiCall(id=original_id, tenant_id=tid, job_id=jid, research_run_id=rc.research_run_id, task_type=task,
            profile=profile, request_hash="1" * 64, operation_key="1" * 64, state="succeeded", reserved_microusd=1200, cost_microusd=7,
            result_key="synthetic-only", result_checksum="a" * 64))
        await session.commit()
    proposal = dict(scope=scope, target_id=str(target), task_type=task, input_usd_per_million="3", output_usd_per_million="4",
        valid_until="2099-01-01T00:00:00Z", source="Synthetic verified new tariff")
    return tid, headers, rc, profile, original_id, proposal


@pytest.mark.parametrize("scope", ["research_run", "embedding_generation", "job"])
async def test_renewal_keeps_receipts_profile_budget_and_execution_separate(client, scope):
    tid, headers, rc, original, original_id, proposal = await setup(client, scope)
    path = "/api/providers/authorizations"
    preview = await client.post(f"{path}/preview", headers=headers, json=proposal)
    assert preview.status_code == 200, preview.text
    assert "synthetic-not-a-real-key" not in preview.text
    body = {**proposal, "client_id": str(uuid.uuid4()), "preview_hash": preview.json()["preview_hash"], "authorize": True, "reason": "Synthetic review"}
    response = await client.post(path, headers=headers, json=body)
    assert response.status_code == 201, response.text
    assert (await client.post(path, headers=headers, json=body)).json()["id"] == response.json()["id"]
    assert (await client.post(path, headers=headers, json={**body, "reason": "changed reason"})).status_code == 409
    async with SessionLocal() as session:
        await set_rls_context(session, tenant_id=tid)
        renewed = (await session.scalars(select(AiBillingAuthorization))).all()
        assert len(renewed) == 1 and renewed[0].version == 1
        previous = await session.get(AiCall, original_id)
        assert previous.profile == original and previous.cost_microusd == 7
        assert (await session.get(Job, rc.job_id)).status == "running"
        resolved = await ProviderService(session).resolve(tenant_id=tid, task_type=rc.task_type, job_id=rc.job_id)
        assert a.snapshot(resolved) == original
        if scope == "research_run":
            run = await session.get(ResearchRun, rc.research_run_id)
            assert run.status == "paused" and run.budget_microusd == 1000000 and run.profiles[rc.task_type] == original
    if scope == "research_run":
        with pytest.raises(HTTPException) as error: await a._reserve(rc, original, "2" * 64, "2" * 64, 1200)
        assert error.value.status_code == 409  # Renewal did not resume anything.
        async with SessionLocal() as session:
            await set_rls_context(session, tenant_id=tid)
            (await session.get(ResearchRun, rc.research_run_id)).status = "running"
            await session.commit()
    admitted, fresh = await a._reserve(rc, original, "2" * 64, "2" * 64, 1200)
    assert fresh and admitted.profile["execution_profile"] == original
    assert admitted.reserved_microusd == (3000 if rc.task_type == "embedding" else 3400)
    assert admitted.profile["billing_authorization"]["id"] == response.json()["id"]
    replay, fresh = await a._reserve(rc, original, "1" * 64, "1" * 64, 1200)
    assert not fresh and replay.id == original_id and replay.cost_microusd == 7
    other = _auth(await _tenant_token(client, f"other-{scope}@example.com", "Other"))
    assert (await client.post(f"{path}/preview", headers=other, json=proposal)).status_code == 404
    assert (await client.get(path, headers=other, params={k: proposal[k] for k in ("scope", "target_id", "task_type")})).status_code == 404


async def test_concurrent_approvals_require_fresh_preview_and_job_uses_run_owner(client):
    tid, headers, rc, profile, _, proposal = await setup(client, "research_run")
    proposal.update(scope="job", target_id=str(rc.job_id))
    path = "/api/providers/authorizations"
    preview = await client.post(f"{path}/preview", headers=headers, json=proposal)
    assert preview.status_code == 200, preview.text
    assert preview.json()["owner"]["scope"] == "research_run"
    body = {**proposal, "preview_hash": preview.json()["preview_hash"], "authorize": True, "reason": "Synthetic concurrent approval"}
    results = await asyncio.gather(*(client.post(path, headers=headers, json={**body, "client_id": str(uuid.uuid4())}) for _ in range(2)))
    assert sorted(result.status_code for result in results) == [201, 409]
    async with SessionLocal() as session:
        await set_rls_context(session, tenant_id=tid)
        rows = (await session.scalars(select(AiBillingAuthorization))).all()
        assert len(rows) == 1 and rows[0].research_run_id == rc.research_run_id and rows[0].job_id is None
        assert (await session.get(ResearchRun, rc.research_run_id)).profiles["inference"] == profile
