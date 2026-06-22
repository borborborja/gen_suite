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
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.rls import set_rls_context
from ..db.session import SessionLocal
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


def rate_limit(bucket: str, *, limit: int, window_s: int) -> Callable[..., Coroutine[Any, Any, None]]:
    """Fixed-window per-IP limiter on the shared Redis. Fail-open if Redis is unreachable so an
    outage never locks legitimate users out. Use on unauthenticated endpoints (login/register)."""

    async def _dep(request: Request) -> None:
        from .events import get_redis

        key = f"rl:{bucket}:{_client_ip(request)}"
        try:
            r = get_redis()
            n = await r.incr(key)
            if n == 1:
                await r.expire(key, window_s)
        except Exception:
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
    return Principal(
        user_id=uuid.UUID(claims["sub"]),
        is_server_admin=bool(claims.get("sa")),
        tenant_id=uuid.UUID(tid) if tid else None,
        role=claims.get("role"),
    )


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
    """Dependency factory: allow server-admins, or members holding one of ``roles``."""

    async def _dep(principal: Principal = Depends(get_current_principal)) -> Principal:
        if principal.is_server_admin:
            return principal
        if principal.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "insufficient role")
        return principal

    return _dep


async def require_server_admin(
    principal: Principal = Depends(get_current_principal),
) -> Principal:
    if not principal.is_server_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "server admin required")
    return principal
