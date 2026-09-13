from __future__ import annotations
import uuid
from datetime import datetime, timezone
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select, text
from ...core.deps import get_current_principal, get_tenant_db, require_roles
from ...core.dispatch import schedule
from ...models.job import Job
from ...models.storage_inventory import StorageScan
from ...models.storage_quota import StorageQuota, StorageWrite
from ...models.task_dispatch import TaskDispatch
from ...settings import settings
from . import inventory, quota, service

router = APIRouter(prefix="/storage/quota", tags=["storage-quota"])
from .write_actions import router as write_actions_router
router.include_router(write_actions_router)
ADMIN = [Depends(require_roles("tenant_admin"))]


class RecountRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    client_id: uuid.UUID
    reason: str = Field(min_length=1, max_length=2000)
    authorize_write_pause: Literal[True]
    external_writers_stopped: Literal[True]

    @field_validator("authorize_write_pause", "external_writers_stopped", mode="before")
    @classmethod
    def explicit_true(cls, value):
        if value is not True: raise ValueError("Confirma la pausa y que no hay otros clientes escribiendo en S3.")
        return value


async def quota_out(db, row):
    current = inventory.configuration()[1]
    pending = int(await db.scalar(select(func.count()).select_from(StorageWrite).where(
        StorageWrite.tenant_id == row.tenant_id, StorageWrite.state.in_(quota.PENDING))) or 0)
    scan = await db.get(StorageScan, row.scan_id) if row.scan_id else None
    return {"phase": row.phase, "used_bytes": row.used_bytes, "reserved_bytes": row.reserved_bytes,
        "limit_bytes": settings.storage_tenant_quota_bytes, "pending_writes": pending,
        "location_matches": row.location_hash == current, "baseline_at": row.baseline_at,
        "quota_enforced": True, "measurement": "conservative_payload_bytes",
        "scan": await service.scan_out(db, scan) if scan else None}


@router.get("")
async def status(principal=Depends(get_current_principal), db=Depends(get_tenant_db)):
    row = await db.get(StorageQuota, principal.tenant_id)
    if row is None:
        row = StorageQuota(tenant_id=principal.tenant_id, phase="uninitialized", used_bytes=0,
            reserved_bytes=0, location_hash=None, scan_id=None, baseline_at=None)
    return await quota_out(db, row)


def write_out(row):
    return {"id": row.id, "kind": row.kind, "state": row.state, "reserved_bytes": row.reserved_bytes,
        "actual_bytes": row.actual_bytes, "job_id": row.job_id, "created_at": row.created_at,
        "finished_at": row.finished_at, "error": row.error}


@router.get("/writes")
async def writes(offset: int = Query(0, ge=0), principal=Depends(get_current_principal), db=Depends(get_tenant_db)):
    rows = (await db.scalars(select(StorageWrite).where(StorageWrite.tenant_id == principal.tenant_id,
        StorageWrite.state.in_(quota.PENDING)).order_by(StorageWrite.created_at, StorageWrite.id).offset(offset).limit(20))).all()
    return [write_out(row) for row in rows]


@router.post("/writes/{write_id}/recheck", dependencies=ADMIN)
async def recheck(write_id: uuid.UUID, principal=Depends(get_current_principal), db=Depends(get_tenant_db)):
    row = await db.scalar(select(StorageWrite).where(StorageWrite.id == write_id, StorageWrite.tenant_id == principal.tenant_id))
    if not row: raise HTTPException(404, "No existe ese recibo en este espacio.")
    try: await quota.recheck(row)
    except HTTPException: raise
    except Exception: raise HTTPException(409, "S3 no acredita el resultado exacto. Se conserva la reserva; no se ha repetido la escritura.") from None
    await db.refresh(row)
    return write_out(row)


@router.post("/recount", status_code=202, dependencies=ADMIN)
async def recount(body: RecountRequest, principal=Depends(get_current_principal), db=Depends(get_tenant_db)):
    row = await quota.account(db, principal.tenant_id)
    scan_id = uuid.uuid5(principal.tenant_id, "storage-quota:v1:" + str(body.client_id))
    digest = inventory.fingerprint(body.model_dump(mode="json"))
    existing = await db.get(StorageScan, scan_id)
    if existing:
        if existing.request_hash != digest: raise HTTPException(409, "La solicitud de recuento cambió de motivo.")
        return await quota_out(db, row)
    if row.phase in {"draining", "counting"}: raise HTTPException(409, "Ya hay un recuento protegido; reanúdalo o cancélalo.")
    buckets, location = inventory.configuration()
    job = Job(id=uuid.uuid5(scan_id, "job"), tenant_id=principal.tenant_id, created_by=principal.user_id,
        type="recount_storage", status="queued", params={"scan_id": str(scan_id)})
    db.add(job); await schedule(db, job, "recount_storage", scan_id=str(scan_id))
    scan = StorageScan(id=scan_id, tenant_id=principal.tenant_id, job_id=job.id, buckets=buckets,
        location_hash=location, request_hash=digest, phase="versions", bucket_slot=0, cursor={},
        created_by=principal.user_id, reason=body.reason)
    db.add(scan); await db.flush()
    row.scan_id = scan.id; row.phase = "draining"; await db.flush()
    return await quota_out(db, row)


@router.post("/recount/{action}", status_code=202, dependencies=ADMIN)
async def control(action: str, principal=Depends(get_current_principal), db=Depends(get_tenant_db)):
    if action not in {"cancel", "retry"}: raise HTTPException(404, "Acción no disponible.")
    row = await quota.account(db, principal.tenant_id)
    if row.phase not in {"draining", "counting"} or not row.scan_id:
        raise HTTPException(409, "No hay un recuento protegido pendiente.")
    scan = await db.get(StorageScan, row.scan_id)
    if not scan: raise HTTPException(409, "Falta el inventario del recuento.")
    if action == "retry":
        lock = int.from_bytes(scan.job_id.bytes[:8], "big", signed=True)
        if not await db.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": lock}):
            raise HTTPException(409, "El intento anterior todavía está terminando.")
    job = await db.scalar(select(Job).where(Job.id == scan.job_id).with_for_update())
    if action == "cancel":
        if job.status in {"queued", "running", "error", "paused"}: job.status = "cancelled"; job.finished_at = datetime.now(timezone.utc)
        row.phase = "ready" if row.baseline_at else "uninitialized"
    elif job.status not in {"queued", "running"}:
        if not inventory.matches_configuration(scan): raise HTTPException(409, "Cancela este recuento e inicia otro en el destino actual.")
        dispatch = await db.get(TaskDispatch, job.id)
        if not dispatch: raise HTTPException(409, "Falta el envío duradero; no se duplica el recuento.")
        dispatch.generation += 1; dispatch.status = "pending"; dispatch.available_at = datetime.now(timezone.utc)
        dispatch.error = None; dispatch.dispatched_at = None
        job.status = "queued"; job.error = None; job.finished_at = None; scan.error = None
    await db.flush()
    return await quota_out(db, row)
