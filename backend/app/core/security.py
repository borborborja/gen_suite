"""Password hashing (Argon2id) and JWT issue/verify."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error

from ..settings import settings

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except Argon2Error:
        return False


@dataclass
class Principal:
    """The authenticated caller, derived from an access token."""

    user_id: uuid.UUID
    is_server_admin: bool = False
    tenant_id: uuid.UUID | None = None
    role: str | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_access_token(principal: Principal) -> str:
    now = _now()
    claims = {
        "sub": str(principal.user_id),
        "sa": principal.is_server_admin,
        "tid": str(principal.tenant_id) if principal.tenant_id else None,
        "role": principal.role,
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.access_token_ttl_minutes)).timestamp()),
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_refresh_token(principal: Principal) -> tuple[str, uuid.UUID, datetime]:
    """Refresh tokens embed the active tenant/role so rotation preserves context."""
    now = _now()
    jti = uuid.uuid4()
    expires_at = now + timedelta(days=settings.refresh_token_ttl_days)
    claims = {
        "sub": str(principal.user_id),
        "sa": principal.is_server_admin,
        "tid": str(principal.tenant_id) if principal.tenant_id else None,
        "role": principal.role,
        "type": "refresh",
        "jti": str(jti),
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    token = jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, jti, expires_at


def decode_token(token: str, expected_type: str) -> dict:
    claims = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    if claims.get("type") != expected_type:
        raise jwt.InvalidTokenError(f"expected {expected_type} token")
    return claims
