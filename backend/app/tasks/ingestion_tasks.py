"""Validate immutable upload parts and publish complete books with bounded memory."""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
import warnings
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image
from pypdf import PdfReader
from sqlalchemy import func, insert, literal, select

from ..core import events, storage
from ..core.dispatch import schedule
from ..core.job_lifecycle import job_is_active, update_active_job
from ..core.task_execution import serialized_job
from ..db.rls import commit_keep_rls, set_rls_context
from ..db.session import SessionLocal
from ..models.document import Document, Page
from ..models.ingest import IngestBook, IngestFile, IngestSession
from ..models.job import Job
from ..modules.documents.ingestion import CHUNK_BYTES
from ..modules.documents.service import _EXT, _resolve_municipality


class IngestStopped(Exception):
    pass


async def _publish(tenant_id, job_id, data):
    try:
        await events.publish(tenant_id, job_id, data)
    except Exception:
        pass


async def _check_running(db, session_id, job_id):
    active_job = await db.scalar(select(IngestSession.job_id).where(IngestSession.id == session_id))
    if active_job != job_id or not await job_is_active(db, job_id):
        raise IngestStopped()


async def assemble_file(bucket, item, destination, progress):
    """Verify every stored part while streaming; never concatenate the collection in RAM."""
    expected_offset = 0
    full_hash = hashlib.sha256()
    with open(destination, "wb") as target:
        for offset, part in sorted(item.parts.items(), key=lambda pair: int(pair[0])):
            if offset != str(expected_offset) or part["size"] != min(CHUNK_BYTES, item.byte_size - expected_offset):
                raise ValueError("Fragmentos incompletos o desordenados")
            obj = await storage.open_object(bucket, part["key"])
            digest, count = hashlib.sha256(), 0
            try:
                while block := await asyncio.to_thread(obj["Body"].read, 1024 ** 2):
                    count += len(block)
                    if count > part["size"]:
                        raise ValueError("Fragmento de tamaño incorrecto")
                    digest.update(block)
                    full_hash.update(block)
                    await asyncio.to_thread(target.write, block)
            finally:
                await asyncio.to_thread(obj["Body"].close)
            if count != part["size"] or digest.hexdigest() != part["sha256"]:
                raise ValueError("Fragmento incompleto o checksum incorrecto")
            expected_offset += count
            await progress(expected_offset)
    if expected_offset != item.byte_size:
        raise ValueError("El archivo está incompleto")
    return full_hash.hexdigest()


def inspect_original(path, kind):
    if kind == "pdf":
        with open(path, "rb") as source:
            pdf = PdfReader(source)
            if pdf.is_encrypted:
                raise ValueError("PDF cifrado")
            count = len(pdf.pages)
            if not 0 < count <= 100_000:
                raise ValueError("Número de páginas PDF no válido")
        return {"content_type": "application/pdf", "page_count": count, "width": None, "height": None}
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(path) as image:
            content_type = Image.MIME.get(image.format)
            if content_type not in _EXT or getattr(image, "n_frames", 1) != 1 or image.width * image.height > 64_000_000:
                raise ValueError("Imagen no admitida o con varias páginas")
            width, height = image.size
            image.load()  # Validate actual compressed pixels, not only the filename/header.
        return {"content_type": content_type, "width": width, "height": height, "page_count": 1}


async def _prepare_file(db, tenant_id, session_id, job_id, book, item):
    if item.status == "verified":
        return
    bucket = storage.bucket_for("private")
    doc_id = uuid.uuid5(book.id, "document")

    async def progress(offset):
        await _check_running(db, session_id, job_id)
        await update_active_job(db, job_id, progress={"stage": "verify_originals", "file": item.relative_path,
                                                     "file_bytes": offset, "file_size": item.byte_size})
        await commit_keep_rls(db, tenant_id=tenant_id)

    with TemporaryDirectory(prefix="gensuite-ingest-") as tmp:
        path = str(Path(tmp) / "original")
        digest = await assemble_file(bucket, item, path, progress)
        info = await asyncio.to_thread(inspect_original, path, book.kind)
        key = f"{tenant_id}/{doc_id}/" + ("original.pdf" if book.kind == "pdf" else f"pages/{item.position}{_EXT[info['content_type']]}")
        await storage.upload_file(bucket, key, path, info["content_type"])
        await _check_running(db, session_id, job_id)
        item.storage_key, item.checksum, item.status, item.error = key, digest, "verified", None
        for name, value in info.items():
            setattr(item, name, value)
        await commit_keep_rls(db, tenant_id=tenant_id)


async def _write_manifest(db, book, path):
    """Ordered original manifest, streamed from keyset-paginated rows to a temporary file."""
    after = 0
    first = True
    with open(path, "w", encoding="utf-8") as output:
        output.write('{"schema_version":1,"ingest_session_id":' + json.dumps(str(book.session_id)) + ',"files":[')
        while True:
            batch = (await db.scalars(select(IngestFile).where(IngestFile.book_id == book.id, IngestFile.position > after)
                                      .order_by(IngestFile.position).limit(500))).all()
            if not batch:
                break
            for item in batch:
                entry = {"filename": item.relative_path, "byte_size": item.byte_size, "sha256": item.checksum,
                         "position": item.position, "last_modified": item.last_modified}
                output.write(("" if first else ",") + json.dumps(entry, ensure_ascii=False))
                first = False
            after = batch[-1].position
        output.write("]}")


async def _publish_book(db, tenant_id, session_id, job_id, book):
    await _check_running(db, session_id, job_id)
    count = await db.scalar(select(func.count(IngestFile.id)).where(IngestFile.book_id == book.id))
    unverified = await db.scalar(select(IngestFile.id).where(IngestFile.book_id == book.id, IngestFile.status != "verified").limit(1))
    if not count or unverified:
        raise ValueError("Faltan originales verificados")
    doc_id, bucket = uuid.uuid5(book.id, "document"), storage.bucket_for("private")
    prefix = f"{tenant_id}/{doc_id}/"
    with TemporaryDirectory(prefix="gensuite-manifest-") as tmp:
        manifest = str(Path(tmp) / "original-manifest.json")
        await _write_manifest(db, book, manifest)
        await storage.upload_file(bucket, prefix + "original-manifest.json", manifest, "application/json")
    # Publish only a complete book, and serialize against retries/cancellation of the collection.
    collection = await db.scalar(select(IngestSession).where(IngestSession.id == session_id).with_for_update())
    await _check_running(db, session_id, job_id)
    book = await db.scalar(select(IngestBook).where(IngestBook.id == book.id).with_for_update().execution_options(populate_existing=True))
    if book.document_id:
        return
    options = book.options
    place_id = await _resolve_municipality(db, tenant_id, options.get("municipality"), options.get("municipality_lat"), options.get("municipality_lng"))
    first = await db.scalar(select(IngestFile).where(IngestFile.book_id == book.id).order_by(IngestFile.position).limit(1))
    doc = Document(id=doc_id, tenant_id=tenant_id, created_by=collection.created_by, title=book.title,
        doc_type=book.kind, source_kind="resumable_upload", visibility="private", image_policy="retain",
        storage_bucket=bucket, storage_prefix=prefix, page_count=first.page_count if book.kind == "pdf" else count,
        fingerprint=first.checksum if book.kind == "pdf" else None,
        may_contain_living=options.get("may_contain_living", False), source_origin=options.get("source_origin"),
        default_record_type=options.get("record_type"), place_id=place_id,
        year_from=options.get("year_from"), year_to=options.get("year_to"), book_number=options.get("book_number"),
        is_index=options.get("is_index", False))
    db.add(doc)
    await db.flush()
    if book.kind == "image_set":
        # One database statement even for a book containing 100,000 scans; no 100k ORM objects.
        await db.execute(insert(Page).from_select(
            ["id", "tenant_id", "document_id", "visibility", "page_no", "storage_key", "content_type",
             "byte_size", "width", "height", "original_filename", "checksum", "kind"],
            select(IngestFile.id, IngestFile.tenant_id, literal(doc_id), literal("private"),
                IngestFile.position, IngestFile.storage_key, IngestFile.content_type, IngestFile.byte_size,
                IngestFile.width, IngestFile.height, IngestFile.relative_path, IngestFile.checksum,
                literal("index" if doc.is_index else "record")).where(IngestFile.book_id == book.id)))
    else:
        raster = Job(tenant_id=tenant_id, created_by=collection.created_by, type="rasterize", status="queued",
                     params={"document_id": str(doc_id)})
        db.add(raster)
        await schedule(db, raster, "rasterize_document", document_id=str(doc_id))
    book.document_id, book.status, book.error = doc_id, "completed", None
    await commit_keep_rls(db, tenant_id=tenant_id)


@serialized_job
async def ingest_collection(ctx, *, job_id, tenant_id, session_id):
    session_id = uuid.UUID(str(session_id))
    async with SessionLocal() as db:
        await set_rls_context(db, tenant_id=tenant_id)
        if not await update_active_job(db, job_id, status="running", started_at=datetime.now(timezone.utc)):
            return
        await commit_keep_rls(db, tenant_id=tenant_id)
        after, complete, failed = None, 0, 0
        try:
            while True:
                await _check_running(db, session_id, job_id)
                stmt = select(IngestBook).where(IngestBook.session_id == session_id)
                if after:
                    stmt = stmt.where(IngestBook.id > after)
                book = await db.scalar(stmt.order_by(IngestBook.id).limit(1))
                if book is None:
                    break
                after = book.id
                if book.status == "completed":
                    complete += 1
                    continue
                current_file_id = None
                try:
                    position = 0
                    while True:
                        item = await db.scalar(select(IngestFile).where(IngestFile.book_id == book.id, IngestFile.position > position)
                                               .order_by(IngestFile.position).limit(1))
                        if item is None:
                            break
                        position, current_file_id = item.position, item.id
                        await _prepare_file(db, tenant_id, session_id, job_id, book, item)
                    await _publish_book(db, tenant_id, session_id, job_id, book)
                    complete += 1
                except IngestStopped:
                    raise
                except Exception:
                    await db.rollback()
                    await set_rls_context(db, tenant_id=tenant_id)
                    await _check_running(db, session_id, job_id)
                    book = await db.get(IngestBook, after)
                    book.status, book.error = "error", "No se pudo verificar o publicar el libro; revisa los originales y el almacenamiento. Puedes reintentar sin repetir la subida."
                    if current_file_id:
                        item = await db.get(IngestFile, current_file_id)
                        if item.status != "verified":
                            item.status, item.error = "error", book.error
                    failed += 1
                    await commit_keep_rls(db, tenant_id=tenant_id)
                await update_active_job(db, job_id, progress={"stage": "publish_books", "completed": complete, "failed": failed})
                await commit_keep_rls(db, tenant_id=tenant_id)
                await _publish(tenant_id, job_id, {"kind": "page_ok", "done": complete + failed, "completed": complete, "failed": failed})
            collection = await db.scalar(select(IngestSession).where(IngestSession.id == session_id).with_for_update())
            await _check_running(db, session_id, job_id)
            status = "partial" if failed and complete else "error" if failed else "completed"
            changed = await update_active_job(db, job_id, status=status, finished_at=datetime.now(timezone.utc),
                result={"session_id": str(session_id), "books": complete, "failed": failed},
                error=f"{failed} libros requieren revisión" if failed else None)
            if changed:
                collection.status = status
            await commit_keep_rls(db, tenant_id=tenant_id)
            if changed:
                await _publish(tenant_id, job_id, {"kind": "book_fail" if failed else "all_done", "books": complete, "failed": failed})
        except IngestStopped:
            await db.rollback()
            await _publish(tenant_id, job_id, {"kind": "cancelled"})
