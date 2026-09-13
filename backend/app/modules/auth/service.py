from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone

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
from ...core.crypto import get_secret_box
from ...core.mail import send_transactional
from ...db.rls import set_rls_context
from ...settings import settings
from ...models.membership import Membership
from ...models.tenant import Tenant
from ...models.user import AccessRequest, AccountToken, RefreshToken, User, UserMfa
from .schemas import AccessRequestOut, MembershipOut, MeOut, TokenPair, UserOut


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _code_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _totp(secret: str, counter: int) -> str:
    raw = base64.b32decode(secret + "=" * (-len(secret) % 8))
    digest = hmac.new(raw, counter.to_bytes(8, "big"), hashlib.sha1).digest()
    offset = digest[-1] & 15
    value = (int.from_bytes(digest[offset:offset + 4], "big") & 0x7fffffff) % 1_000_000
    return f"{value:06d}"


def _valid_totp(secret: str, code: str) -> bool:
    now = int(time.time() // 30)
    normalized = code.strip().replace(" ", "")
    return any(hmac.compare_digest(_totp(secret, now + delta), normalized) for delta in (-1, 0, 1))


async def _consume_token(session: AsyncSession, purpose: str, raw: str, *, email: str | None = None) -> AccountToken:
    row = await session.scalar(select(AccountToken).where(
        AccountToken.purpose == purpose, AccountToken.token_hash == _token_hash(raw),
    ))
    if not row or row.consumed_at is not None or row.expires_at <= _now() or (email and row.email != email):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "el enlace no es válido o ha caducado")
    row.consumed_at = _now()
    return row


async def _issue_token(session: AsyncSession, purpose: str, email: str, *, user_id: uuid.UUID | None = None,
                       created_by: uuid.UUID | None = None, lifetime: timedelta) -> str:
    raw = secrets.token_urlsafe(32)
    session.add(AccountToken(
        purpose=purpose, email=email, token_hash=_token_hash(raw), user_id=user_id,
        created_by=created_by, expires_at=_now() + lifetime,
    ))
    await session.flush()
    return raw


def _link(path: str, token: str) -> str:
    return f"{settings.frontend_public_url.rstrip('/')}{path}?token={token}"


async def _issue_tokens(session: AsyncSession, principal: Principal) -> TokenPair:
    access = create_access_token(principal)
    refresh, jti, expires_at = create_refresh_token(principal)
    session.add(RefreshToken(jti=jti, user_id=principal.user_id, expires_at=expires_at))
    return TokenPair(access_token=access, refresh_token=refresh)


async def register_user(
    session: AsyncSession, email: str, password: str, full_name: str | None, invite_token: str | None = None,
) -> TokenPair:
    email = email.strip().lower()
    if await session.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "email already registered")
    # The very first user bootstraps the server administrator — but only when explicitly enabled,
    # so an internet-exposed fresh deploy can't be admin-claimed by whoever registers first. The
    # operator sets ALLOW_FIRST_USER_ADMIN=true for initial setup, then turns it back off.
    # Serialized via an advisory xact lock so two concurrent first registrations can't BOTH see
    # count==0 and both be promoted (check-then-act race).
    if settings.allow_first_user_admin:
        from sqlalchemy import text
        await session.execute(text("SELECT pg_advisory_xact_lock(hashtext('first_user_admin'))"))
    is_first = (await session.scalar(select(func.count()).select_from(User))) == 0
    grant_admin = is_first and settings.allow_first_user_admin
    invite_required = settings.invitation_only and settings.environment.lower() != "development" and not grant_admin
    if invite_required and not invite_token:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "necesitas una invitación aprobada para crear una cuenta")
    if invite_token:
        await _consume_token(session, "invite", invite_token, email=email)
    user = User(
        email=email,
        password_hash=hash_password(password),
        full_name=full_name,
        is_server_admin=grant_admin,
        email_verified_at=_now() if invite_token or grant_admin or settings.environment.lower() == "development" else None,
    )
    session.add(user)
    await session.flush()
    return await _issue_tokens(session, Principal(user_id=user.id, is_server_admin=grant_admin))


async def login(session: AsyncSession, email: str, password: str, mfa_code: str | None = None) -> TokenPair:
    email = email.strip().lower()
    user = await session.scalar(select(User).where(User.email == email))
    if not user or not user.is_active or not verify_password(password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
    mfa = await session.get(UserMfa, user.id)
    if mfa and mfa.enabled:
        secret = get_secret_box().decrypt(mfa.secret_ciphertext, mfa.secret_nonce)
        normalized = (mfa_code or "").strip().replace(" ", "")
        recovery = next((value for value in mfa.recovery_code_hashes if hmac.compare_digest(value, _code_hash(normalized))), None)
        if _valid_totp(secret, normalized):
            pass
        elif recovery:
            mfa.recovery_code_hashes = [value for value in mfa.recovery_code_hashes if value != recovery]
        else:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "se requiere un código de autenticación")
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


async def request_access(session: AsyncSession, email: str, full_name: str | None) -> None:
    """Queue a request without exposing whether a prior request/account exists."""
    normalized = email.strip().lower()
    row = await session.scalar(select(AccessRequest).where(AccessRequest.email == normalized))
    if row:
        if row.status in {"rejected", "revoked"}:
            row.status, row.full_name, row.reviewed_at, row.reviewed_by = "pending", full_name, None, None
        await session.flush()
        return
    session.add(AccessRequest(email=normalized, full_name=full_name))
    await session.flush()


async def list_access_requests(session: AsyncSession) -> list[AccessRequestOut]:
    rows = (await session.scalars(select(AccessRequest).order_by(AccessRequest.created_at.desc()))).all()
    return [AccessRequestOut(id=row.id, email=row.email, full_name=row.full_name, status=row.status) for row in rows]


async def approve_access_request(session: AsyncSession, request_id: uuid.UUID, reviewer_id: uuid.UUID) -> None:
    row = await session.get(AccessRequest, request_id)
    if not row or row.status != "pending":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "solicitud pendiente no encontrada")
    token = await _issue_token(session, "invite", row.email, created_by=reviewer_id, lifetime=timedelta(hours=24))
    row.status, row.reviewed_by, row.reviewed_at = "approved", reviewer_id, _now()
    await send_transactional(
        row.email, "Tu invitación a Gen Suite",
        "Tu acceso ha sido aprobado. Crea tu cuenta en las próximas 24 horas:\n\n"
        + _link("/", token),
    )


async def request_password_reset(session: AsyncSession, email: str) -> None:
    user = await session.scalar(select(User).where(User.email == email.strip().lower()))
    if not user or not user.is_active:
        return
    token = await _issue_token(session, "reset", user.email, user_id=user.id, lifetime=timedelta(minutes=30))
    await send_transactional(
        user.email, "Restablece tu contraseña de Gen Suite",
        "Usa este enlace una sola vez durante los próximos 30 minutos:\n\n" + _link("/reset", token),
    )


async def reset_password(session: AsyncSession, raw_token: str, new_password: str) -> None:
    token = await _consume_token(session, "reset", raw_token)
    user = await session.get(User, token.user_id)
    if not user:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "el enlace no es válido o ha caducado")
    user.password_hash = hash_password(new_password)
    for refresh in (await session.scalars(select(RefreshToken).where(
        RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None)
    ))).all():
        refresh.revoked_at = _now()
    await session.flush()


async def begin_mfa(session: AsyncSession, user_id: uuid.UUID, email: str) -> str:
    secret = base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")
    ciphertext, nonce = get_secret_box().encrypt(secret)
    row = await session.get(UserMfa, user_id)
    if row:
        if row.enabled:
            raise HTTPException(status.HTTP_409_CONFLICT, "MFA ya está activado; desactívalo antes de configurarlo de nuevo")
        row.secret_ciphertext, row.secret_nonce, row.enabled, row.recovery_code_hashes = ciphertext, nonce, False, []
    else:
        session.add(UserMfa(user_id=user_id, secret_ciphertext=ciphertext, secret_nonce=nonce))
    await session.flush()
    label = f"Gen Suite:{email}"
    return f"otpauth://totp/{label}?secret={secret}&issuer=Gen%20Suite&period=30&digits=6"


async def confirm_mfa(session: AsyncSession, user_id: uuid.UUID, code: str) -> list[str]:
    row = await session.get(UserMfa, user_id)
    if not row:
        raise HTTPException(status.HTTP_409_CONFLICT, "inicia primero la configuración de MFA")
    secret = get_secret_box().decrypt(row.secret_ciphertext, row.secret_nonce)
    if not _valid_totp(secret, code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "el código de autenticación no es válido")
    codes = [secrets.token_urlsafe(6) for _ in range(8)]
    row.enabled, row.recovery_code_hashes = True, [_code_hash(value) for value in codes]
    await session.flush()
    return codes


async def disable_mfa(session: AsyncSession, user_id: uuid.UUID, password: str, code: str) -> None:
    user = await session.get(User, user_id)
    row = await session.get(UserMfa, user_id)
    if not user or not row or not row.enabled or not verify_password(password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no se pudo desactivar MFA")
    secret = get_secret_box().decrypt(row.secret_ciphertext, row.secret_nonce)
    if not _valid_totp(secret, code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "el código de autenticación no es válido")
    await session.delete(row)
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
