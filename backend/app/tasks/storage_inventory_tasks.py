"""Resumable metadata inventory: private/public versions, uploads and uploaded parts."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from botocore.exceptions import ClientError
from sqlalchemy import insert, select
from ..core import storage
from ..core.job_lifecycle import job_is_active, update_active_job
from ..core.task_execution import serialized_job
from ..db.rls import commit_keep_rls, set_rls_context
from ..db.session import SessionLocal
from ..models.storage_inventory import StorageScan, StorageInventoryItem
from ..modules.storage_audit import inventory


class InventoryStopped(Exception):
    pass


async def running(db, scan):
    if not inventory.matches_configuration(scan):
        raise ValueError("El almacenamiento ya no coincide con el inventariado")
    if not await job_is_active(db, scan.job_id): raise InventoryStopped()


async def checkpoint(db, scan):
    await running(db, scan)
    if not await update_active_job(db, scan.job_id, progress={"phase": scan.phase, "bucket_role": ("private", "public")[scan.bucket_slot]}):
        raise InventoryStopped()
    await commit_keep_rls(db, tenant_id=scan.tenant_id)


async def save_page(db, scan, slot, rows):
    if rows:
        # Cursor and entries commit atomically. A repeated identity on a *later* page is
        # overlapping/inconsistent pagination, not a safe reason to silently discard it.
        # The PK also breaks multi-page cursor cycles without an unbounded in-memory set.
        await db.execute(insert(StorageInventoryItem).values([{**row, "id": inventory.item_id(scan.id, slot, row),
            "tenant_id": scan.tenant_id, "scan_id": scan.id, "bucket_slot": slot} for row in rows]))


async def process(db, scan, client, *, finalize=None):
    while scan.phase in {"versions", "uploads"}:
        await running(db, scan)
        reader = inventory.versions_page if scan.phase == "versions" else inventory.uploads_page
        rows, following = await asyncio.to_thread(reader, client, scan.buckets[scan.bucket_slot], scan.tenant_id, scan.cursor)
        await save_page(db, scan, scan.bucket_slot, rows)
        scan.cursor = following
        if not following:
            if scan.bucket_slot == 0:
                scan.bucket_slot = 1
            else:
                scan.bucket_slot = 0
                scan.phase = "uploads" if scan.phase == "versions" else "parts"
        await checkpoint(db, scan)
    while scan.phase == "parts":
        await running(db, scan)
        upload = await db.scalar(select(StorageInventoryItem).where(StorageInventoryItem.scan_id == scan.id,
            StorageInventoryItem.tenant_id == scan.tenant_id, StorageInventoryItem.kind == "upload",
            StorageInventoryItem.upload_state == "pending").order_by(StorageInventoryItem.id).limit(1))
        if upload is None:
            if finalize is not None:
                await finalize(db, scan)
                return
            finished = datetime.now(timezone.utc)
            if not await update_active_job(db, scan.job_id, status="completed", finished_at=finished,
                    result={"scan_id": str(scan.id), "consistent_snapshot": False, "quota_enforced": False}):
                raise InventoryStopped()
            scan.phase = "completed"; scan.completed_at = finished; scan.error = None
            await commit_keep_rls(db, tenant_id=scan.tenant_id)
            return
        scan.bucket_slot = upload.bucket_slot
        try:
            rows, following = await asyncio.to_thread(inventory.parts_page, client,
                scan.buckets[upload.bucket_slot], scan.tenant_id, upload)
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") != "NoSuchUpload": raise
            # Concurrent completion/abort is NOT proof of zero bytes. Preserve any parts
            # already observed and expose incomplete inspection in the resulting report.
            upload.upload_state = "unavailable"
        else:
            await save_page(db, scan, upload.bucket_slot, rows)
            if following is None:
                upload.upload_state = "listed"
            else:
                upload.parts_cursor = following
        await checkpoint(db, scan)


@serialized_job
async def inventory_storage(ctx, *, job_id, tenant_id, scan_id):
    scan_id = uuid.UUID(str(scan_id))
    async with SessionLocal() as db:
        await set_rls_context(db, tenant_id=tenant_id)
        scan = await db.scalar(select(StorageScan).where(StorageScan.id == scan_id, StorageScan.tenant_id == tenant_id, StorageScan.job_id == job_id))
        if not scan or scan.phase == "completed": return
        if not await update_active_job(db, job_id, status="running", started_at=datetime.now(timezone.utc)): return
        scan.started_at = scan.started_at or datetime.now(timezone.utc); scan.error = None
        await commit_keep_rls(db, tenant_id=tenant_id)
        try:
            await process(db, scan, storage._s3())
        except InventoryStopped:
            await db.rollback()
        except Exception:
            await db.rollback(); await set_rls_context(db, tenant_id=tenant_id)
            scan = await db.get(StorageScan, scan_id)
            if not scan: return
            # Do not expose SDK errors, bucket names, object keys or credentials.
            scan.error = ("Inventario incompleto. Revisa conexión, permisos de listar versiones/cargas/partes y "
                "paginación del almacenamiento. Si cambió el almacenamiento o se repiten objetos, inicia un "
                "inventario nuevo. El resultado parcial no acredita espacio libre ni modifica cuotas.")
            await update_active_job(db, job_id, status="error", error=scan.error, finished_at=datetime.now(timezone.utc))
            await commit_keep_rls(db, tenant_id=tenant_id)
