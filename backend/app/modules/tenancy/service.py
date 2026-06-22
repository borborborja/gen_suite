from __future__ import annotations

import re
import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.security import Principal
from ...models.membership import Membership, MembershipRole
from ...models.tenant import Tenant
from ...models.user import User
from .schemas import MemberOut, TenantOut


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "tenant"


async def create_tenant(
    session: AsyncSession, principal: Principal, name: str, slug: str | None
) -> TenantOut:
    """Create a tenant and make the caller its tenant_admin.

    Runs on an authn-scoped session (app.user_id set), so the caller's own membership
    row passes the RLS WITH CHECK via the user_id branch even before a tenant is active.
    """
    base = _slugify(slug or name)
    candidate, n = base, 1
    while await session.scalar(select(Tenant.id).where(Tenant.slug == candidate)):
        n += 1
        candidate = f"{base}-{n}"

    tenant = Tenant(name=name, slug=candidate)
    session.add(tenant)
    await session.flush()
    session.add(
        Membership(
            tenant_id=tenant.id,
            user_id=principal.user_id,
            role=MembershipRole.tenant_admin.value,
        )
    )
    await session.flush()
    return TenantOut(
        id=tenant.id, name=tenant.name, slug=tenant.slug, plan=tenant.plan, status=tenant.status
    )


async def add_member(
    session: AsyncSession, principal: Principal, email: str, role: MembershipRole
) -> MemberOut:
    user = await session.scalar(select(User).where(User.email == email.strip().lower()))
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no user with that email")
    membership = Membership(
        tenant_id=principal.tenant_id, user_id=user.id, role=role.value
    )
    session.add(membership)
    try:
        await session.flush()
    except IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, "user is already a member")
    return MemberOut(
        user_id=user.id, email=user.email, full_name=user.full_name, role=role.value
    )


async def list_members(session: AsyncSession, principal: Principal) -> list[MemberOut]:
    rows = (
        await session.execute(
            select(User.id, User.email, User.full_name, Membership.role)
            .join(User, User.id == Membership.user_id)
            .where(Membership.tenant_id == principal.tenant_id)
        )
    ).all()
    return [
        MemberOut(user_id=r[0], email=r[1], full_name=r[2], role=r[3]) for r in rows
    ]
