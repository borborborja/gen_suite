from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.deps import get_authn_db, get_current_principal, get_tenant_db, require_roles
from ...core.security import Principal
from ...models.membership import MembershipRole
from . import service
from .reset import reset_tenant_data
from .schemas import AddMemberRequest, CreateTenantRequest, MemberOut, TenantOut

router = APIRouter(prefix="/tenants", tags=["tenancy"])


@router.post("/reset", dependencies=[Depends(require_roles(MembershipRole.tenant_admin.value))])
async def reset_data(
    scope: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> dict:
    """Destructive: wipe the tenant's tree | library | discoveries | all (Ajustes danger zone)."""
    return await reset_tenant_data(db, principal.tenant_id, scope)


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
