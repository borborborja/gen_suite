from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.deps import get_current_principal, get_tenant_db, require_roles
from ...core.security import Principal
from ...models.membership import MembershipRole
from ..tree.audit import audited
from . import service
from .schemas import (
    ApplyResult, BaselineIn, BaselineOut, ConnectionIn, ConnectionOut, ResolveChange, SyncChangeBatch,
    SyncChangeOut, SyncSnapshot,
)

router = APIRouter(prefix="/interop", tags=["interop"])
_EDIT = (MembershipRole.editor.value,)


@router.get("/model")
async def supported_model() -> dict:
    return {"object_types": sorted(service.GRAMPS_OBJECT_TYPES), "baseline_required": True, "deletion": "tombstone"}


@router.get("/connections", response_model=list[ConnectionOut])
async def list_connections(db: AsyncSession = Depends(get_tenant_db)) -> list[ConnectionOut]:
    return await service.list_connections(db)


@router.post("/connections", response_model=ConnectionOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_roles(*_EDIT))])
async def create_connection(body: ConnectionIn, principal: Principal = Depends(get_current_principal),
                            db: AsyncSession = Depends(get_tenant_db)) -> ConnectionOut:
    async with audited(db, principal, action="interop_connection_create", entity_type="interop_connection", summary="Configuró una conexión de sincronización"):
        row = await service.create_connection(db, principal.tenant_id, principal.user_id, body)
    return service.connection_out(row)


@router.get("/connections/{connection_id}/baselines", response_model=list[BaselineOut])
async def list_baselines(connection_id: uuid.UUID, db: AsyncSession = Depends(get_tenant_db)) -> list[BaselineOut]:
    return await service.list_baselines(db, connection_id)


@router.post("/connections/{connection_id}/baselines", response_model=BaselineOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_roles(*_EDIT))])
async def establish_baseline(connection_id: uuid.UUID, body: BaselineIn,
                             principal: Principal = Depends(get_current_principal),
                             db: AsyncSession = Depends(get_tenant_db)) -> BaselineOut:
    async with audited(db, principal, action="sync_baseline_create", entity_type="sync_baseline", summary="Confirmó una línea base de sincronización"):
        row = await service.establish_baseline(db, connection_id, principal.user_id, body)
    return service.baseline_out(row)


@router.get("/connections/{connection_id}/changes", response_model=list[SyncChangeOut])
async def list_changes(connection_id: uuid.UUID, state: str | None = Query(default=None),
                       db: AsyncSession = Depends(get_tenant_db)) -> list[SyncChangeOut]:
    return await service.list_changes(db, connection_id, state)


@router.post("/connections/{connection_id}/changes", response_model=list[SyncChangeOut],
             status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_roles(*_EDIT))])
async def ingest_changes(connection_id: uuid.UUID, body: SyncChangeBatch,
                         principal: Principal = Depends(get_current_principal),
                         db: AsyncSession = Depends(get_tenant_db)) -> list[SyncChangeOut]:
    async with audited(db, principal, action="sync_change_ingest", entity_type="sync_change", summary="Recibió cambios de sincronización"):
        return await service.ingest_changes(db, connection_id, body.changes)


@router.post("/connections/{connection_id}/apply", response_model=ApplyResult,
             dependencies=[Depends(require_roles(*_EDIT))])
async def apply_pending_changes(connection_id: uuid.UUID, principal: Principal = Depends(get_current_principal),
                                db: AsyncSession = Depends(get_tenant_db)) -> ApplyResult:
    async with audited(db, principal, action="sync_change_apply", entity_type="sync_connection", entity_id=connection_id,
                       summary="Aplicó cambios de sincronización no conflictivos"):
        return await service.apply_pending_changes(db, connection_id)


@router.get("/connections/{connection_id}/snapshot", response_model=SyncSnapshot)
async def get_snapshot(connection_id: uuid.UUID, db: AsyncSession = Depends(get_tenant_db)) -> SyncSnapshot:
    return SyncSnapshot(connection_id=connection_id, objects=await service.get_snapshot(db, connection_id))


@router.post("/changes/{change_id}/resolve", response_model=SyncChangeOut,
             dependencies=[Depends(require_roles(*_EDIT))])
async def resolve_change(change_id: uuid.UUID, body: ResolveChange,
                         principal: Principal = Depends(get_current_principal),
                         db: AsyncSession = Depends(get_tenant_db)) -> SyncChangeOut:
    async with audited(db, principal, action="sync_conflict_resolve", entity_type="sync_change", entity_id=change_id,
                       summary="Resolvió un cambio de sincronización"):
        row = await service.resolve_change(db, change_id, principal.user_id, body.resolution, body.note)
    return service.change_out(row)
