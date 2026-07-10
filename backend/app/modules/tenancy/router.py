from __future__ import annotations

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.deps import get_authn_db, get_current_principal, get_tenant_db, require_roles
from ...core.security import Principal
from ...models.membership import MembershipRole
from . import service
from .reset import reset_tenant_data
from .schemas import AddMemberRequest, CreateTenantRequest, MemberOut, TenantOut

router = APIRouter(prefix="/tenants", tags=["tenancy"])


class ResetRequest(BaseModel):
    scope: str


@router.post("/reset", dependencies=[Depends(require_roles(MembershipRole.tenant_admin.value))])
async def reset_data(
    body: ResetRequest,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> dict:
    """Destructive: wipe the tenant's tree | library | discoveries | all (Ajustes danger zone).
    Scope goes in the body, like every other mutating POST."""
    return await reset_tenant_data(db, principal.tenant_id, body.scope)


@router.post("", response_model=TenantOut, status_code=status.HTTP_201_CREATED)
async def create_tenant(
    body: CreateTenantRequest,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_authn_db),
) -> TenantOut:
    return await service.create_tenant(db, principal, body.name, body.slug)


@router.get("/members", response_model=list[MemberOut])
async def list_members(
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> list[MemberOut]:
    return await service.list_members(db, principal)


@router.post(
    "/members",
    response_model=MemberOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(MembershipRole.tenant_admin.value))],
)
async def add_member(
    body: AddMemberRequest,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> MemberOut:
    return await service.add_member(db, principal, body.email, body.role)
