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


_ROLE_RANK = {"guest": 0, "viewer": 1, "contributor": 2, "editor": 3, "admin": 4, "owner": 5}


def _normalized_role(value: str | None) -> str:
    return {"tenant_admin": "admin", "researcher": "editor"}.get(value or "", value or "guest")


def _can_manage(principal: Principal, current: str | None, requested: str | None = None) -> None:
    """Owners administer ownership; admins administer everyone below admin."""
    actor = _normalized_role(principal.role)
    if actor == "owner":
        return
    if actor != "admin" or _ROLE_RANK.get(_normalized_role(current), 99) >= _ROLE_RANK["admin"] or (
        requested is not None and _ROLE_RANK.get(_normalized_role(requested), 99) >= _ROLE_RANK["admin"]
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "solo una persona propietaria puede administrar roles de administración u ownership")


async def _cannot_orphan_owner(session: AsyncSession, tenant_id: uuid.UUID, membership: Membership, next_role: str | None) -> None:
    if _normalized_role(membership.role) != "owner" or _normalized_role(next_role) == "owner":
        return
    other_owner = await session.scalar(select(Membership.user_id).where(
        Membership.tenant_id == tenant_id, Membership.role == MembershipRole.owner.value,
        Membership.user_id != membership.user_id,
    ))
    if not other_owner:
        raise HTTPException(status.HTTP_409_CONFLICT, "el espacio debe conservar al menos una persona propietaria")


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "tenant"


async def create_tenant(
    session: AsyncSession, principal: Principal, name: str, slug: str | None
) -> TenantOut:
    """Create a tenant and make the caller its owner.

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
            role=MembershipRole.owner.value,
        )
    )
    await session.flush()
    return TenantOut(
        id=tenant.id, name=tenant.name, slug=tenant.slug, plan=tenant.plan, status=tenant.status
    )


async def add_member(
    session: AsyncSession, principal: Principal, email: str, role: MembershipRole
) -> MemberOut:
    _can_manage(principal, None, role.value)
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


async def update_member_role(
    session: AsyncSession, principal: Principal, user_id: uuid.UUID, role: MembershipRole
) -> MemberOut:
    membership = await session.scalar(select(Membership).where(
        Membership.tenant_id == principal.tenant_id, Membership.user_id == user_id
    ))
    if not membership:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "miembro no encontrado")
    _can_manage(principal, membership.role, role.value)
    await _cannot_orphan_owner(session, principal.tenant_id, membership, role.value)
    membership.role = role.value
    await session.flush()
    user = await session.get(User, user_id)
    return MemberOut(user_id=user_id, email=user.email, full_name=user.full_name, role=membership.role)


async def remove_member(session: AsyncSession, principal: Principal, user_id: uuid.UUID) -> None:
    membership = await session.scalar(select(Membership).where(
        Membership.tenant_id == principal.tenant_id, Membership.user_id == user_id
    ))
    if not membership:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "miembro no encontrado")
    _can_manage(principal, membership.role)
    await _cannot_orphan_owner(session, principal.tenant_id, membership, None)
    await session.delete(membership)
    await session.flush()


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
