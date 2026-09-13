"""Quota transactions are independent of the caller's document transaction.

The quota row is the sole lock order root: quota -> write. No Tenant FOR UPDATE,
no optimistic credit for overwrites/deletes, and no automatic release on timeout.
"""
from __future__ import annotations
import asyncio
import hashlib
import uuid
from datetime import datetime, timezone
from fastapi import HTTPException
from botocore.exceptions import ClientError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from ...core.execution_context import current_job
from ...db.rls import set_rls_context
from ...db.session import SessionLocal
from ...models.storage_quota import StorageQuota, StorageWrite
from ...settings import settings
from . import inventory

PENDING = ("reserved", "sending", "unknown")


class DeletionStillPresent(HTTPException):
    def __init__(self):
        super().__init__(409, "La versión sigue presente; no se da la eliminación por confirmada.")


def scope(bucket, key):
    try:
        tenant_id = uuid.UUID(key.split("/", 1)[0])
        inventory.validate_key(tenant_id, key)
        buckets, digest = inventory.configuration()
        if bucket not in buckets: raise ValueError()
        return tenant_id, digest
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(409, "La escritura no pertenece al almacenamiento de un espacio válido.") from None


async def account(db, tenant_id):
    await db.execute(insert(StorageQuota).values(tenant_id=tenant_id, phase="uninitialized", used_bytes=0, reserved_bytes=0)
        .on_conflict_do_nothing(index_elements=["tenant_id"]))
    return await db.scalar(select(StorageQuota).where(StorageQuota.tenant_id == tenant_id).with_for_update())


async def initialize_empty(db, row):
    from ...core import storage
    buckets, digest = inventory.configuration()
    # Four bounded metadata requests while the quota row excludes other admissions.
    # Existing data is NEVER assigned zero; a background fenced recount is required.
    for bucket in buckets:
        for reader in (inventory.versions_page, inventory.uploads_page):
            try:
                values, cursor = await asyncio.to_thread(reader, storage._s3(), bucket, row.tenant_id, {})
            except Exception:
                raise HTTPException(503, "S3 no permite verificar la cuota inicial; revisa permisos y conexión antes de escribir.") from None
            if values or cursor:
                raise HTTPException(409, "El espacio ya contiene objetos. Inicia el recuento protegido de cuota en Ajustes antes de escribir.")
    row.used_bytes = 0; row.phase = "ready"; row.location_hash = digest; row.baseline_at = datetime.now(timezone.utc)


async def reserve(db, tenant_id, bucket, key, amount, *, kind="put", write_id=None, target_version_id=None):
    actual_tenant, digest = scope(bucket, key)
    if actual_tenant != tenant_id or type(amount) is not int or not 0 <= amount <= 2**63 - 1:
        raise HTTPException(422, "Reserva de almacenamiento no válida.")
    row = await account(db, tenant_id)
    if row.phase in {"draining", "counting"}:
        raise HTTPException(409, "La cuota está en recuento protegido; no admite nuevas escrituras ni llamadas de IA de pago.")
    deleting = kind in {"delete", "delete_version"}
    if row.phase == "uninitialized" and not deleting:
        await initialize_empty(db, row)
    if row.location_hash is not None and row.location_hash != digest:
        raise HTTPException(409, "El almacenamiento cambió. Recuenta la cuota en el nuevo destino antes de escribir.")
    if not deleting and row.used_bytes + row.reserved_bytes + amount > settings.storage_tenant_quota_bytes:
        raise HTTPException(413, "Cuota de almacenamiento insuficiente; cuentan originales, derivados y reservas pendientes. Revisa Ajustes.")
    key_hash = hashlib.sha256(key.encode()).hexdigest()
    pending = await db.scalar(select(StorageWrite.id).where(StorageWrite.tenant_id == tenant_id,
        StorageWrite.location_hash == digest, StorageWrite.bucket == bucket, StorageWrite.key_hash == key_hash,
        StorageWrite.state.in_(PENDING)).limit(1))
    if pending: raise HTTPException(409, "Este objeto tiene una escritura pendiente o incierta; revisa su recibo antes de repetirla.")
    context = current_job.get()
    write = StorageWrite(id=write_id or uuid.uuid4(), tenant_id=tenant_id,
        job_id=context[1] if context and context[0] == tenant_id else None, kind=kind, state="reserved",
        bucket=bucket, storage_key=key, key_hash=key_hash, location_hash=digest, reserved_bytes=amount,
        target_version_id=target_version_id)
    row.reserved_bytes += amount; db.add(write); await db.flush()
    return write


async def reserve_independent(bucket, key, amount, **kwargs):
    tenant_id, _ = scope(bucket, key)
    async with SessionLocal() as db:
        await set_rls_context(db, tenant_id=tenant_id)
        write = await reserve(db, tenant_id, bucket, key, amount, **kwargs)
        await db.commit()  # Session ends here; never commits the caller's request.
        return write


async def begin(bucket, key, amount, write_id):
    tenant_id, digest = scope(bucket, key)
    async with SessionLocal() as db:
        await set_rls_context(db, tenant_id=tenant_id)
        row = await account(db, tenant_id)
        write = await db.scalar(select(StorageWrite).where(StorageWrite.id == write_id, StorageWrite.tenant_id == tenant_id).with_for_update())
        if (not write or write.state != "reserved" or write.bucket != bucket or write.storage_key != key
                or write.location_hash != digest or amount > write.reserved_bytes or amount < 0
                or row.phase == "counting"):
            raise HTTPException(409, "La reserva de este objeto no permite iniciar la escritura.")
        # Prepaid AI responses may finish during draining. Counting starts only after
        # ALL reservations, sending operations and uncertain outcomes have drained.
        write.state = "sending"; await db.commit()
        return write


async def record_upload(write, upload_id):
    upload_id = inventory.opaque(upload_id, 2048)
    async with SessionLocal() as db:
        await set_rls_context(db, tenant_id=write.tenant_id)
        row = await db.get(StorageWrite, write.id)
        if not row or row.state != "sending": raise HTTPException(409, "La carga ya no está autorizada.")
        row.upload_id = upload_id; await db.commit()
    write.upload_id = upload_id


async def finish(write, actual_bytes, version_id=None):
    if type(actual_bytes) is not int or not 0 <= actual_bytes <= write.reserved_bytes:
        raise ValueError("El resultado supera la reserva de almacenamiento")
    if version_id is not None: inventory.opaque(version_id, 1024)
    async with SessionLocal() as db:
        await set_rls_context(db, tenant_id=write.tenant_id)
        quota = await account(db, write.tenant_id)
        row = await db.scalar(select(StorageWrite).where(StorageWrite.id == write.id).with_for_update())
        if row and row.state == "succeeded": return
        if not row or row.state not in {"sending", "unknown"} or quota.phase == "counting":
            raise HTTPException(409, "El recibo de almacenamiento cambió; la reserva se conserva.")
        quota.reserved_bytes -= row.reserved_bytes; quota.used_bytes += actual_bytes
        row.state = "succeeded"; row.actual_bytes = actual_bytes; row.version_id = version_id
        row.error = None; row.finished_at = datetime.now(timezone.utc)
        await db.commit()


async def unknown(write):
    async with SessionLocal() as db:
        await set_rls_context(db, tenant_id=write.tenant_id)
        await account(db, write.tenant_id)
        row = await db.scalar(select(StorageWrite).where(StorageWrite.id == write.id).with_for_update())
        if row and row.state == "sending":
            row.state = "unknown"; row.error = "Resultado S3 incierto; reserva retenida. No se repite automáticamente."
            await db.commit()


async def cancel_unused(db, tenant_id, write_id):
    """Only a reservation that has never sent an S3 request can be released."""
    quota = await account(db, tenant_id)
    row = await db.scalar(select(StorageWrite).where(StorageWrite.id == write_id, StorageWrite.tenant_id == tenant_id).with_for_update())
    if row and row.state == "reserved":
        quota.reserved_bytes -= row.reserved_bytes; row.state = "cancelled"; row.finished_at = datetime.now(timezone.utc)
        return True
    return False


async def recheck(write):
    """Read-only reconciliation. Absence never proves that a PUT will not arrive later."""
    from ...core import storage
    from ...core.storage_transfers import checked_head
    _, digest = scope(write.bucket, write.storage_key)
    if write.location_hash != digest or write.state not in {"sending", "unknown"}:
        raise HTTPException(409, "El recibo no pertenece a una escritura incierta del destino actual.")
    if write.kind == "delete_version":
        try:
            await asyncio.to_thread(storage._s3().head_object, Bucket=write.bucket, Key=write.storage_key, VersionId=write.target_version_id)
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") not in {"404", "NoSuchKey", "NoSuchVersion", "NotFound"}: raise
            await finish(write, 0)
            return
        raise DeletionStillPresent()
    if write.kind == "delete":
        raise HTTPException(409, "Un borrado sin versión tiene resultado incierto. Se conserva el recibo y no se presume espacio liberado.")
    head = await asyncio.to_thread(storage._s3().head_object, Bucket=write.bucket, Key=write.storage_key)
    amount = inventory.byte_size(head.get("ContentLength"))
    if amount > write.reserved_bytes: raise HTTPException(409, "El objeto supera su reserva; requiere revisión del almacenamiento.")
    version = await checked_head(storage._s3(), write, amount, head.get("VersionId"))
    await finish(write, amount, version)


async def known_deletion(bucket, key, version_id):
    tenant_id, digest = scope(bucket, key)
    async with SessionLocal() as db:
        await set_rls_context(db, tenant_id=tenant_id)
        write = await db.scalar(select(StorageWrite).where(StorageWrite.tenant_id == tenant_id,
            StorageWrite.location_hash == digest, StorageWrite.bucket == bucket,
            StorageWrite.key_hash == hashlib.sha256(key.encode()).hexdigest(), StorageWrite.state.in_(("sending", "unknown"))))
    if write and write.kind == "delete_version" and write.target_version_id == version_id:
        try:
            await recheck(write)
        except DeletionStillPresent:
            # Only the same serialized, permanently fenced staging-cleanup job may
            # repeat its authorized exact deletion. Never a generic UI retry of PUT
            # or an unversioned delete against a reusable document namespace.
            context = current_job.get()
            if not context or context != (tenant_id, write.job_id): raise
            async with SessionLocal() as db:
                await set_rls_context(db, tenant_id=tenant_id)
                state = await account(db, tenant_id)
                previous = await db.scalar(select(StorageWrite).where(StorageWrite.id == write.id).with_for_update())
                if state.phase == "counting" or previous.state not in {"sending", "unknown"}: raise
                previous.state = "reserved"; previous.error = None
                await db.commit()
            return write.id
        return True
    return False
