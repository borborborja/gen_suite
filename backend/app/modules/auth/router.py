from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.deps import get_authn_db, get_current_principal, get_db, rate_limit
from ...core.security import Principal
from ...settings import settings
from . import service
from .schemas import (
    AccessRequestIn, AccessRequestOut, AccessTokenOut, ChangePasswordRequest, LoginRequest, MeOut,
    MfaCodeIn, MfaConfirmedOut, MfaDisableIn, MfaEnrollmentOut, PasswordResetConfirm,
    PasswordResetRequest, RefreshRequest, RegisterRequest, TokenPair, UpdateMeRequest,
)

router = APIRouter(prefix="/auth", tags=["auth"])

# Throttle the unauthenticated endpoints to blunt brute-force / user-enumeration.
_login_limit = rate_limit("auth_login", limit=10, window_s=60, fail_closed=True)
_register_limit = rate_limit("auth_register", limit=5, window_s=3600, fail_closed=True)
_access_request_limit = rate_limit("auth_access_request", limit=3, window_s=3600, fail_closed=True)
_password_reset_limit = rate_limit("auth_password_reset", limit=3, window_s=3600, fail_closed=True)
_password_reset_confirm_limit = rate_limit("auth_password_reset_confirm", limit=10, window_s=3600, fail_closed=True)


def _set_browser_refresh(response: Response, token: str) -> None:
    response.set_cookie(
        "gs_refresh", token, httponly=True, secure=settings.environment.lower() != "development",
        samesite="strict", max_age=settings.refresh_token_ttl_days * 86400,
        path="/api/auth/session",
    )


def _clear_browser_refresh(response: Response) -> None:
    response.delete_cookie("gs_refresh", path="/api/auth/session",
                           secure=settings.environment.lower() != "development", samesite="strict")


def _browser_token(response: Response, pair: TokenPair) -> AccessTokenOut:
    _set_browser_refresh(response, pair.refresh_token)
    return AccessTokenOut(access_token=pair.access_token)


@router.post("/register", response_model=TokenPair, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(_register_limit)])
async def register(body: RegisterRequest, db: AsyncSession = Depends(get_db)) -> TokenPair:
    return await service.register_user(db, body.email, body.password, body.full_name, body.invite_token)


@router.post("/session/register", response_model=AccessTokenOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(_register_limit)])
async def browser_register(body: RegisterRequest, response: Response,
                           db: AsyncSession = Depends(get_db)) -> AccessTokenOut:
    return _browser_token(response, await service.register_user(db, body.email, body.password, body.full_name, body.invite_token))


@router.post("/login", response_model=TokenPair, dependencies=[Depends(_login_limit)])
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)) -> TokenPair:
    return await service.login(db, body.email, body.password, body.mfa_code)


@router.post("/session/login", response_model=AccessTokenOut, dependencies=[Depends(_login_limit)])
async def browser_login(body: LoginRequest, response: Response,
                        db: AsyncSession = Depends(get_db)) -> AccessTokenOut:
    return _browser_token(response, await service.login(db, body.email, body.password, body.mfa_code))


@router.post("/access-requests", status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(_access_request_limit)])
async def create_access_request(body: AccessRequestIn, db: AsyncSession = Depends(get_db)) -> None:
    await service.request_access(db, body.email, body.full_name)


@router.get("/access-requests", response_model=list[AccessRequestOut])
async def list_access_requests(principal: Principal = Depends(get_current_principal),
                               db: AsyncSession = Depends(get_db)) -> list[AccessRequestOut]:
    if not principal.is_server_admin:
        from fastapi import HTTPException
        raise HTTPException(status.HTTP_403_FORBIDDEN, "solo administración del servidor")
    return await service.list_access_requests(db)


@router.post("/access-requests/{request_id}/approve", status_code=status.HTTP_204_NO_CONTENT)
async def approve_access_request(request_id: uuid.UUID, principal: Principal = Depends(get_current_principal),
                                 db: AsyncSession = Depends(get_db)) -> None:
    if not principal.is_server_admin:
        from fastapi import HTTPException
        raise HTTPException(status.HTTP_403_FORBIDDEN, "solo administración del servidor")
    await service.approve_access_request(db, request_id, principal.user_id)


@router.post("/password-reset/request", status_code=status.HTTP_204_NO_CONTENT,
             dependencies=[Depends(_password_reset_limit)])
async def password_reset_request(body: PasswordResetRequest, db: AsyncSession = Depends(get_db)) -> None:
    await service.request_password_reset(db, body.email)


@router.post("/password-reset/confirm", status_code=status.HTTP_204_NO_CONTENT,
             dependencies=[Depends(_password_reset_confirm_limit)])
async def password_reset_confirm(body: PasswordResetConfirm, db: AsyncSession = Depends(get_db)) -> None:
    await service.reset_password(db, body.token, body.new_password)


@router.post("/refresh", response_model=TokenPair)
async def refresh(body: RefreshRequest, db: AsyncSession = Depends(get_db)) -> TokenPair:
    return await service.rotate(db, body.refresh_token)


@router.post("/session/refresh", response_model=AccessTokenOut)
async def browser_refresh(request: Request, response: Response,
                          db: AsyncSession = Depends(get_db)) -> AccessTokenOut:
    token = request.cookies.get("gs_refresh")
    if not token:
        _clear_browser_refresh(response)
        from fastapi import HTTPException
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "sesión no encontrada")
    try:
        return _browser_token(response, await service.rotate(db, token))
    except Exception:
        _clear_browser_refresh(response)
        raise


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: RefreshRequest, db: AsyncSession = Depends(get_db)) -> None:
    await service.revoke(db, body.refresh_token)


@router.post("/session/logout", status_code=status.HTTP_204_NO_CONTENT)
async def browser_logout(request: Request, response: Response, db: AsyncSession = Depends(get_db)) -> None:
    token = request.cookies.get("gs_refresh")
    if token:
        await service.revoke(db, token)
    _clear_browser_refresh(response)


@router.post("/switch/{tenant_id}", response_model=TokenPair)
async def switch_tenant(
    tenant_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_authn_db),
) -> TokenPair:
    return await service.switch_tenant(db, principal, tenant_id)


@router.post("/session/switch/{tenant_id}", response_model=AccessTokenOut)
async def browser_switch_tenant(
    tenant_id: uuid.UUID, response: Response, principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_authn_db),
) -> AccessTokenOut:
    return _browser_token(response, await service.switch_tenant(db, principal, tenant_id))


@router.get("/me", response_model=MeOut)
async def me(
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_authn_db),
) -> MeOut:
    return await service.get_me(db, principal)


@router.patch("/me", response_model=MeOut)
async def update_me(
    body: UpdateMeRequest,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_authn_db),
) -> MeOut:
    return await service.update_me(db, principal, full_name=body.full_name, email=body.email)


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: ChangePasswordRequest,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_authn_db),
) -> None:
    await service.change_password(db, principal, current_password=body.current_password,
                                  new_password=body.new_password)


@router.post("/mfa/enroll", response_model=MfaEnrollmentOut)
async def mfa_enroll(principal: Principal = Depends(get_current_principal),
                     db: AsyncSession = Depends(get_authn_db)) -> MfaEnrollmentOut:
    profile = await service.get_me(db, principal)
    return MfaEnrollmentOut(otpauth_url=await service.begin_mfa(db, principal.user_id, profile.user.email))


@router.post("/mfa/confirm", response_model=MfaConfirmedOut)
async def mfa_confirm(body: MfaCodeIn, principal: Principal = Depends(get_current_principal),
                      db: AsyncSession = Depends(get_authn_db)) -> MfaConfirmedOut:
    return MfaConfirmedOut(recovery_codes=await service.confirm_mfa(db, principal.user_id, body.code))


@router.post("/mfa/disable", status_code=status.HTTP_204_NO_CONTENT)
async def mfa_disable(body: MfaDisableIn, principal: Principal = Depends(get_current_principal),
                      db: AsyncSession = Depends(get_authn_db)) -> None:
    await service.disable_mfa(db, principal.user_id, body.password, body.code)
