"""Durable inventory orchestration; metadata only, no inline S3 operations."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from fastapi import HTTPException
from sqlalchemy import func, select, text
from ...core.dispatch import schedule
from ...models.job import Job
from ...models.storage_inventory import StorageScan, StorageInventoryItem
from ...models.task_dispatch import TaskDispatch
from ...models.tenant import Tenant
from .inventory import configuration, fingerprint, matches_configuration


async def owned(db, tenant_id, scan_id, *, lock=False):
    # Protected recounts share the inventory tables, but their controls must also
    # maintain the quota fence. Never expose them through observational controls.
    query = select(StorageScan).join(Job, Job.id == StorageScan.job_id).where(
        StorageScan.id == scan_id, StorageScan.tenant_id == tenant_id, Job.type == "inventory_storage")
    row = await db.scalar(query.with_for_update() if lock else query)
    if not row: raise HTTPException(404, "No existe este inventario en el espacio activo.")
    return row


def summary(scan, job):
    return {"id": scan.id, "job_id": scan.job_id, "job_status": job.status if job else None,
        "phase": scan.phase, "bucket_role": ("private", "public")[scan.bucket_slot],
        "reason": scan.reason, "created_by": scan.created_by, "created_at": scan.created_at,
        "started_at": scan.started_at, "completed_at": scan.completed_at, "invalidated_at": scan.invalidated_at,
        "storage_configuration_matches": matches_configuration(scan), "error": scan.error or (job.error if job else None),
        "consistent_snapshot": False, "quota_enforced": False, "measurement": "observed_payload_bytes"}


async def scan_out(db, scan):
    result = summary(scan, await db.get(Job, scan.job_id))
    rows = (await db.execute(select(StorageInventoryItem.bucket_slot, StorageInventoryItem.category,
        StorageInventoryItem.kind, StorageInventoryItem.is_latest, StorageInventoryItem.upload_state,
        func.count(), func.sum(StorageInventoryItem.byte_size)).where(StorageInventoryItem.scan_id == scan.id,
        StorageInventoryItem.tenant_id == scan.tenant_id).group_by(StorageInventoryItem.bucket_slot,
        StorageInventoryItem.category, StorageInventoryItem.kind, StorageInventoryItem.is_latest, StorageInventoryItem.upload_state))).all()
    counts = {"current_versions": 0, "noncurrent_versions": 0, "delete_markers": 0,
        "multipart_uploads": 0, "multipart_parts": 0, "uninspected_uploads": 0}
    sizes = {"current_versions": 0, "noncurrent_versions": 0, "multipart_parts": 0}
    groups = {}
    for slot, category, kind, latest, state, count, size in rows:
        size = int(size or 0)
        metric = {"version": "current_versions" if latest else "noncurrent_versions",
            "delete_marker": "delete_markers", "upload": "multipart_uploads", "part": "multipart_parts"}[kind]
        counts[metric] += count
        if metric in sizes: sizes[metric] += size
        if kind == "upload" and state != "listed": counts["uninspected_uploads"] += count
        key = (slot, category)
        group = groups.setdefault(key, {"bucket_role": ("private", "public")[slot], "category": category,
            "payload_bytes": 0, "versions": 0, "delete_markers": 0, "multipart_uploads": 0, "multipart_parts": 0})
        group["payload_bytes"] += size
        group["versions" if kind == "version" else metric] += count
    result.update(counts=counts, bytes=sizes, observed_payload_bytes=sum(sizes.values()),
        groups=[groups[key] for key in sorted(groups)],
        listing_finished=scan.phase == "completed", upload_inspection_complete=counts["uninspected_uploads"] == 0 and scan.phase == "completed")
    return result


async def create(db, principal, body):
    # No key-changing lock: unrelated document/Job FK inserts use KEY SHARE on Tenant.
    await db.scalar(select(Tenant.id).where(Tenant.id == principal.tenant_id).with_for_update(key_share=True))
    scan_id = uuid.uuid5(principal.tenant_id, "storage-inventory:v1:" + str(body.client_id))
    digest = fingerprint(body.model_dump(mode="json"))
    previous = await db.get(StorageScan, scan_id)
    if previous:
        if previous.request_hash != digest: raise HTTPException(409, "La misma solicitud no puede cambiar el motivo del inventario.")
        return await scan_out(db, previous)
    active = await db.scalar(select(StorageScan.id).join(Job, Job.id == StorageScan.job_id).where(
        StorageScan.tenant_id == principal.tenant_id, Job.type == "inventory_storage", Job.status.in_(("queued", "running"))).limit(1))
    if active: raise HTTPException(409, "Ya hay un inventario en marcha. Consúltalo o cancélalo antes de iniciar otro.")
    try:
        buckets, location_hash = configuration()
    except ValueError as error:
        raise HTTPException(503, "Revisa que los buckets privado y público sean distintos antes de inventariar.") from error
    job = Job(id=uuid.uuid5(scan_id, "job"), tenant_id=principal.tenant_id, created_by=principal.user_id,
        type="inventory_storage", status="queued", params={"scan_id": str(scan_id)})
    db.add(job)
    await schedule(db, job, "inventory_storage", scan_id=str(scan_id))
    scan = StorageScan(id=scan_id, tenant_id=principal.tenant_id, job_id=job.id, buckets=buckets,
        location_hash=location_hash, request_hash=digest, phase="versions", bucket_slot=0, cursor={},
        created_by=principal.user_id, reason=body.reason)
    db.add(scan); await db.flush()
    return await scan_out(db, scan)


async def control(db, tenant_id, scan_id, action):
    await db.scalar(select(Tenant.id).where(Tenant.id == tenant_id).with_for_update(key_share=True))
    scan = await owned(db, tenant_id, scan_id)
    # Check a live worker before locking its Job row (workers checkpoint under that row).
    if action == "retry":
        key = int.from_bytes(scan.job_id.bytes[:8], "big", signed=True)
        if not await db.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": key}):
            raise HTTPException(409, "El intento anterior todavía está terminando; espera antes de reanudar.")
    job = await db.scalar(select(Job).where(Job.id == scan.job_id, Job.tenant_id == tenant_id).with_for_update())
    if not job: raise HTTPException(409, "Falta el trabajo del inventario.")
    if action == "cancel":
        if job.status in {"queued", "running", "paused", "error"}:
            job.status = "cancelled"; job.finished_at = datetime.now(timezone.utc)
    else:
        if scan.phase == "completed" or job.status in {"queued", "running"}: return await scan_out(db, scan)
        if job.status not in {"error", "paused"} or not matches_configuration(scan):
            raise HTTPException(409, "Este inventario no se puede reanudar; inicia uno nuevo sin borrar el historial.")
        active = await db.scalar(select(StorageScan.id).join(Job, Job.id == StorageScan.job_id).where(
            StorageScan.tenant_id == tenant_id, Job.type == "inventory_storage", Job.status.in_(("queued", "running"))).limit(1))
        if active: raise HTTPException(409, "Termina o cancela el otro inventario activo antes de reanudar este.")
        dispatch = await db.get(TaskDispatch, scan.job_id)
        if not dispatch: raise HTTPException(409, "Falta el envío duradero del inventario; no se duplica el trabajo.")
        dispatch.generation += 1; dispatch.status = "pending"; dispatch.available_at = datetime.now(timezone.utc)
        dispatch.error = None; dispatch.dispatched_at = None
        job.status = "queued"; job.error = None; job.finished_at = None; scan.error = None
    await db.flush()
    return await scan_out(db, scan)
