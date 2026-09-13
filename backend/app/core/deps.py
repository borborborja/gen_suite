"""Request dependencies: token decoding, RLS-scoped DB sessions, role guards.

Three session flavours:
  * ``get_db``        — bare session, no RLS GUCs. For login/register/refresh.
  * ``get_authn_db``  — authenticated; sets app.user_id (+ tenant if the token carries one).
  * ``get_tenant_db`` — requires an active tenant; sets all RLS GUCs. Used by tenant modules.

Endpoints/services must NOT commit mid-request: the GUCs are transaction-local
(set_config local=true), so an early commit would drop the tenant context. The session
dependency owns the single commit at the end of the request.
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.rls import set_rls_context
from ..db.session import SessionLocal
from ..models.membership import Membership
from .security import Principal, decode_token

_bearer = HTTPBearer(auto_error=True)


def _client_ip(request: Request) -> str:
    # Behind Cloudflare the real client is in CF-Connecting-IP; fall back to X-Forwarded-For, then
    # the socket peer. Using the real client keeps the rate-limit bucket per-user, not per-proxy.
    cf = request.headers.get("cf-connecting-ip")
    if cf:
        return cf.strip()
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limit(bucket: str, *, limit: int, window_s: int, fail_closed: bool = False) -> Callable[..., Coroutine[Any, Any, None]]:
    """Fixed-window per-IP limiter.

    Authentication and recovery endpoints must fail closed: serving a temporary 503 is
    safer than offering unlimited password guesses while Redis is unavailable. Public
    read-only sharing may still choose availability over throttling.
    """

    async def _dep(request: Request) -> None:
        from .events import get_redis

        key = f"rl:{bucket}:{_client_ip(request)}"
        try:
            r = get_redis()
            n = await r.incr(key)
            if n == 1:
                await r.expire(key, window_s)
        except Exception as exc:
            if fail_closed:
                raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "protección temporalmente no disponible") from exc
            return
        if n > limit:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS, "demasiados intentos; espera un momento"
            )

    return _dep


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_current_principal(
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
) -> Principal:
    # External API: a personal access token (gsk_…) resolves against the api_keys table instead of
    # being decoded as a JWT. Same Authorization: Bearer header.
    if creds.credentials.startswith("gsk_"):
        from ..modules.api_keys.service import resolve_api_key
        principal = await resolve_api_key(creds.credentials)
        if principal is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or revoked API key")
        return principal
    try:
        claims = decode_token(creds.credentials, "access")
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired token")
    tid = claims.get("tid")
    principal = Principal(
        user_id=uuid.UUID(claims["sub"]),
        is_server_admin=bool(claims.get("sa")),
        tenant_id=uuid.UUID(tid) if tid else None,
        role=claims.get("role"),
    )
    if principal.tenant_id is None or principal.is_server_admin:
        return principal
    # JWTs are intentionally short lived, but membership changes (a demotion, removal or owner
    # transfer) are security events and must take effect before their expiry.  Read the current
    # membership under the user RLS branch and replace the token's historical role.
    async with SessionLocal() as session:
        await set_rls_context(
            session, user_id=principal.user_id, tenant_id=principal.tenant_id, role=principal.role
        )
        membership = await session.scalar(select(Membership).where(
            Membership.tenant_id == principal.tenant_id, Membership.user_id == principal.user_id
        ))
    if membership is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "membership is no longer active")
    principal.role = membership.role
    return principal


async def _scoped_session(principal: Principal) -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        await set_rls_context(
            session,
            user_id=principal.user_id,
            tenant_id=principal.tenant_id,
            role=principal.role,
            is_server_admin=principal.is_server_admin,
        )
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_authn_db(
    principal: Principal = Depends(get_current_principal),
) -> AsyncIterator[AsyncSession]:
    async for session in _scoped_session(principal):
        yield session


async def get_tenant_db(
    principal: Principal = Depends(get_current_principal),
) -> AsyncIterator[AsyncSession]:
    if principal.tenant_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no active tenant; switch tenant first")
    async for session in _scoped_session(principal):
        yield session


def require_roles(*roles: str) -> Callable[..., Coroutine[Any, Any, Principal]]:
    """Dependency factory with the workspace-role hierarchy.

    Existing routers name the lowest role that may perform an action (for example ``editor``
    for tree writes).  Owners and admins consequently retain access without every router having
    to repeat their roles.  Legacy tokens remain valid until refresh, but are normalized here.
    """

    ranks = {"guest": 0, "viewer": 1, "contributor": 2, "editor": 3, "admin": 4, "owner": 5}
    aliases = {"tenant_admin": "admin", "researcher": "editor"}
    minimum = min((ranks.get(aliases.get(role, role), 99) for role in roles), default=99)

    async def _dep(principal: Principal = Depends(get_current_principal)) -> Principal:
        if principal.is_server_admin:
            return principal
        actual = aliases.get(principal.role or "", principal.role or "")
        if ranks.get(actual, -1) < minimum:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "insufficient role")
        return principal

    return _dep


async def require_server_admin(
    principal: Principal = Depends(get_current_principal),
) -> Principal:
    if not principal.is_server_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "server admin required")
    return principal
