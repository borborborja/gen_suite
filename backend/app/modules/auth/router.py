from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.deps import get_authn_db, get_current_principal, get_db, rate_limit
from ...core.security import Principal
from . import service
from .schemas import (
    ChangePasswordRequest, LoginRequest, MeOut, RefreshRequest, RegisterRequest, TokenPair,
    UpdateMeRequest,
)

router = APIRouter(prefix="/auth", tags=["auth"])

# Throttle the unauthenticated endpoints to blunt brute-force / user-enumeration.
_login_limit = rate_limit("auth_login", limit=10, window_s=60)
_register_limit = rate_limit("auth_register", limit=5, window_s=3600)


@router.post("/register", response_model=TokenPair, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(_register_limit)])
async def register(body: RegisterRequest, db: AsyncSession = Depends(get_db)) -> TokenPair:
    return await service.register_user(db, body.email, body.password, body.full_name)


@router.post("/login", response_model=TokenPair, dependencies=[Depends(_login_limit)])
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)) -> TokenPair:
    return await service.login(db, body.email, body.password)


@router.post("/refresh", response_model=TokenPair)
async def refresh(body: RefreshRequest, db: AsyncSession = Depends(get_db)) -> TokenPair:
    return await service.rotate(db, body.refresh_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: RefreshRequest, db: AsyncSession = Depends(get_db)) -> None:
    await service.revoke(db, body.refresh_token)


@router.post("/switch/{tenant_id}", response_model=TokenPair)
async def switch_tenant(
    tenant_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_authn_db),
) -> TokenPair:
    return await service.switch_tenant(db, principal, tenant_id)


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
