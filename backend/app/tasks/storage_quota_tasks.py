"""Build a new baseline only under a durable application-wide tenant write fence."""
from datetime import datetime, timezone
import uuid
from sqlalchemy import func, select
from ..core import storage
from ..core.job_lifecycle import update_active_job
from ..core.task_execution import serialized_job
from ..db.rls import commit_keep_rls, set_rls_context
from ..db.session import SessionLocal
from ..models.storage_inventory import StorageScan, StorageInventoryItem
from ..models.storage_quota import StorageWrite
from ..modules.storage_audit import inventory, quota
from .storage_inventory_tasks import InventoryStopped, process


async def finalize(db, scan):
    row = await quota.account(db, scan.tenant_id)
    if row.phase != "counting" or row.scan_id != scan.id or not inventory.matches_configuration(scan):
        raise InventoryStopped()
    unresolved = await db.scalar(select(func.count()).select_from(StorageWrite).where(StorageWrite.tenant_id == scan.tenant_id,
        StorageWrite.state.in_(quota.PENDING)))
    unavailable = await db.scalar(select(func.count()).select_from(StorageInventoryItem).where(StorageInventoryItem.scan_id == scan.id,
        StorageInventoryItem.kind == "upload", StorageInventoryItem.upload_state != "listed"))
    if unresolved or unavailable or row.reserved_bytes:
        raise ValueError("La línea base no está libre de operaciones pendientes")
    total = int(await db.scalar(select(func.coalesce(func.sum(StorageInventoryItem.byte_size), 0)).where(StorageInventoryItem.scan_id == scan.id)) or 0)
    now = datetime.now(timezone.utc)
    if not await update_active_job(db, scan.job_id, status="completed", finished_at=now,
        result={"scan_id": str(scan.id), "baseline_bytes": total, "quota_enforced": True, "external_writers_excluded": True}):
        raise InventoryStopped()
    row.used_bytes = total; row.reserved_bytes = 0; row.location_hash = scan.location_hash; row.baseline_at = now; row.phase = "ready"
    scan.phase = "completed"; scan.completed_at = now; scan.error = None
    await commit_keep_rls(db, tenant_id=scan.tenant_id)


@serialized_job
async def recount_storage(ctx, *, job_id, tenant_id, scan_id):
    scan_id = uuid.UUID(str(scan_id))
    async with SessionLocal() as db:
        await set_rls_context(db, tenant_id=tenant_id)
        try:
            row = await quota.account(db, tenant_id)
            scan = await db.scalar(select(StorageScan).where(StorageScan.id == scan_id, StorageScan.tenant_id == tenant_id, StorageScan.job_id == job_id))
            if not scan or row.scan_id != scan.id or row.phase not in {"draining", "counting"}: return
            if not inventory.matches_configuration(scan): raise ValueError("Destino modificado")
            pending = int(await db.scalar(select(func.count()).select_from(StorageWrite).where(StorageWrite.tenant_id == tenant_id,
                StorageWrite.state.in_(quota.PENDING))) or 0)
            if pending or row.reserved_bytes: raise ValueError("Hay escrituras pendientes o inciertas")
            row.phase = "counting"; scan.started_at = scan.started_at or datetime.now(timezone.utc)
            if not await update_active_job(db, job_id, status="running", started_at=datetime.now(timezone.utc)): return
            await commit_keep_rls(db, tenant_id=tenant_id)
            await process(db, scan, storage._s3(), finalize=finalize)
        except InventoryStopped:
            await db.rollback()
        except Exception:
            await db.rollback(); await set_rls_context(db, tenant_id=tenant_id)
            scan = await db.get(StorageScan, scan_id)
            if not scan: return
            scan.error = ("Recuento protegido incompleto. Espera las reservas previas y verifica los recibos inciertos; "
                "revisa permisos y paginación S3. Reintentar conserva el bloqueo; cancelar recupera la cuota anterior, sin liberar bytes estimados.")
            await update_active_job(db, job_id, status="error", error=scan.error, finished_at=datetime.now(timezone.utc))
            await commit_keep_rls(db, tenant_id=tenant_id)
