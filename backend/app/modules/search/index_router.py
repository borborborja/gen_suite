"""Explicit generation builds and atomic, audited activation/rollback."""
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text, update
from ...core.deps import get_current_principal, get_tenant_db, require_roles
from ...core.dispatch import schedule
from ...models.embedding_index import EmbeddingGeneration, EmbeddingIndexEvent
from ...models.job import Job
from ...models.research_run import ResearchRun
from ...models.task_dispatch import TaskDispatch
from ...models.tenant import Tenant
from ..providers.admission import LOCAL_ENGINES, policy_for, totals
from ..providers.service import ProviderService
from . import indexes

router = APIRouter(prefix="/indexes", tags=["search-indexes"])
ADMIN = [Depends(require_roles("tenant_admin"))]


class GenerationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: uuid.UUID
    title: str = Field(default="Nueva generación del corpus", min_length=1, max_length=255)
    include_public: bool = False
    authorize: Literal[True]


class GenerationAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    expected_active_id: uuid.UUID | None = None
    reason: str = Field(min_length=1, max_length=2000)


async def create_generation(session, principal, body):
    await session.scalar(select(Tenant.id).where(Tenant.id == principal.tenant_id).with_for_update(key_share=True))
    gid = uuid.uuid5(principal.tenant_id, f"full-embedding-generation:{body.client_id}")
    generation = await session.get(EmbeddingGeneration, gid)
    if generation:
        if generation.title != body.title or generation.include_public != body.include_public:
            raise HTTPException(409, "La petición ya existe con otra selección.")
        return generation
    rc = await ProviderService(session).resolve(tenant_id=principal.tenant_id, task_type="embedding")
    if rc.engine not in LOCAL_ENGINES: policy_for(rc)
    profile = indexes.public_profile(rc)
    job = Job(id=uuid.uuid5(gid, "build-job"), tenant_id=principal.tenant_id, type="reembed_corpus",
        status="queued", params={"index_generation_id": str(gid)}, created_by=principal.user_id)
    generation = EmbeddingGeneration(id=gid, tenant_id=principal.tenant_id, title=body.title,
        purpose="full", status="building", version=1, is_active=False, include_public=body.include_public,
        profile=profile, space_key=indexes.space_key(profile), job_id=job.id, created_by=principal.user_id)
    session.add(generation); session.add(job)
    await schedule(session, job, "reembed_corpus")
    indexes.event(session, generation, "created", {"include_public": body.include_public, "authorized": True}, principal.user_id)
    return generation


@router.post("", status_code=201, dependencies=ADMIN)
async def create(body: GenerationCreate, principal=Depends(get_current_principal), session=Depends(get_tenant_db)):
    return await indexes.generation_out(session, await create_generation(session, principal, body))


@router.get("")
async def list_generations(offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100),
                           principal=Depends(get_current_principal), session=Depends(get_tenant_db)):
    rows = (await session.scalars(select(EmbeddingGeneration).where(EmbeddingGeneration.tenant_id == principal.tenant_id)
        .order_by(EmbeddingGeneration.created_at.desc(), EmbeddingGeneration.id).offset(offset).limit(limit))).all()
    # Large coverage joins run only on a selected detail, not 20 times on every list refresh.
    return [{"id": row.id, "title": row.title, "status": row.status, "is_active": row.is_active,
             "version": row.version, "purpose": row.purpose} for row in rows]


@router.get("/active")
async def active(principal=Depends(get_current_principal), session=Depends(get_tenant_db)):
    generation = await indexes.active_generation(session, principal.tenant_id)
    return {"id": generation.id if generation else None,
            "model": generation.profile.get("model") if generation else None,
            "include_public": generation.include_public if generation else False}


@router.get("/{generation_id}")
async def detail(generation_id: uuid.UUID, principal=Depends(get_current_principal), session=Depends(get_tenant_db)):
    generation = await indexes.get_generation(session, principal.tenant_id, generation_id)
    result = await indexes.generation_out(session, generation)
    job = await session.get(Job, generation.job_id) if generation.job_id else None
    result["job_status"] = job.status if job else None
    return result


@router.get("/{generation_id}/events")
async def history(generation_id: uuid.UUID, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
                  principal=Depends(get_current_principal), session=Depends(get_tenant_db)):
    await indexes.get_generation(session, principal.tenant_id, generation_id)
    return list((await session.scalars(select(EmbeddingIndexEvent).where(EmbeddingIndexEvent.generation_id == generation_id)
        .order_by(EmbeddingIndexEvent.created_at.desc(), EmbeddingIndexEvent.id).offset(offset).limit(limit))).all())


@router.post("/{generation_id}/{action}", dependencies=ADMIN)
async def control(generation_id: uuid.UUID, action: Literal["activate", "pause", "resume", "retry", "cancel"],
                  body: GenerationAction, principal=Depends(get_current_principal), session=Depends(get_tenant_db)):
    await session.scalar(select(Tenant.id).where(Tenant.id == principal.tenant_id).with_for_update(key_share=True))
    generation = await session.scalar(select(EmbeddingGeneration).where(EmbeddingGeneration.id == generation_id,
        EmbeddingGeneration.tenant_id == principal.tenant_id).with_for_update())
    if not generation: raise HTTPException(404, "Generación no encontrada.")
    if generation.version != body.expected_version or not body.reason.strip():
        raise HTTPException(409, "Actualiza la generación e indica el motivo del cambio.")
    if action == "activate":
        current = await indexes.active_generation(session, principal.tenant_id)
        if (current.id if current else None) != body.expected_active_id:
            raise HTTPException(409, "Otra persona ha cambiado el índice activo. Revisa ambos perfiles antes de sustituirlo.")
        if generation.status != "ready": raise HTTPException(409, "Termina la generación antes de activarla.")
        if generation.research_run_id:
            run = await session.get(ResearchRun, generation.research_run_id)
            if not run or run.status != "completed":
                raise HTTPException(409, "Termina o revisa la investigación antes de publicar su índice.")
        coverage = await indexes.coverage(session, generation)
        previously_active = False
        if coverage["counts"].get("pending", 0):
            previously_active = bool(await session.scalar(select(EmbeddingIndexEvent.id).where(
                EmbeddingIndexEvent.generation_id == generation.id, EmbeddingIndexEvent.kind == "activated").limit(1)))
        if (coverage["counts"].get("pending", 0) and not previously_active) or not sum(coverage["searchable"].values()):
            raise HTTPException(409, "El índice tiene trabajo pendiente o ninguna evidencia vigente consultable.")
        previous = current.id if current else None
        if current and current.id != generation.id:
            current.is_active = False; current.version += 1
            await session.flush()  # Release unique-active key before setting the replacement.
        generation.is_active = True
        indexes.event(session, generation, "activated", {"previous_id": str(previous) if previous else None,
            "reason": body.reason, "coverage": coverage}, principal.user_id)
    else:
        if generation.purpose != "full" or generation.is_active:
            raise HTTPException(409, "Gestiona esta etapa desde su investigación o trabajo original; el índice activo se conserva.")
        job = await session.get(Job, generation.job_id)
        if not job: raise HTTPException(409, "No se encuentra el trabajo de esta generación.")
        allowed = {"pause": {"queued", "running"}, "resume": {"paused"}, "retry": {"error", "partial"},
                   "cancel": {"queued", "running", "paused", "error", "partial"}}
        if job.status not in allowed[action]: raise HTTPException(409, "Esta acción no corresponde al estado del trabajo.")
        if action in ("resume", "retry"):
            if (await totals(session, principal.tenant_id))["unknown_calls"]:
                raise HTTPException(409, "Concilia las llamadas inciertas antes de reanudar.")
            key = int.from_bytes(job.id.bytes[:8], "big", signed=True)
            if not await session.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": key}):
                raise HTTPException(409, "El intento anterior aún está terminando. Espera antes de reanudar.")
            intent = await session.get(TaskDispatch, job.id)
            if not intent: raise HTTPException(409, "No se encuentra la intención duradera de envío.")
            intent.generation += 1; intent.status = "pending"; intent.available_at = datetime.now(timezone.utc)
            intent.error = None; intent.dispatched_at = None
            job.status = "queued"; job.error = None; job.finished_at = None
            generation.status = "building"; generation.error = None
        else:
            job.status = "paused" if action == "pause" else "cancelled"
            job.finished_at = datetime.now(timezone.utc)
            if action == "cancel": generation.status = "cancelled"
        indexes.event(session, generation, action, {"reason": body.reason, "job_id": str(job.id)}, principal.user_id)
    generation.version += 1
    await session.flush()
    result = await indexes.generation_out(session, generation)
    job = await session.get(Job, generation.job_id) if generation.job_id else None
    result["job_status"] = job.status if job else None
    return result
