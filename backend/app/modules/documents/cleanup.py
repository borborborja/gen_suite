"""Explicit disposal of one closed upload namespace, never a document prefix."""
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import case, func, select, text
from ...core import storage
from ...core.deps import get_current_principal, get_tenant_db, require_roles
from ...core.dispatch import schedule
from ...models.ingest import IngestSession, IngestFile, IngestBook
from ...models.ingest_cleanup import IngestCleanup, IngestCleanupObject
from ...models.job import Job
from ...models.task_dispatch import TaskDispatch
from ...models.tenant import Tenant
from ...settings import settings
from ..providers.admission import fingerprint
from .ingestion import owned

router = APIRouter(prefix="/ingestion", tags=["ingest-cleanup"])
ADMIN = [Depends(require_roles("tenant_admin"))]


class CleanupApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preview_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str = Field(min_length=1, max_length=2000)
    authorize_permanent_staging_deletion: Literal[True]
    local_originals_confirmed: Literal[True]

    @field_validator("authorize_permanent_staging_deletion", "local_originals_confirmed", mode="before")
    @classmethod
    def explicit_true(cls, value):
        if value is not True: raise ValueError("La eliminación de temporales necesita confirmación explícita.")
        return value


async def admission_bytes(db, tenant_id):
    charged = case((IngestCleanup.phase == "completed", IngestCleanup.retained_reservation_bytes), else_=IngestSession.reserved_bytes)
    return int(await db.scalar(select(func.coalesce(func.sum(charged), 0)).select_from(IngestSession)
        .outerjoin(IngestCleanup, (IngestCleanup.session_id == IngestSession.id) & (IngestCleanup.tenant_id == IngestSession.tenant_id))
        .where(IngestSession.tenant_id == tenant_id)) or 0)


async def idle_job(db, tenant_id, job_id):
    if not job_id: return None
    # The same lock as serialized_job; a stale timestamp is not proof that a worker stopped.
    key = int.from_bytes(job_id.bytes[:8], "big", signed=True)
    if not await db.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": key}):
        raise HTTPException(409, "El intento anterior todavía está terminando. Espera antes de limpiar o reintentar.")
    job = await db.scalar(select(Job).where(Job.id == job_id, Job.tenant_id == tenant_id).with_for_update())
    if job and job.status in {"running", "queued"}:
        raise HTTPException(409, "Cancela o termina primero la verificación de originales. No se limpian cargas en ejecución.")
    return job


async def preview_data(db, tenant_id, session_id, *, lock=False):
    row = await owned(db, tenant_id, session_id, lock=lock)
    if row.status in {"cleaning", "discarded"}:
        raise HTTPException(409, "Esta sesión ya está cerrada para limpieza. Consulta o reintenta su mismo trabajo.")
    if lock:
        job = await idle_job(db, tenant_id, row.job_id)
    else:
        job = await db.get(Job, row.job_id) if row.job_id else None
        if job and job.status in {"running", "queued"}:
            raise HTTPException(409, "Cancela o termina primero la verificación de originales.")
    files = int(await db.scalar(select(func.count()).select_from(IngestFile).where(IngestFile.session_id == row.id)) or 0)
    published = int(await db.scalar(select(func.count()).select_from(IngestBook).where(IngestBook.session_id == row.id,
        IngestBook.status == "completed")) or 0)
    # A processing attempt may have written an original before its DB checkpoint. Keep
    # that admission charge conservatively; only never-processed sessions release it.
    kept = 0 if row.status in {"manifest", "uploading"} and not row.job_id else row.reserved_bytes
    result = {"session_id": str(row.id), "title": row.title, "status": row.status,
        "updated_at": row.updated_at.isoformat(), "job_id": str(row.job_id) if row.job_id else None,
        "job_status": job.status if job else None, "files": files, "published_books": published,
        "received_bytes": row.received_bytes, "reserved_bytes": row.reserved_bytes,
        "retained_reservation_bytes": kept, "released_reservation_bytes": row.reserved_bytes - kept,
        "mode": "compact" if row.status == "completed" else "discard",
        "deletion_scope": "temporary_parts_and_all_their_versions_only", "originals_deleted": False,
        "can_resume_upload_afterwards": False}
    result["preview_hash"] = fingerprint(result)
    return result, row


async def cleanup_out(db, row):
    job = await db.get(Job, row.job_id)
    stats = (await db.execute(select(func.count(), func.coalesce(func.sum(IngestCleanupObject.byte_size), 0),
        func.count().filter(IngestCleanupObject.deleted_at.is_not(None)),
        func.coalesce(func.sum(IngestCleanupObject.byte_size).filter(IngestCleanupObject.deleted_at.is_not(None)), 0))
        .where(IngestCleanupObject.cleanup_id == row.id, IngestCleanupObject.inventory_epoch == row.inventory_epoch))).one()
    return {"id": row.id, "session_id": row.session_id, "job_id": row.job_id,
        "mode": row.mode, "phase": row.phase, "inventory_epoch": row.inventory_epoch,
        "job_status": job.status if job else None, "error": row.error or (job.error if job else None),
        "planned_objects": stats[0], "planned_bytes": int(stats[1]), "deleted_objects": stats[2], "deleted_bytes": int(stats[3]),
        "retained_reservation_bytes": row.retained_reservation_bytes, "reason": row.reason,
        "created_by": row.created_by, "created_at": row.created_at, "completed_at": row.completed_at}


@router.get("/quota")
async def quota(principal=Depends(get_current_principal), db=Depends(get_tenant_db)):
    return {"admission_bytes": await admission_bytes(db, principal.tenant_id), "limit_bytes": settings.ingest_tenant_quota_bytes,
        "physical_storage_quota": False}


@router.get("/{session_id}/cleanup")
async def get_cleanup(session_id: uuid.UUID, principal=Depends(get_current_principal), db=Depends(get_tenant_db)):
    await owned(db, principal.tenant_id, session_id)
    row = await db.scalar(select(IngestCleanup).where(IngestCleanup.session_id == session_id, IngestCleanup.tenant_id == principal.tenant_id))
    return await cleanup_out(db, row) if row else None


@router.post("/{session_id}/cleanup/preview", dependencies=ADMIN)
async def preview(session_id: uuid.UUID, principal=Depends(get_current_principal), db=Depends(get_tenant_db)):
    result, _ = await preview_data(db, principal.tenant_id, session_id)
    return result


@router.post("/{session_id}/cleanup", status_code=202, dependencies=ADMIN)
async def approve(session_id: uuid.UUID, body: CleanupApproval, principal=Depends(get_current_principal), db=Depends(get_tenant_db)):
    if not body.reason.strip(): raise HTTPException(422, "Documenta el motivo de la limpieza.")
    await db.scalar(select(Tenant.id).where(Tenant.id == principal.tenant_id).with_for_update(key_share=True))
    row = await owned(db, principal.tenant_id, session_id, lock=True)
    cleanup_id = uuid.uuid5(row.id, "staging-cleanup:v1")
    request_hash = fingerprint(body.model_dump(mode="json"))
    existing = await db.get(IngestCleanup, cleanup_id)
    if existing:
        if existing.request_hash != request_hash: raise HTTPException(409, "Ya existe una limpieza confirmada; no se cambia su alcance.")
        return await cleanup_out(db, existing)
    compared, row = await preview_data(db, principal.tenant_id, session_id, lock=True)
    if compared["preview_hash"] != body.preview_hash:
        raise HTTPException(409, "La sesión ha cambiado. Revisa la comparación antes de eliminar temporales.")
    job = Job(id=uuid.uuid5(cleanup_id, "job"), tenant_id=principal.tenant_id, created_by=principal.user_id,
        type="cleanup_ingestion", status="queued", params={"cleanup_id": str(cleanup_id), "session_id": str(row.id)})
    db.add(job)
    await schedule(db, job, "cleanup_ingestion", cleanup_id=str(cleanup_id))
    cleanup = IngestCleanup(id=cleanup_id, tenant_id=principal.tenant_id, session_id=row.id, job_id=job.id,
        mode=compared["mode"], phase="verify_originals", original_status=row.status, bucket=storage.bucket_for("private"),
        inventory_epoch=1, inventory_cursor={}, retained_reservation_bytes=compared["retained_reservation_bytes"],
        preview_hash=body.preview_hash, request_hash=request_hash, created_by=principal.user_id, reason=body.reason.strip())
    db.add(cleanup); row.status = "cleaning"  # Durable fence before any external deletion.
    await db.flush()
    return await cleanup_out(db, cleanup)


@router.post("/{session_id}/cleanup/retry", status_code=202, dependencies=ADMIN)
async def retry(session_id: uuid.UUID, principal=Depends(get_current_principal), db=Depends(get_tenant_db)):
    await owned(db, principal.tenant_id, session_id, lock=True)
    row = await db.scalar(select(IngestCleanup).where(IngestCleanup.session_id == session_id,
        IngestCleanup.tenant_id == principal.tenant_id).with_for_update())
    if not row: raise HTTPException(404, "No existe una limpieza autorizada para esta sesión.")
    job = await db.get(Job, row.job_id)
    if row.phase == "completed" or (job and job.status in {"queued", "running"}): return await cleanup_out(db, row)
    job = await idle_job(db, principal.tenant_id, row.job_id)
    dispatch = await db.get(TaskDispatch, row.job_id)
    if not job or not dispatch: raise HTTPException(409, "Falta el trabajo o su envío duradero; no se crea otra eliminación.")
    dispatch.generation += 1; dispatch.status = "pending"; dispatch.available_at = datetime.now(timezone.utc)
    dispatch.error = None; dispatch.dispatched_at = None
    job.status = "queued"; job.error = None; job.finished_at = None; row.error = None
    await db.flush()
    return await cleanup_out(db, row)
