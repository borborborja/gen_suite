from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.deps import get_current_principal, get_tenant_db, require_roles
from ...core.queue import get_queue
from ...core.security import Principal
from ...models.job import Job
from ...models.ai_call import AiCall
from ...models.membership import MembershipRole
from ...models.tenant import Tenant
from ..jobs.schemas import JobOut
from . import service
from .catalog import PROVIDER_CATALOG
from .authorizations import router as authorizations_router
from .schemas import (
    BindingOut,
    BindingUpsert,
    CredentialCreate,
    CredentialOut,
    ProviderCatalogEntry,
)

router = APIRouter(prefix="/providers", tags=["providers"])
router.include_router(authorizations_router)

_ADMIN = (MembershipRole.tenant_admin.value,)


@router.post(
    "/reembed-corpus", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_roles(*_ADMIN))],
)
async def reembed_corpus(
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> JobOut:
    """Legacy entry point: build a separate generation, retaining the active index.
    Activation remains an explicit, audited action in the generation API."""
    from ..jobs.service import active_job_of_type
    existing = await active_job_of_type(db, principal.tenant_id, "reembed_corpus")
    if existing:
        return JobOut(
            id=existing.id, type=existing.type, status=existing.status, progress=existing.progress,
            result=existing.result, error=existing.error, created_at=existing.created_at,
            started_at=existing.started_at, finished_at=existing.finished_at,
        )
    from ..search.index_router import GenerationCreate, create_generation
    generation = await create_generation(db, principal, GenerationCreate(client_id=uuid.uuid4(), authorize=True))
    job = await db.get(Job, generation.job_id)
    return JobOut(
        id=job.id, type=job.type, status=job.status, progress=job.progress, result=job.result,
        error=job.error, created_at=job.created_at, started_at=job.started_at, finished_at=job.finished_at,
    )


def _cred_out(cred) -> CredentialOut:
    return CredentialOut(
        id=cred.id, scope=cred.scope, tenant_id=cred.tenant_id, provider_key=cred.provider_key,
        label=cred.label, base_url=cred.base_url, model_default=cred.model_default,
        key_masked=service.masked(cred), is_active=cred.is_active, created_at=cred.created_at,
    )


@router.get("/catalog", response_model=list[ProviderCatalogEntry])
async def catalog() -> list[ProviderCatalogEntry]:
    return [ProviderCatalogEntry(key=k, **v) for k, v in PROVIDER_CATALOG.items()]


@router.get("/credentials", response_model=list[CredentialOut])
async def list_credentials(
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> list[CredentialOut]:
    creds = await service.list_credentials(db, principal.tenant_id, principal.is_server_admin)
    return [_cred_out(c) for c in creds]


@router.post(
    "/credentials", response_model=CredentialOut, status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(*_ADMIN))],
)
async def create_credential(
    body: CredentialCreate,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> CredentialOut:
    cred = await service.create_credential(
        db, tenant_id=principal.tenant_id, is_server_admin=principal.is_server_admin,
        created_by=principal.user_id, scope=body.scope, provider_key=body.provider_key,
        label=body.label, base_url=body.base_url, model_default=body.model_default,
        api_key=body.api_key,
    )
    return _cred_out(cred)


@router.delete(
    "/credentials/{cred_id}", status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_roles(*_ADMIN))],
)
async def delete_credential(
    cred_id: uuid.UUID, db: AsyncSession = Depends(get_tenant_db)
) -> None:
    await service.delete_credential(db, cred_id)


@router.get("/bindings", response_model=list[BindingOut])
async def list_bindings(
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> list[BindingOut]:
    return [
        BindingOut(
            id=b.id, task_type=b.task_type, credential_id=b.credential_id, model=b.model, params=b.params
        )
        for b in await service.list_bindings(db, principal.tenant_id)
    ]


@router.put(
    "/bindings", response_model=BindingOut, dependencies=[Depends(require_roles(*_ADMIN))]
)
async def upsert_binding(
    body: BindingUpsert,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> BindingOut:
    b = await service.upsert_binding(
        db, tenant_id=principal.tenant_id, task_type=body.task_type,
        credential_id=body.credential_id, model=body.model, params=body.params,
    )
    return BindingOut(id=b.id, task_type=b.task_type, credential_id=b.credential_id, model=b.model, params=b.params)


# ── Spending control ──────────────────────────────────────────────────────────────────────────
class SpendOut(BaseModel):
    month_cents: int            # AI spend so far this calendar month (USD cents, estimated)
    budget_cents: int | None    # monthly cap, or null if none
    reserved_cents: int = 0
    settled_microusd: int = 0
    reserved_microusd: int = 0
    unknown_calls: int = 0


class BudgetUpdate(BaseModel):
    monthly_budget_cents: int | None = Field(ge=0, le=100_000_000, strict=True)


async def _spend(db, tenant_id):
    from .admission import totals
    balance = await totals(db, tenant_id)
    budget = await db.scalar(select(Tenant.monthly_budget_cents).where(Tenant.id == tenant_id))
    return SpendOut(month_cents=(balance["settled_microusd"] + 9999) // 10000,
        reserved_cents=(balance["reserved_microusd"] + 9999) // 10000, budget_cents=budget, **balance)


@router.get("/spend", response_model=SpendOut)
async def get_spend(
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> SpendOut:
    return await _spend(db, principal.tenant_id)


@router.put("/budget", response_model=SpendOut, dependencies=[Depends(require_roles(*_ADMIN))])
async def set_budget(
    body: BudgetUpdate,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> SpendOut:
    await db.execute(update(Tenant).where(Tenant.id == principal.tenant_id).values(
        monthly_budget_cents=body.monthly_budget_cents))
    return await _spend(db, principal.tenant_id)


class AiCallOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    job_id: uuid.UUID | None
    task_type: str
    state: str
    profile: dict
    reserved_microusd: int
    cost_microusd: int | None
    provider_request_id: str | None
    error_code: str | None
    created_at: datetime
    settled_at: datetime | None
    reconciliation: dict | None


@router.get("/ai-calls", response_model=list[AiCallOut], dependencies=[Depends(require_roles(*_ADMIN))])
async def list_ai_calls(
    state: str | None = Query(None, pattern="^(inflight|unknown|succeeded|reconciled)$"),
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
    principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_tenant_db),
):
    stmt = select(AiCall).where(AiCall.tenant_id == principal.tenant_id)
    if state:
        stmt = stmt.where(AiCall.state == state)
    return list((await db.scalars(stmt.order_by(AiCall.created_at.desc(), AiCall.id).offset(offset).limit(limit))).all())


class ReconcileCall(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cost_microusd: int = Field(ge=0, le=100_000_000_000, strict=True)
    evidence: str = Field(min_length=10, max_length=2000)
    provider_request_id: str | None = Field(None, max_length=256)


@router.post("/ai-calls/{call_id}/reconcile", response_model=AiCallOut,
             dependencies=[Depends(require_roles(*_ADMIN))])
async def reconcile_ai_call(
    call_id: uuid.UUID, body: ReconcileCall,
    principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_tenant_db),
):
    await db.scalar(select(Tenant.id).where(Tenant.id == principal.tenant_id).with_for_update(key_share=True))
    call = await db.scalar(select(AiCall).where(AiCall.id == call_id,
        AiCall.tenant_id == principal.tenant_id).with_for_update())
    if not call:
        raise HTTPException(404, "Llamada no encontrada.")
    from .response_guard import review_call
    await review_call(db, call)
    if call.state not in ("unknown", "inflight"):
        raise HTTPException(409, "La llamada ya tiene una liquidación; no se sobrescribe el historial.")
    if call.state == "inflight" and call.created_at > datetime.now(timezone.utc) - timedelta(minutes=15):
        raise HTTPException(409, "La llamada aún puede estar en ejecución. Espera antes de conciliarla.")
    call.cost_microusd = body.cost_microusd
    call.provider_request_id = body.provider_request_id or call.provider_request_id
    call.state = "reconciled"
    call.settled_at = datetime.now(timezone.utc)
    call.reconciliation = {"by": str(principal.user_id), "evidence": body.evidence,
                           "at": call.settled_at.isoformat(), "previous_error": call.error_code}
    await db.flush()
    return call


@router.post("/ai-calls/{call_id}/recover", response_model=AiCallOut,
             dependencies=[Depends(require_roles(*_ADMIN))])
async def recover_ai_response(
    call_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_tenant_db),
):
    """Recover the window S3-success/DB-failure, without contacting the billed provider."""
    from .admission import BudgetPolicy, read_saved_response, validate_usage
    call = await db.scalar(select(AiCall).where(AiCall.id == call_id, AiCall.tenant_id == principal.tenant_id))
    if not call:
        raise HTTPException(404, "Llamada no encontrada.")
    from .response_guard import review_call
    await review_call(db, call)
    if call.state not in ("unknown", "inflight"):
        raise HTTPException(409, "La llamada ya está liquidada.")
    if call.state == "inflight" and call.created_at > datetime.now(timezone.utc) - timedelta(minutes=15):
        raise HTTPException(409, "La llamada todavía puede estar ejecutándose.")
    key = f"{principal.tenant_id}/ai-calls/{call.id}/response.json"
    try:
        envelope, checksum = await read_saved_response(key)
        cost = validate_usage(envelope["usage"], BudgetPolicy.model_validate(call.profile["budget_policy"]))
        if "data" not in envelope:
            raise ValueError("missing_data")
    except Exception:
        raise HTTPException(409, "No hay una respuesta guardada verificable. Conserva la reserva y concilia con evidencia del proveedor.") from None
    await db.scalar(select(Tenant.id).where(Tenant.id == principal.tenant_id).with_for_update(key_share=True))
    changed = await db.execute(update(AiCall).where(AiCall.id == call_id, AiCall.tenant_id == principal.tenant_id,
        AiCall.state.in_(("unknown", "inflight"))).values(state="succeeded", cost_microusd=cost,
            usage=envelope["usage"], provider_request_id=envelope["usage"].get("request_id"),
            result_key=key, result_checksum=checksum, error_code=envelope.get("error"),
            settled_at=datetime.now(timezone.utc), reconciliation={"by": str(principal.user_id), "recovered_from_private_storage": True}))
    if not changed.rowcount:
        raise HTTPException(409, "Otra operación acaba de liquidar esta llamada.")
    await db.refresh(call)
    return call
