from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.deps import get_current_principal, get_tenant_db, require_roles
from ...core.queue import get_queue
from ...core.security import Principal
from ...models.job import Job
from ...models.membership import MembershipRole
from ...models.tenant import Tenant
from ..jobs.schemas import JobOut
from . import service
from .catalog import PROVIDER_CATALOG
from .schemas import (
    BindingOut,
    BindingUpsert,
    CredentialCreate,
    CredentialOut,
    ProviderCatalogEntry,
)

router = APIRouter(prefix="/providers", tags=["providers"])

_ADMIN = (MembershipRole.tenant_admin.value,)


@router.post(
    "/reembed-corpus", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_roles(*_ADMIN))],
)
async def reembed_corpus(
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> JobOut:
    """Re-embed the whole corpus with the currently-bound embedding model. Run this after switching
    the `embedding` provider — vectors from different models aren't comparable, so the old ones must
    be recomputed or vector search breaks."""
    job = Job(
        tenant_id=principal.tenant_id, type="reembed_corpus", status="queued",
        params={}, created_by=principal.user_id,
    )
    db.add(job)
    await db.flush()
    queue = await get_queue()
    await queue.enqueue_job("reembed_corpus", job_id=str(job.id), tenant_id=str(principal.tenant_id))
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


class BudgetUpdate(BaseModel):
    monthly_budget_cents: int | None  # null clears the cap


@router.get("/spend", response_model=SpendOut)
async def get_spend(
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> SpendOut:
    spent = await service.month_spend_cents(db, principal.tenant_id)
    budget = await db.scalar(select(Tenant.monthly_budget_cents).where(Tenant.id == principal.tenant_id))
    return SpendOut(month_cents=spent, budget_cents=budget)


@router.put("/budget", response_model=SpendOut, dependencies=[Depends(require_roles(*_ADMIN))])
async def set_budget(
    body: BudgetUpdate,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> SpendOut:
    await db.execute(update(Tenant).where(Tenant.id == principal.tenant_id).values(
        monthly_budget_cents=body.monthly_budget_cents))
    spent = await service.month_spend_cents(db, principal.tenant_id)
    return SpendOut(month_cents=spent, budget_cents=body.monthly_budget_cents)
