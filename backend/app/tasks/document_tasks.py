"""Non-destructive document generations and offline, versioned exports."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from PIL import Image
from sqlalchemy import inspect, or_, select, update

from ..core import events, storage
from ..core.task_execution import serialized_job
from ..db.rls import commit_keep_rls, set_rls_context
from ..db.session import SessionLocal
from ..models.document import Document, Page
from ..models.document_export import DocumentExport
from ..models.job import Job
from ..models.transcription import Transcription
from ..modules.documents.preservation import ConsultationPDF, PreservationBag, file_checksum
from ..modules.documents.pdfa import ExportCancelled, PdfaError, convert
from ..settings import settings
from ..modules.documents.service import _iter_rasterize_pdf

# MuPDF is not thread-safe. All document rendering in a worker uses one dedicated thread;
# object transfers stay asynchronous and do not occupy it.
_DOCUMENT_CPU = ThreadPoolExecutor(max_workers=1, thread_name_prefix="document-render")


async def _cpu(function, *args, **kwargs):
    return await asyncio.get_running_loop().run_in_executor(_DOCUMENT_CPU, partial(function, *args, **kwargs))


async def _publish(tenant_id, job_id, event):
    try:
        await events.publish(tenant_id, job_id, event)
    except Exception:
        pass  # DB status remains authoritative when pub/sub is unavailable.


async def _state(session, tenant_id, job_id):
    await set_rls_context(session, tenant_id=tenant_id)
    return await session.scalar(select(Job.status).where(Job.id == job_id))


async def _start(session, tenant_id, job_id):
    from sqlalchemy import func
    changed = await session.execute(update(Job).where(Job.id == job_id,
        Job.status.in_(("queued", "running"))).values(status="running",
        started_at=func.coalesce(Job.started_at, datetime.now(timezone.utc))))
    return bool(changed.rowcount)


async def _finish(session, tenant_id, job_id, *, result=None, error=None):
    await set_rls_context(session, tenant_id=tenant_id)
    changed = await session.execute(update(Job).where(
        Job.id == job_id, Job.status.in_(("queued", "running")),
    ).values(status="error" if error else "completed", result=result, error=error,
             finished_at=datetime.now(timezone.utc)))
    await commit_keep_rls(session, tenant_id=tenant_id)
    if changed.rowcount:
        await _publish(tenant_id, job_id, {"kind": "book_fail" if error else "all_done",
                       "error": error, **(result or {})})


async def _owned_document(session, tenant_id, document_id):
    doc = await session.scalar(select(Document).where(
        Document.id == document_id, Document.tenant_id == tenant_id,
    ).with_for_update())
    if doc is None:
        raise ValueError("Documento no encontrado")
    return doc


async def _raster_settings(session, tenant_id):
    from ..models.tenant import Tenant
    tenant = await session.get(Tenant, tenant_id)
    return {"dpi": int(tenant.raster_dpi or 300), "fmt": tenant.raster_format or "webp",
            "autosplit": bool(tenant.raster_autosplit)} if tenant else {"dpi": 300, "fmt": "webp", "autosplit": True}


async def _render(session, tenant_id, job_id, doc):
    job = await session.get(Job, job_id)
    options = (job.params or {}).get("raster_settings") or await _raster_settings(session, tenant_id)
    job.params = {**(job.params or {}), "raster_settings": options}
    if not await _start(session, tenant_id, job_id):
        return
    await commit_keep_rls(session, tenant_id=tenant_id)
    count = 0
    with TemporaryDirectory(prefix="gensuite-raster-") as tmp:
        source_path = str(Path(tmp) / "original.pdf")
        await storage.download_file(doc.storage_bucket, f"{doc.storage_prefix}original.pdf", source_path)
        iterator = _iter_rasterize_pdf(source_path, **options)
        try:
            while True:
                if await _state(session, tenant_id, job_id) not in ("running", "queued"):
                    return
                rendered = await _cpu(next, iterator, None)
                if rendered is None:
                    break
                count += 1
                image, ct, physical_no, region = rendered
                previous = await session.scalar(select(Page).where(Page.document_id == doc.id, Page.page_no == count))
                if previous is None:
                    digest = hashlib.sha256(image).hexdigest()
                    extension = "webp" if ct == "image/webp" else "jpg"
                    key = f"{doc.storage_prefix}pages/{count}.{extension}"
                    await storage.put_object(doc.storage_bucket, key, image, ct)
                    with Image.open(io.BytesIO(image)) as im:
                        width, height = im.size
                    session.add(Page(tenant_id=tenant_id, document_id=doc.id, visibility=doc.visibility,
                        page_no=count, storage_key=key, content_type=ct, byte_size=len(image),
                        width=width, height=height, checksum=digest,
                        source_page_no=physical_no, source_region=region))
                await session.execute(update(Job).where(Job.id == job_id).values(
                    progress={"done": count, "total": max(count, doc.page_count)}))
                await commit_keep_rls(session, tenant_id=tenant_id)
                await _publish(tenant_id, job_id, {"kind": "page_ok", "done": count, "total": max(count, doc.page_count)})
        finally:
            await _cpu(iterator.close)
    if not count:
        raise ValueError("El PDF no contiene páginas")
    doc.page_count = count
    await _finish(session, tenant_id, job_id, result={"pages": count, "document_id": str(doc.id)})


@serialized_job
async def rasterize_document(ctx, *, job_id, tenant_id, document_id):
    async with SessionLocal() as session:
        await set_rls_context(session, tenant_id=tenant_id)
        try:
            doc = await _owned_document(session, tenant_id, uuid.UUID(str(document_id)))
            await _render(session, tenant_id, job_id, doc)
        except Exception:
            await session.rollback()
            await _finish(session, tenant_id, job_id, error="No se pudo rasterizar el PDF; los originales y páginas existentes se conservan.")


@serialized_job
async def rerasterize_document(ctx, *, job_id, tenant_id, document_id):
    """A new document generation; never delete or renumber the cited source's pages."""
    async with SessionLocal() as session:
        await set_rls_context(session, tenant_id=tenant_id)
        try:
            source = await _owned_document(session, tenant_id, uuid.UUID(str(document_id)))
            derived_id = uuid.uuid5(uuid.UUID(str(job_id)), "raster-generation")
            doc = await session.get(Document, derived_id)
            if doc is None:
                prefix = f"{tenant_id}/{derived_id}/"
                with TemporaryDirectory(prefix="gensuite-source-") as tmp:
                    original = str(Path(tmp) / "original.pdf")
                    await storage.download_file(source.storage_bucket, f"{source.storage_prefix}original.pdf", original)
                    await storage.upload_file(source.storage_bucket, f"{prefix}original.pdf", original, "application/pdf")
                doc = Document(id=derived_id, tenant_id=tenant_id, title=f"{source.title[:470]} (nueva digitalización)",
                    doc_type="pdf", visibility="private", derived_from_id=source.id,
                    source_kind=source.source_kind, source_origin=source.source_origin, source_ref=source.source_ref,
                    storage_bucket=source.storage_bucket, storage_prefix=prefix, page_count=source.page_count,
                    fingerprint=source.fingerprint, default_record_type=source.default_record_type,
                    place_id=source.place_id, year_from=source.year_from, year_to=source.year_to,
                    book_number=source.book_number, is_index=source.is_index, created_by=source.created_by,
                    image_policy=source.image_policy, may_contain_living=source.may_contain_living,
                    rights_declaration=source.rights_declaration)
                session.add(doc)
                await commit_keep_rls(session, tenant_id=tenant_id)
            await _render(session, tenant_id, job_id, doc)
        except Exception:
            await session.rollback()
            await _finish(session, tenant_id, job_id, error="No se pudo crear la nueva digitalización; el documento anterior no se ha modificado.")


def _json_row(row):
    excluded = {"embedding", "storage_bucket", "storage_prefix", "storage_key"}
    return {column.key: getattr(row, column.key) for column in inspect(type(row)).columns if column.key not in excluded}


async def _export(ctx, *, job_id, tenant_id, document_id, kind):
    async with SessionLocal() as session:
        await set_rls_context(session, tenant_id=tenant_id)
        try:
            doc = await _owned_document(session, tenant_id, uuid.UUID(str(document_id)))
            if not await _start(session, tenant_id, job_id):
                return
            export_id = uuid.uuid5(uuid.UUID(str(job_id)), kind)
            existing = await session.get(DocumentExport, export_id)
            if existing:
                await _finish(session, tenant_id, job_id, result={"export_id": str(export_id), "document_id": str(doc.id)})
                return
            pages = list((await session.scalars(select(Page).where(Page.document_id == doc.id).order_by(Page.page_no))).all())
            if not pages or any(p.image_purged for p in pages):
                raise ValueError("Faltan imágenes")
            is_pdf = kind in ("pdf", "pdfa")
            if kind == "pdfa" and (len(pages) > settings.pdfa_max_pages or
                sum(p.byte_size or 0 for p in pages) > settings.pdfa_max_bytes):
                raise PdfaError("El libro supera el límite del perfil PDF/A; divide el libro o ajusta su capacidad.")
            transcriptions = list((await session.scalars(select(Transcription).where(
                Transcription.document_id == doc.id).order_by(Transcription.created_at))).all())
            active = {t.page_no: t for t in transcriptions if t.is_active}
            snapshot = {str(t.page_no): str(t.id) for t in active.values()}
            manifest = {"schema_version": 1, "scope": "document", "document_id": str(doc.id),
                "transcriptions": snapshot, "pages": [_json_row(p) for p in pages],
                "pdfa": "not_validated", "text_mode": "appendix" if is_pdf else "json",
                "source_page_count": len(pages)}
            metadata = {"manifest": manifest, "document": _json_row(doc),
                        "transcriptions": [_json_row(t) for t in transcriptions]}
            if kind == "bagit":
                from ..models.record import Record
                from ..models.mention import PersonMention
                from ..models.citation import Citation
                from ..models.research import Source, Repository
                records = list((await session.scalars(select(Record).where(Record.document_id == doc.id))).all())
                record_ids = [r.id for r in records]
                citations = list((await session.scalars(select(Citation).where(or_(
                    Citation.record_id.in_(record_ids), Citation.page_id.in_([p.id for p in pages]),
                    Citation.transcription_id.in_([t.id for t in transcriptions]),
                )))).all())
                sources = list((await session.scalars(select(Source).where(or_(
                    Source.id.in_([c.source_id for c in citations if c.source_id]), Source.primary_document_id == doc.id,
                )))).all())
                metadata.update({"records": [_json_row(r) for r in records],
                    "mentions": [_json_row(m) for m in (await session.scalars(select(PersonMention).where(PersonMention.record_id.in_(record_ids)))).all()],
                    "citations": [_json_row(c) for c in citations], "sources": [_json_row(s) for s in sources],
                    "repositories": [_json_row(r) for r in (await session.scalars(select(Repository).where(Repository.id.in_([s.repository_id for s in sources if s.repository_id])))).all()]})
            await commit_keep_rls(session, tenant_id=tenant_id)
            with TemporaryDirectory(prefix="gensuite-export-") as tmp:
                extension = "pdf" if is_pdf else "zip"
                path = str(Path(tmp) / f"export.{extension}")
                writer = await _cpu(ConsultationPDF if is_pdf else PreservationBag, path)
                try:
                    if kind == "bagit" and doc.doc_type == "pdf":
                        original = str(Path(tmp) / "original.pdf")
                        await storage.download_file(doc.storage_bucket, f"{doc.storage_prefix}original.pdf", original)
                        if doc.fingerprint and await asyncio.to_thread(file_checksum, original) != doc.fingerprint:
                            raise ValueError("El PDF original no coincide con su checksum")
                        await asyncio.to_thread(writer.add_file, original, "data/originals/original.pdf")
                    if kind == "bagit":
                        from botocore.exceptions import ClientError
                        source_manifest = str(Path(tmp) / "original-manifest.json")
                        try:
                            await storage.download_file(doc.storage_bucket, f"{doc.storage_prefix}original-manifest.json", source_manifest)
                            await asyncio.to_thread(writer.add_file, source_manifest, "data/originals/original-manifest.json")
                        except ClientError as exc:
                            if str(exc.response.get("Error", {}).get("Code")) not in {"404", "NoSuchKey", "NotFound"}:
                                raise
                            metadata["warnings"] = ["Documento histórico sin manifest de importación original"]
                        for t in transcriptions:
                            await asyncio.to_thread(writer.add_text, t.text or "", f"data/transcriptions/{t.id}.txt")
                    for number, page in enumerate(pages, 1):
                        if await _state(session, tenant_id, job_id) != "running":
                            return
                        part = str(Path(tmp) / "page.bin")
                        await storage.download_file(doc.storage_bucket, page.storage_key, part)
                        checksum = await asyncio.to_thread(file_checksum, part)
                        if page.checksum and checksum != page.checksum:
                            raise ValueError("La imagen no coincide con su checksum")
                        if is_pdf:
                            image = await asyncio.to_thread(Path(part).read_bytes)
                            await _cpu(writer.add_image, image)
                        else:
                            ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/tiff": "tif"}.get(page.content_type, "bin")
                            entry = f"data/images/{page.id}.{ext}"
                            await asyncio.to_thread(writer.add_file, part, entry)
                            manifest["pages"][number - 1]["archive_path"] = entry
                        await session.execute(update(Job).where(Job.id == job_id).values(progress={"done": number, "total": len(pages)}))
                        await commit_keep_rls(session, tenant_id=tenant_id)
                    if is_pdf:
                        for page in pages:
                            if await _state(session, tenant_id, job_id) != "running":
                                return
                            t = active.get(page.page_no)
                            await _cpu(writer.add_transcription, page.page_no,
                                t.text if t else None, reviewed=bool(t and t.status == "corrected"))
                            await session.execute(update(Job).where(Job.id == job_id).values(
                                progress={"stage": "transcription_appendix", "done": page.page_no, "total": len(pages)}))
                            await commit_keep_rls(session, tenant_id=tenant_id)
                        manifest["output_page_count"] = writer.page_count
                    else:
                        await asyncio.to_thread(writer.add_json, metadata)
                finally:
                    await _cpu(writer.close)
                if kind == "pdfa":
                    async def checkpoint():
                        if await _state(session, tenant_id, job_id) != "running":
                            return False
                        await session.execute(update(Job).where(Job.id == job_id, Job.status == "running").values(
                            progress={"stage": "pdfa_conversion_validation", "done": len(pages), "total": len(pages)}))
                        await commit_keep_rls(session, tenant_id=tenant_id)
                        return True
                    converted, proof = await convert(Path(path), checkpoint=checkpoint)
                    path = str(converted)
                    manifest.update({"pdfa": "validated", "validation": proof})
                if await _state(session, tenant_id, job_id) != "running":
                    return
                key = f"{doc.storage_prefix}exports/{export_id}.{extension}"
                await storage.upload_file(doc.storage_bucket, key, path, "application/pdf" if is_pdf else "application/zip")
                session.add(DocumentExport(id=export_id, tenant_id=tenant_id, document_id=doc.id, kind=kind,
                    storage_key=key, checksum=await asyncio.to_thread(file_checksum, path),
                    byte_size=Path(path).stat().st_size, manifest=json.loads(json.dumps(manifest, default=str))))
                await _finish(session, tenant_id, job_id, result={"export_id": str(export_id), "document_id": str(doc.id), "kind": kind})
        except ExportCancelled:
            await session.rollback()
        except PdfaError as exc:
            await session.rollback()
            await _finish(session, tenant_id, job_id, error=str(exc))
        except Exception:
            await session.rollback()
            await _finish(session, tenant_id, job_id, error="No se pudo generar la exportación. Comprueba las imágenes y el almacenamiento; los originales se conservan.")


@serialized_job
async def compact_to_pdf(ctx, *, job_id, tenant_id, document_id):
    return await _export(ctx, job_id=job_id, tenant_id=tenant_id, document_id=document_id, kind="pdf")


@serialized_job
async def export_document_archive(ctx, *, job_id, tenant_id, document_id):
    return await _export(ctx, job_id=job_id, tenant_id=tenant_id, document_id=document_id, kind="bagit")


@serialized_job
async def export_document_pdfa(ctx, *, job_id, tenant_id, document_id):
    return await _export(ctx, job_id=job_id, tenant_id=tenant_id, document_id=document_id, kind="pdfa")
