"""External-API personal access tokens: generate, list, revoke, and resolve to a Principal.

Token format: ``gsk_<urlsafe-random>``. Only the SHA-256 hash is stored; the plaintext is returned
once at creation. Read scope binds the token to the viewer role (GETs only); write scope inherits the
creator's tenant role.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.security import Principal
from ...db.session import SessionLocal
from ...models.api_key import ApiKey


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _generate() -> tuple[str, str, str]:
    token = f"gsk_{secrets.token_urlsafe(32)}"
    return token, token[:12], _hash(token)


async def create_key(
    session: AsyncSession, principal: Principal, *, name: str, scope: str, expires_days: int | None,
) -> tuple[ApiKey, str]:
    if principal.tenant_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no active tenant")
    scope = scope if scope in ("read", "write") else "read"
    role = "viewer" if scope == "read" else (principal.role or "researcher")
    token, prefix, token_hash = _generate()
    expires_at = (
        datetime.now(timezone.utc) + timedelta(days=expires_days) if expires_days else None
    )
    key = ApiKey(
        user_id=principal.user_id, tenant_id=principal.tenant_id, name=name.strip()[:128] or "token",
        role=role, scope=scope, token_prefix=prefix, token_hash=token_hash, expires_at=expires_at,
    )
    session.add(key)
    await session.flush()
    return key, token


async def list_keys(session: AsyncSession, user_id: uuid.UUID) -> list[ApiKey]:
    return list((await session.scalars(
        select(ApiKey).where(ApiKey.user_id == user_id).order_by(ApiKey.created_at.desc())
    )).all())


async def revoke_key(session: AsyncSession, user_id: uuid.UUID, key_id: uuid.UUID) -> None:
    key = await session.get(ApiKey, key_id)
    if not key or key.user_id != user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "key not found")
    if key.revoked_at is None:
        key.revoked_at = datetime.now(timezone.utc)


async def resolve_api_key(token: str) -> Principal | None:
    """Resolve a ``gsk_`` token to a Principal (own bare session — runs before tenant context exists).
    Validates not revoked / not expired and bumps last_used_at. Returns None if invalid."""
    token_hash = _hash(token)
    async with SessionLocal() as session:
        key = await session.scalar(select(ApiKey).where(ApiKey.token_hash == token_hash))
        if not key or key.revoked_at is not None:
            return None
        if key.expires_at is not None and key.expires_at < datetime.now(timezone.utc):
            return None
        key.last_used_at = datetime.now(timezone.utc)
        try:
            await session.commit()
        except Exception:
            await session.rollback()
        return Principal(
            user_id=key.user_id, is_server_admin=False, tenant_id=key.tenant_id, role=key.role,
        )
