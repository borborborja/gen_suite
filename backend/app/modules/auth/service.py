from __future__ import annotations

import uuid
from datetime import datetime, timezone

import jwt
from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.security import (
    Principal,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from ...db.rls import set_rls_context
from ...settings import settings
from ...models.membership import Membership
from ...models.tenant import Tenant
from ...models.user import RefreshToken, User
from .schemas import MembershipOut, MeOut, TokenPair, UserOut


async def _issue_tokens(session: AsyncSession, principal: Principal) -> TokenPair:
    access = create_access_token(principal)
    refresh, jti, expires_at = create_refresh_token(principal)
    session.add(RefreshToken(jti=jti, user_id=principal.user_id, expires_at=expires_at))
    return TokenPair(access_token=access, refresh_token=refresh)


async def register_user(
    session: AsyncSession, email: str, password: str, full_name: str | None
) -> TokenPair:
    email = email.strip().lower()
    if await session.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "email already registered")
    # The very first user bootstraps the server administrator — but only when explicitly enabled,
    # so an internet-exposed fresh deploy can't be admin-claimed by whoever registers first. The
    # operator sets ALLOW_FIRST_USER_ADMIN=true for initial setup, then turns it back off.
    is_first = (await session.scalar(select(func.count()).select_from(User))) == 0
    grant_admin = is_first and settings.allow_first_user_admin
    user = User(
        email=email,
        password_hash=hash_password(password),
        full_name=full_name,
        is_server_admin=grant_admin,
    )
    session.add(user)
    await session.flush()
    return await _issue_tokens(session, Principal(user_id=user.id, is_server_admin=grant_admin))


async def login(session: AsyncSession, email: str, password: str) -> TokenPair:
    email = email.strip().lower()
    user = await session.scalar(select(User).where(User.email == email))
    if not user or not user.is_active or not verify_password(password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
    user.last_login_at = datetime.now(timezone.utc)

    # Enable the user_id RLS branch so we can read this user's memberships.
    await set_rls_context(session, user_id=user.id)
    principal = Principal(user_id=user.id, is_server_admin=user.is_server_admin)
    memberships = (
        await session.scalars(select(Membership).where(Membership.user_id == user.id))
    ).all()
    if len(memberships) == 1:  # auto-select when unambiguous
        principal.tenant_id = memberships[0].tenant_id
        principal.role = memberships[0].role
    return await _issue_tokens(session, principal)


async def rotate(session: AsyncSession, refresh_token: str) -> TokenPair:
    try:
        claims = decode_token(refresh_token, "refresh")
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid refresh token")
    row = await session.scalar(
        select(RefreshToken).where(RefreshToken.jti == uuid.UUID(claims["jti"]))
    )
    if not row or row.revoked_at is not None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "refresh token revoked or unknown")
    row.revoked_at = datetime.now(timezone.utc)  # rotation: single-use
    tid = claims.get("tid")
    principal = Principal(
        user_id=uuid.UUID(claims["sub"]),
        is_server_admin=bool(claims.get("sa")),
        tenant_id=uuid.UUID(tid) if tid else None,
        role=claims.get("role"),
    )
    return await _issue_tokens(session, principal)


async def revoke(session: AsyncSession, refresh_token: str) -> None:
    try:
        claims = decode_token(refresh_token, "refresh")
    except jwt.PyJWTError:
        return
    row = await session.scalar(
        select(RefreshToken).where(RefreshToken.jti == uuid.UUID(claims["jti"]))
    )
    if row and row.revoked_at is None:
        row.revoked_at = datetime.now(timezone.utc)


async def switch_tenant(
    session: AsyncSession, principal: Principal, tenant_id: uuid.UUID
) -> TokenPair:
    membership = await session.scalar(
        select(Membership).where(
            Membership.user_id == principal.user_id, Membership.tenant_id == tenant_id
        )
    )
    if not membership:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not a member of this tenant")
    return await _issue_tokens(
        session,
        Principal(
            user_id=principal.user_id,
            is_server_admin=principal.is_server_admin,
            tenant_id=tenant_id,
            role=membership.role,
        ),
    )


async def update_me(session: AsyncSession, principal: Principal, *,
                    full_name: str | None, email: str | None) -> MeOut:
    user = await session.get(User, principal.user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "user not found")
    if full_name is not None:
        user.full_name = full_name or None
    if email is not None:
        new_email = email.strip().lower()
        if new_email != user.email:
            taken = await session.scalar(
                select(User.id).where(User.email == new_email, User.id != user.id))
            if taken:
                raise HTTPException(status.HTTP_409_CONFLICT, "email already registered")
            user.email = new_email
    await session.flush()
    return await get_me(session, principal)


async def change_password(session: AsyncSession, principal: Principal, *,
                          current_password: str, new_password: str) -> None:
    user = await session.get(User, principal.user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "user not found")
    if not verify_password(current_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "current password is incorrect")
    user.password_hash = hash_password(new_password)
    # invalidate other sessions: revoke all refresh tokens for this user
    rows = (await session.scalars(
        select(RefreshToken).where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None)))).all()
    for r in rows:
        r.revoked_at = datetime.now(timezone.utc)
    await session.flush()


async def get_me(session: AsyncSession, principal: Principal) -> MeOut:
    user = await session.get(User, principal.user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "user not found")
    rows = (
        await session.execute(
            select(Membership.tenant_id, Tenant.name, Tenant.slug, Membership.role)
            .join(Tenant, Tenant.id == Membership.tenant_id)
            .where(Membership.user_id == principal.user_id)
        )
    ).all()
    return MeOut(
        user=UserOut(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            is_server_admin=user.is_server_admin,
        ),
        active_tenant_id=principal.tenant_id,
        active_role=principal.role,
        memberships=[
            MembershipOut(tenant_id=r[0], tenant_name=r[1], tenant_slug=r[2], role=r[3])
            for r in rows
        ],
    )
