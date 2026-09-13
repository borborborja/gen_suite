"""Explicit two-step financial renewal. Never starts jobs or modifies existing receipts."""
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from ...core.deps import get_current_principal, get_tenant_db, require_roles
from ...models.ai_authorization import AiBillingAuthorization
from ...models.ai_call import AiCall
from ...models.embedding_index import EmbeddingGeneration
from ...models.job import Job
from ...models.research_run import ResearchRun
from ...models.tenant import Tenant
from .admission import LOCAL_ENGINES, fingerprint
from .billing import SCOPE_FIELDS, compatible_policy, latest, require_fresh
from .service import ProviderService

Scope = Literal["research_run", "embedding_generation", "job"]
Task = Literal["transcription", "inference", "embedding"]
router = APIRouter(prefix="/authorizations", tags=["ai-authorizations"])
ADMIN = [Depends(require_roles("tenant_admin"))]


class Proposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Scope
    target_id: uuid.UUID
    task_type: Task
    input_usd_per_million: Decimal = Field(gt=0, le=10000, max_digits=12, decimal_places=6)
    output_usd_per_million: Decimal = Field(ge=0, le=10000, max_digits=12, decimal_places=6)
    valid_until: AwareDatetime
    source: str = Field(min_length=8, max_length=1000)

    @field_validator("source")
    @classmethod
    def meaningful_source(cls, value):
        if len(value.strip()) < 8:
            raise ValueError("Indica una fuente de tarifa verificable.")
        return value.strip()


class Approval(Proposal):
    client_id: uuid.UUID
    preview_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    authorize: Literal[True]
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("authorize", mode="before")
    @classmethod
    def explicit_authorization(cls, value):
        if value is not True:
            raise ValueError("La renovación requiere confirmación explícita.")
        return value


async def owner_profile(session, tenant_id, scope, target_id, task_type, *, lock=False):
    if scope == "job":
        job = await session.scalar(select(Job).where(Job.id == target_id, Job.tenant_id == tenant_id))
        if not job: raise HTTPException(404, "Trabajo no encontrado.")
        # Resolve the effective owner before locking: coordinators lock run before job.
        parent = (job.params or {}).get("research_run_id")
        if parent:
            return await owner_profile(session, tenant_id, "research_run", uuid.UUID(parent), task_type, lock=lock)
        parent = (job.params or {}).get("index_generation_id")
        if parent and task_type == "embedding":
            return await owner_profile(session, tenant_id, "embedding_generation", uuid.UUID(parent), task_type, lock=lock)
    model = {"research_run": ResearchRun, "embedding_generation": EmbeddingGeneration, "job": Job}[scope]
    statement = select(model).where(model.id == target_id, model.tenant_id == tenant_id)
    owner = await session.scalar(statement.with_for_update().execution_options(populate_existing=True) if lock else statement)
    if not owner: raise HTTPException(404, "No se encuentra el titular de esta autorización.")
    if scope == "research_run":
        profile = owner.profiles.get(task_type)
    elif scope == "embedding_generation":
        profile = owner.profile if task_type == "embedding" else None
    else:
        receipt = await session.scalar(select(AiCall).where(AiCall.tenant_id == tenant_id, AiCall.job_id == target_id,
            AiCall.task_type == task_type).order_by(AiCall.created_at, AiCall.id).limit(1))
        profile = receipt.profile.get("execution_profile", receipt.profile) if receipt else None
    if not profile or "budget_policy" not in profile or profile.get("engine") in LOCAL_ENGINES:
        raise HTTPException(409, "No hay un perfil de pago fijado para esta tarea. Los perfiles locales no necesitan renovación; los trabajos aún sin llamadas se configuran en Ajustes.")
    return scope, owner, profile


def owner_out(scope, owner):
    version = owner.updated_at.isoformat() if scope == "job" and owner.updated_at else getattr(owner, "version", None)
    return {"scope": scope, "id": str(owner.id), "title": getattr(owner, "title", None) or getattr(owner, "type", "Trabajo"),
            "status": owner.status, "version": version}


def authorization_out(row):
    return {"id": row.id, "version": row.version, "task_type": row.task_type,
            "scope": next(scope for scope, field in SCOPE_FIELDS.items() if getattr(row, field)),
            "target_id": next(getattr(row, field) for field in SCOPE_FIELDS.values() if getattr(row, field)),
            "base_profile_hash": row.base_profile_hash, "budget_policy": row.budget_policy,
            "created_by": row.created_by, "created_at": row.created_at, "reason": row.reason}


async def preview_data(session, tenant_id, body, *, lock=False):
    scope, owner, profile = await owner_profile(session, tenant_id, body.scope, body.target_id, body.task_type, lock=lock)
    if owner.status == "cancelled" or (scope != "embedding_generation" and owner.status == "completed"):
        raise HTTPException(409, "La ejecución ya ha terminado. Renueva el índice activo por separado si vas a consultarlo.")
    # Resolve only to verify an accessible, active credential. No provider request is made.
    await ProviderService(session).resolve_frozen(tenant_id=tenant_id, task_type=body.task_type, profile=profile)
    current = await latest(session, tenant_id, scope, owner.id, body.task_type)
    if current and current.base_profile_hash != fingerprint(profile):
        raise HTTPException(409, "El perfil fijado no coincide con su historial de autorización.")
    changes = body.model_dump(mode="json", include={"input_usd_per_million", "output_usd_per_million", "valid_until", "source"})
    after = compatible_policy(profile, {**profile["budget_policy"], **changes})
    require_fresh(after)
    before = compatible_policy(profile, current.budget_policy if current else profile["budget_policy"])
    result = {"owner": owner_out(scope, owner), "task_type": body.task_type, "execution_profile": profile,
        "base_profile_hash": fingerprint(profile), "authorization_version": current.version if current else 0,
        "before": before.model_dump(mode="json"), "after": after.model_dump(mode="json"),
        "before_reservation_microusd": before.reservation, "after_reservation_microusd": after.reservation}
    result["preview_hash"] = fingerprint(result)
    return result, owner


@router.get("")
async def history(scope: Scope, target_id: uuid.UUID, task_type: Task,
                  offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100),
                  principal=Depends(get_current_principal), session=Depends(get_tenant_db)):
    scope, owner, profile = await owner_profile(session, principal.tenant_id, scope, target_id, task_type)
    rows = (await session.scalars(select(AiBillingAuthorization).where(
        AiBillingAuthorization.tenant_id == principal.tenant_id,
        getattr(AiBillingAuthorization, SCOPE_FIELDS[scope]) == owner.id,
        AiBillingAuthorization.task_type == task_type).order_by(AiBillingAuthorization.version.desc()).offset(offset).limit(limit))).all()
    current = await latest(session, principal.tenant_id, scope, owner.id, task_type)
    if current and current.base_profile_hash != fingerprint(profile):
        raise HTTPException(409, "El perfil fijado no coincide con su historial de autorización.")
    policy = compatible_policy(profile, current.budget_policy if current else profile["budget_policy"])
    return {"owner": owner_out(scope, owner), "execution_profile": profile,
        "current_policy": policy.model_dump(mode="json"), "expired": policy.valid_until <= datetime.now(timezone.utc),
        "authorization_version": current.version if current else 0, "history": [authorization_out(row) for row in rows]}


@router.post("/preview", dependencies=ADMIN)
async def preview(body: Proposal, principal=Depends(get_current_principal), session=Depends(get_tenant_db)):
    result, _ = await preview_data(session, principal.tenant_id, body)
    return result


@router.post("", status_code=201, dependencies=ADMIN)
async def approve(body: Approval, principal=Depends(get_current_principal), session=Depends(get_tenant_db)):
    if not body.reason.strip(): raise HTTPException(422, "Documenta el motivo de la renovación.")
    await session.scalar(select(Tenant.id).where(Tenant.id == principal.tenant_id).with_for_update(key_share=True))
    aid = uuid.uuid5(principal.tenant_id, f"ai-billing-authorization:{body.client_id}")
    request_hash = fingerprint(body.model_dump(mode="json"))
    existing = await session.get(AiBillingAuthorization, aid)
    if existing:
        if existing.tenant_id != principal.tenant_id or existing.request_hash != request_hash:
            raise HTTPException(409, "Esta confirmación ya existe con otro contenido.")
        return authorization_out(existing)
    result, owner = await preview_data(session, principal.tenant_id, body, lock=True)
    if result["preview_hash"] != body.preview_hash:
        raise HTTPException(409, "La ejecución o autorización ha cambiado. Revisa de nuevo la comparación antes de confirmar.")
    scope = result["owner"]["scope"]
    row = AiBillingAuthorization(id=aid, tenant_id=principal.tenant_id, **{SCOPE_FIELDS[scope]: owner.id},
        task_type=body.task_type, version=result["authorization_version"] + 1,
        base_profile_hash=result["base_profile_hash"], budget_policy=result["after"],
        request_hash=request_hash, preview_hash=body.preview_hash, created_by=principal.user_id, reason=body.reason.strip())
    session.add(row)
    detail = {"authorization_id": str(aid), "task_type": body.task_type, "version": row.version,
              "before": result["before"], "after": result["after"], "reason": row.reason}
    if scope == "research_run":
        from ..research.runs import append_event
        append_event(session, owner, "billing_renewed", detail, principal.user_id)
        owner.version += 1
    elif scope == "embedding_generation":
        from ..search.indexes import event
        event(session, owner, "billing_renewed", detail, principal.user_id)
        owner.version += 1
    await session.flush()  # The request dependency commits approval and event together.
    return authorization_out(row)
