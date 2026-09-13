"""Fenced staging cleanup, keyset checkpoints and exact S3 version deletion only."""
from __future__ import annotations
import asyncio
import hashlib
import re
import uuid
from datetime import datetime, timezone
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from botocore.exceptions import ClientError
from ..core import storage
from ..core.job_lifecycle import job_is_active, update_active_job
from ..core.task_execution import serialized_job
from ..db.rls import commit_keep_rls, set_rls_context
from ..db.session import SessionLocal
from ..models.ingest import IngestBook, IngestFile, IngestSession
from ..models.ingest_cleanup import IngestCleanup, IngestCleanupObject
from ..models.tenant import Tenant
from ..modules.documents.ingestion import CHUNK_BYTES
from ..modules.documents.service import _EXT
from ..modules.providers.admission import fingerprint
from ..modules.storage_audit.inventory import cursor_identifier


class CleanupStopped(Exception):
    pass


def staging_prefix(tenant_id, session_id):
    return f"{uuid.UUID(str(tenant_id))}/ingestion/{uuid.UUID(str(session_id))}/"


def validate_staging_key(tenant_id, session_id, key):
    prefix = staging_prefix(tenant_id, session_id)
    if not isinstance(key, str) or not key.startswith(prefix):
        raise ValueError("Objeto fuera del staging autorizado")
    match = re.fullmatch(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/([0-9]{16})-([0-9a-f]{64})", key[len(prefix):])
    if not match or int(match[2]) % CHUNK_BYTES:
        raise ValueError("El objeto no tiene el formato de un fragmento de subida")
    return uuid.UUID(match[1])


def validate_staging_cursor(tenant_id, session_id, cursor):
    if not isinstance(cursor, dict) or (cursor and set(cursor) != {"KeyMarker", "VersionIdMarker"}):
        raise ValueError("Cursor no válido")
    if cursor:
        key = cursor["KeyMarker"]
        # A listing cursor may contain provider metadata. It is NOT a deletion key;
        # actual object keys still pass the strict fragment validator independently.
        if (not isinstance(key, str) or not key.startswith(staging_prefix(tenant_id, session_id))
                or "\x00" in key or len(key.encode("utf-8")) > 2048):
            raise ValueError("Cursor fuera del staging autorizado")
        cursor_identifier(cursor["VersionIdMarker"], "VersionIdMarker")


def version_page(client, bucket, tenant_id, session_id, cursor):
    """Bounded list; never infer version IDs from a missing field or delete a prefix."""
    validate_staging_cursor(tenant_id, session_id, cursor)
    result = client.list_object_versions(Bucket=bucket, Prefix=staging_prefix(tenant_id, session_id), MaxKeys=100, **cursor)
    if (result.get("Name") != bucket or result.get("Prefix") != staging_prefix(tenant_id, session_id)
            or type(result.get("IsTruncated")) is not bool):
        raise ValueError("El inventario no acredita el bucket, prefijo y final de página solicitados")
    objects = []
    for field, marker in (("Versions", False), ("DeleteMarkers", True)):
        for value in result.get(field, []):
            key, version = value.get("Key"), value.get("VersionId")
            validate_staging_key(tenant_id, session_id, key)
            if not isinstance(version, str) or not 0 < len(version) <= 1024 or not version.isascii() or any(ord(c) < 32 for c in version):
                raise ValueError("Falta una versión de almacenamiento válida")
            size = 0 if marker else value.get("Size")
            if type(size) is not int or not 0 <= size <= CHUNK_BYTES:
                raise ValueError("Tamaño de fragmento inesperado")
            objects.append({"storage_key": key, "version_id": version, "byte_size": size, "is_delete_marker": marker})
    if len(objects) > 100: raise ValueError("El almacenamiento no respetó el límite de página")
    if result.get("CommonPrefixes"): raise ValueError("No se admiten directorios agrupados en el manifiesto de eliminación")
    if cursor and (result.get("KeyMarker") != cursor["KeyMarker"]
                   or result.get("VersionIdMarker") != cursor["VersionIdMarker"]):
        raise ValueError("La respuesta no corresponde al cursor solicitado")
    cursor_next = {}
    if result.get("IsTruncated"):
        key, version = result.get("NextKeyMarker"), result.get("NextVersionIdMarker")
        cursor_next = {"KeyMarker": key, "VersionIdMarker": version}
        validate_staging_cursor(tenant_id, session_id, cursor_next)
        if not objects: raise ValueError("Cursor de versiones incompleto")
        if cursor_next == cursor: raise ValueError("El almacenamiento repite el cursor")
    return objects, cursor_next


def delete_version(client, bucket, tenant_id, session_id, item):
    validate_staging_key(tenant_id, session_id, item.storage_key)
    if not isinstance(item.version_id, str) or not item.version_id:
        raise ValueError("No se permite borrar sin versión explícita")
    try:
        # Includes the literal 'null' version in unversioned buckets. Never bypasses
        # Object Lock, retention or MFA Delete; permission failures stop the cleanup.
        client.delete_object(Bucket=bucket, Key=item.storage_key, VersionId=item.version_id)
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") not in {"NoSuchKey", "NoSuchVersion"}: raise


async def verify_original(bucket, tenant_id, book, item, heartbeat):
    doc_id = uuid.uuid5(book.id, "document")
    suffix = "original.pdf" if book.kind == "pdf" else f"pages/{item.position}{_EXT.get(item.content_type, '.invalid')}"
    if item.storage_key != f"{tenant_id}/{doc_id}/{suffix}" or not re.fullmatch(r"[a-f0-9]{64}", item.checksum or ""):
        raise ValueError("Original sin identidad verificable")
    obj = await storage.open_object(bucket, item.storage_key)
    digest, count = hashlib.sha256(), 0
    try:
        if obj.get("ContentLength") is not None and obj["ContentLength"] != item.byte_size:
            raise ValueError("Tamaño de original distinto")
        while block := await asyncio.to_thread(obj["Body"].read, 1024 ** 2):
            count += len(block)
            if count > item.byte_size: raise ValueError("El original supera su manifiesto")
            digest.update(block)
            if count % (8 * 1024 ** 2) == 0: await heartbeat()
    finally:
        await asyncio.to_thread(obj["Body"].close)
    if count != item.byte_size or digest.hexdigest() != item.checksum:
        raise ValueError("El original conservado no supera su checksum")


async def running(db, cleanup):
    state = await db.scalar(select(IngestSession.status).where(IngestSession.id == cleanup.session_id,
        IngestSession.tenant_id == cleanup.tenant_id))
    if state != "cleaning" or not await job_is_active(db, cleanup.job_id): raise CleanupStopped()
    if cleanup.bucket != storage.bucket_for("private"):
        raise ValueError("El bucket privado ya no coincide con el autorizado")


async def checkpoint(db, cleanup):
    await running(db, cleanup)
    await update_active_job(db, cleanup.job_id, progress={"phase": cleanup.phase, "inventory_epoch": cleanup.inventory_epoch})
    await commit_keep_rls(db, tenant_id=cleanup.tenant_id)


async def process(db, cleanup, client):
    while cleanup.phase == "verify_originals":
        await running(db, cleanup)
        statement = select(IngestFile).where(IngestFile.session_id == cleanup.session_id,
            IngestFile.tenant_id == cleanup.tenant_id)
        if cleanup.verified_after: statement = statement.where(IngestFile.id > cleanup.verified_after)
        item = await db.scalar(statement.order_by(IngestFile.id).limit(1))
        if not item:
            cleanup.phase = "inventory"; await checkpoint(db, cleanup); break
        book = await db.get(IngestBook, item.book_id)
        if not book or book.tenant_id != cleanup.tenant_id: raise ValueError("Libro no disponible")
        if cleanup.mode == "compact" and (item.status != "verified" or book.status != "completed" or not book.document_id):
            raise ValueError("La colección completada no conserva todos sus originales")
        if item.status == "verified":
            await verify_original(cleanup.bucket, cleanup.tenant_id, book, item, lambda: checkpoint(db, cleanup))
        cleanup.verified_after = item.id
        await checkpoint(db, cleanup)
    while cleanup.phase == "inventory":
        await running(db, cleanup)
        values, cursor = await asyncio.to_thread(version_page, client, cleanup.bucket, cleanup.tenant_id, cleanup.session_id, cleanup.inventory_cursor)
        if values:
            rows = [{**value, "id": uuid.uuid5(cleanup.id, fingerprint({"epoch": cleanup.inventory_epoch,
                "key": value["storage_key"], "version": value["version_id"]})),
                "tenant_id": cleanup.tenant_id, "cleanup_id": cleanup.id, "inventory_epoch": cleanup.inventory_epoch} for value in values]
            await db.execute(insert(IngestCleanupObject).values(rows).on_conflict_do_nothing(index_elements=["id"]))
        cleanup.inventory_cursor = cursor
        if not cursor:
            # The session is fenced and its originals have passed verification. Remove
            # obsolete upload references before deletion, so a backup during an interrupted
            # cleanup does not mistake disposable/missing chunks for irreplaceable evidence.
            await db.execute(update(IngestFile).where(IngestFile.session_id == cleanup.session_id,
                IngestFile.tenant_id == cleanup.tenant_id).values(parts={}))
            cleanup.phase = "delete"
        await checkpoint(db, cleanup)
    while cleanup.phase == "delete":
        await running(db, cleanup)
        rows = (await db.scalars(select(IngestCleanupObject).where(IngestCleanupObject.cleanup_id == cleanup.id,
            IngestCleanupObject.inventory_epoch == cleanup.inventory_epoch, IngestCleanupObject.deleted_at.is_(None))
            .order_by(IngestCleanupObject.id).limit(100))).all()
        if not rows:
            cleanup.phase = "verify_empty"; await checkpoint(db, cleanup); break
        for item in rows:
            await running(db, cleanup)
            from ..core.storage_transfers import execute
            from ..modules.storage_audit.quota import known_deletion
            validate_staging_key(cleanup.tenant_id, cleanup.session_id, item.storage_key)
            receipt = await known_deletion(cleanup.bucket, item.storage_key, item.version_id)
            if receipt is not True:
                async def send(write):
                    await asyncio.to_thread(delete_version, client, cleanup.bucket, cleanup.tenant_id, cleanup.session_id, item)
                    return {}
                await execute(client, cleanup.bucket, item.storage_key, 0, "delete_version", send,
                    write_id=receipt or None, target_version_id=item.version_id)
            item.deleted_at = datetime.now(timezone.utc)
            # Acknowledgement and progress are durable per object. Lost acknowledgements
            # are safe to repeat against the exact same immutable version ID.
            await checkpoint(db, cleanup)
    if cleanup.phase == "verify_empty":
        await running(db, cleanup)
        remaining, cursor = await asyncio.to_thread(version_page, client, cleanup.bucket, cleanup.tenant_id, cleanup.session_id, {})
        if remaining or cursor:
            raise ValueError("Quedan versiones fuera del manifiesto; no se amplía la eliminación ni se libera cuota")
        await db.scalar(select(Tenant.id).where(Tenant.id == cleanup.tenant_id).with_for_update(key_share=True))
        session = await db.scalar(select(IngestSession).where(IngestSession.id == cleanup.session_id,
            IngestSession.tenant_id == cleanup.tenant_id).with_for_update())
        await running(db, cleanup)
        cleanup.phase = "completed"; cleanup.completed_at = datetime.now(timezone.utc); cleanup.error = None
        session.status = "completed" if cleanup.mode == "compact" else "discarded"
        stats = (await db.execute(select(func.count(), func.coalesce(func.sum(IngestCleanupObject.byte_size), 0))
            .where(IngestCleanupObject.cleanup_id == cleanup.id, IngestCleanupObject.inventory_epoch == cleanup.inventory_epoch))).one()
        await update_active_job(db, cleanup.job_id, status="completed", finished_at=cleanup.completed_at,
            result={"session_id": str(session.id), "deleted_versions": stats[0], "deleted_version_bytes": int(stats[1]),
                    "retained_reservation_bytes": cleanup.retained_reservation_bytes, "originals_deleted": False})
        await commit_keep_rls(db, tenant_id=cleanup.tenant_id)


@serialized_job
async def cleanup_ingestion(ctx, *, job_id, tenant_id, cleanup_id):
    cleanup_id = uuid.UUID(str(cleanup_id))
    async with SessionLocal() as db:
        await set_rls_context(db, tenant_id=tenant_id)
        cleanup = await db.scalar(select(IngestCleanup).where(IngestCleanup.id == cleanup_id, IngestCleanup.tenant_id == tenant_id,
            IngestCleanup.job_id == job_id))
        if not cleanup or cleanup.phase == "completed": return
        if not await update_active_job(db, job_id, status="running", started_at=datetime.now(timezone.utc)): return
        cleanup.error = None
        await commit_keep_rls(db, tenant_id=tenant_id)
        try:
            await process(db, cleanup, storage._s3())
        except CleanupStopped:
            await db.rollback()
        except Exception:
            await db.rollback(); await set_rls_context(db, tenant_id=tenant_id)
            cleanup = await db.get(IngestCleanup, cleanup_id)
            if cleanup is None: return
            reasons = {
                "verify_originals": "No se ha podido acreditar la integridad de los originales conservados. Revisa o recupera esos originales antes de reintentar.",
                "inventory": "No se ha podido completar el inventario de versiones. Revisa permisos, formato de objetos y paginación del almacenamiento.",
                "delete": "No se ha confirmado una eliminación de versión. Revisa conexión, permisos, Object Lock y retención; se conserva el mismo manifiesto para reintentar.",
                "verify_empty": "El almacenamiento no acredita el staging vacío. Puede haber versiones fuera del manifiesto: requieren revisión del operador; reintentar no amplía la eliminación autorizada.",
            }
            cleanup.error = reasons.get(cleanup.phase, "Limpieza detenida; revisa el estado persistido.") + " No se libera cuota hasta terminar."
            await update_active_job(db, job_id, status="error", error=cleanup.error, finished_at=datetime.now(timezone.utc))
            await commit_keep_rls(db, tenant_id=tenant_id)
