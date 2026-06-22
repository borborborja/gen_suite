from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.deps import get_authn_db, get_current_principal, get_tenant_db
from ...core.security import Principal
from . import service
from .schemas import ApiKeyOut, CreateApiKeyRequest, CreateApiKeyResponse

router = APIRouter(prefix="/api-keys", tags=["api-keys"])


def _out(k) -> ApiKeyOut:
    return ApiKeyOut(
        id=k.id, name=k.name, scope=k.scope, role=k.role, token_prefix=k.token_prefix,
        created_at=k.created_at, last_used_at=k.last_used_at, expires_at=k.expires_at,
        revoked_at=k.revoked_at,
    )


@router.post("", response_model=CreateApiKeyResponse, status_code=status.HTTP_201_CREATED)
async def create_key(
    body: CreateApiKeyRequest,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> CreateApiKeyResponse:
    """Create an external API token bound to the active tenant. The plaintext token is returned ONCE."""
    key, token = await service.create_key(
        db, principal, name=body.name, scope=body.scope, expires_days=body.expires_days)
    return CreateApiKeyResponse(token=token, key=_out(key))


@router.get("", response_model=list[ApiKeyOut])
async def list_keys(
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_authn_db),
) -> list[ApiKeyOut]:
    return [_out(k) for k in await service.list_keys(db, principal.user_id)]


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_key(
    key_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_authn_db),
) -> None:
    await service.revoke_key(db, principal.user_id, key_id)
