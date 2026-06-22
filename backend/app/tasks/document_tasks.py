"""ARQ job: rasterize an uploaded PDF into per-page JPEGs (plan 2.2).

Moved out of the HTTP request: a book of hundreds of pages would time out the upload. The document
row + original PDF are created synchronously; this task renders each page with pymupdf, stores the
JPEGs in object storage, inserts ``Page`` rows, and reports progress via SSE — mirroring the
transcription/extraction job pattern.
"""
from __future__ import annotations

import asyncio
import io
import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, select, update

from ..core import events, storage
from ..db.rls import set_rls_context
from ..db.session import SessionLocal
from ..models.document import Document, Page
from ..models.job import Job
from ..modules.documents.service import _rasterize_pdf


async def _raster_settings(session, tenant_id) -> tuple[int, str, bool]:
    from ..models.tenant import Tenant
    t = await session.get(Tenant, tenant_id)
    if not t:
        return 300, "webp", True
    return int(t.raster_dpi or 300), (t.raster_format or "webp"), bool(t.raster_autosplit)


async def rasterize_document(ctx, *, job_id, tenant_id, document_id):
    job_id = uuid.UUID(str(job_id))
    tenant_id = uuid.UUID(str(tenant_id))
    document_id = uuid.UUID(str(document_id))

    async def pub(event: dict) -> None:
        await events.publish(tenant_id, job_id, event)

    async with SessionLocal() as session:
        await set_rls_context(session, tenant_id=tenant_id)
        await session.execute(
            update(Job).where(Job.id == job_id).values(status="running", started_at=datetime.now(timezone.utc))
        )
        await session.commit()

        await set_rls_context(session, tenant_id=tenant_id)
        doc = await session.get(Document, document_id)
        if not doc:
            await session.execute(update(Job).where(Job.id == job_id).values(status="error", error="document gone"))
            await session.commit()
            return
        dpi, fmt, autosplit = await _raster_settings(session, tenant_id)
        try:
            data, _ = await storage.get_object(doc.storage_bucket, f"{doc.storage_prefix}original.pdf")
            pages = await asyncio.to_thread(_rasterize_pdf, data, dpi=dpi, fmt=fmt, autosplit=autosplit)
        except Exception as exc:
            await set_rls_context(session, tenant_id=tenant_id)
            await session.execute(
                update(Job).where(Job.id == job_id).values(
                    status="error", error=str(exc)[:1000], finished_at=datetime.now(timezone.utc)
                )
            )
            await session.commit()
            await pub({"kind": "book_fail", "error": str(exc)[:300]})
            return

        total = len(pages)
        await pub({"kind": "book_start", "total": total, "engine": "pymupdf"})
        for i, (img, ct) in enumerate(pages, start=1):
            ext = "webp" if ct == "image/webp" else "jpg"
            key = f"{doc.storage_prefix}pages/{i}.{ext}"
            await storage.put_object(doc.storage_bucket, key, img, ct)
            width = height = None
            try:
                from PIL import Image
                with Image.open(io.BytesIO(img)) as im:
                    width, height = im.size
            except Exception:
                pass
            await set_rls_context(session, tenant_id=tenant_id)
            session.add(Page(
                tenant_id=tenant_id, document_id=document_id, visibility=doc.visibility,
                page_no=i, storage_key=key, content_type=ct,
                byte_size=len(img), width=width, height=height,
            ))
            await session.execute(
                update(Job).where(Job.id == job_id).values(progress={"done": i, "total": total})
            )
            await session.commit()
            await pub({"kind": "page_ok", "done": i, "total": total})

        await set_rls_context(session, tenant_id=tenant_id)
        await session.execute(
            update(Document).where(Document.id == document_id).values(page_count=total)
        )
        await session.execute(
            update(Job).where(Job.id == job_id).values(
                status="completed", finished_at=datetime.now(timezone.utc), result={"pages": total}
            )
        )
        await session.commit()
        await pub({"kind": "all_done", "pages": total})


async def rerasterize_document(ctx, *, job_id, tenant_id, document_id):
    """Re-render an already-uploaded PDF from its retained original at the tenant's CURRENT raster
    settings (higher DPI / WebP / auto-split spreads), and reset the document's derived data so it can
    be re-transcribed/re-extracted at the new quality. Wipes this document's records (→ mentions cascade,
    citations keep but unlink), transcriptions and pages, then re-creates the pages. Destructive WITHIN
    this one document only; the original PDF is untouched so it's repeatable."""
    from ..models.record import Record
    from ..models.transcription import Transcription
    from ..models.index_entry import IndexEntry

    job_id = uuid.UUID(str(job_id)); tenant_id = uuid.UUID(str(tenant_id)); document_id = uuid.UUID(str(document_id))

    async def pub(event: dict) -> None:
        await events.publish(tenant_id, job_id, event)

    async with SessionLocal() as session:
        await set_rls_context(session, tenant_id=tenant_id)
        await session.execute(update(Job).where(Job.id == job_id).values(
            status="running", started_at=datetime.now(timezone.utc)))
        await session.commit()

        await set_rls_context(session, tenant_id=tenant_id)
        doc = await session.get(Document, document_id)
        if not doc:
            await session.execute(update(Job).where(Job.id == job_id).values(status="error", error="document gone"))
            await session.commit(); return
        dpi, fmt, autosplit = await _raster_settings(session, tenant_id)
        try:
            data, _ = await storage.get_object(doc.storage_bucket, f"{doc.storage_prefix}original.pdf")
            pages = await asyncio.to_thread(_rasterize_pdf, data, dpi=dpi, fmt=fmt, autosplit=autosplit)
        except Exception as exc:
            await set_rls_context(session, tenant_id=tenant_id)
            await session.execute(update(Job).where(Job.id == job_id).values(
                status="error", error=str(exc)[:1000], finished_at=datetime.now(timezone.utc)))
            await session.commit(); await pub({"kind": "book_fail", "error": str(exc)[:300]}); return

        # wipe derived data (records cascade their mentions; citations unlink via SET NULL) + old images
        await set_rls_context(session, tenant_id=tenant_id)
        await session.execute(delete(Record).where(Record.document_id == document_id))
        await session.execute(delete(IndexEntry).where(IndexEntry.document_id == document_id))
        await session.execute(delete(Transcription).where(Transcription.document_id == document_id))
        await session.execute(delete(Page).where(Page.document_id == document_id))
        await session.commit()
        try:
            await storage.delete_prefix(doc.storage_bucket, f"{doc.storage_prefix}pages/")
        except Exception:
            pass

        total = len(pages)
        await pub({"kind": "book_start", "total": total, "engine": "pymupdf", "dpi": dpi})
        for i, (img, ct) in enumerate(pages, start=1):
            ext = "webp" if ct == "image/webp" else "jpg"
            key = f"{doc.storage_prefix}pages/{i}.{ext}"
            await storage.put_object(doc.storage_bucket, key, img, ct)
            width = height = None
            try:
                from PIL import Image
                with Image.open(io.BytesIO(img)) as im:
                    width, height = im.size
            except Exception:
                pass
            await set_rls_context(session, tenant_id=tenant_id)
            session.add(Page(
                tenant_id=tenant_id, document_id=document_id, visibility=doc.visibility,
                page_no=i, storage_key=key, content_type=ct, byte_size=len(img), width=width, height=height,
            ))
            await session.execute(update(Job).where(Job.id == job_id).values(progress={"done": i, "total": total}))
            await session.commit()
            await pub({"kind": "page_ok", "done": i, "total": total})

        await set_rls_context(session, tenant_id=tenant_id)
        await session.execute(update(Document).where(Document.id == document_id).values(page_count=total))
        await session.execute(update(Job).where(Job.id == job_id).values(
            status="completed", finished_at=datetime.now(timezone.utc),
            result={"pages": total, "dpi": dpi, "format": fmt, "autosplit": autosplit}))
        await session.commit()
        await pub({"kind": "all_done", "pages": total})


async def compact_to_pdf(ctx, *, job_id, tenant_id, document_id):
    """Combine an image_set document's pages into a single PDF, as a NEW derived `pdf` document.
    Preserves provenance end-to-end: the new doc inherits ``source_ref`` (origin URL) and points to its
    parent via ``derived_from_id``; each copied page keeps its ``source_ref`` (the FamilySearch ARK).
    Pages are copied verbatim (no re-rasterize) so quality + per-image traceability are kept."""
    from PIL import Image

    job_id = uuid.UUID(str(job_id))
    tenant_id = uuid.UUID(str(tenant_id))
    document_id = uuid.UUID(str(document_id))

    async def pub(event: dict) -> None:
        await events.publish(tenant_id, job_id, event)

    async with SessionLocal() as session:
        await set_rls_context(session, tenant_id=tenant_id)
        await session.execute(update(Job).where(Job.id == job_id).values(
            status="running", started_at=datetime.now(timezone.utc)))
        await session.commit()

        await set_rls_context(session, tenant_id=tenant_id)
        src = await session.get(Document, document_id)
        if not src:
            await session.execute(update(Job).where(Job.id == job_id).values(status="error", error="document gone"))
            await session.commit()
            return
        pages = list((await session.scalars(
            select(Page).where(Page.document_id == document_id).order_by(Page.page_no))).all())
        metas = [(p.page_no, p.storage_key, p.source_ref, p.content_type) for p in pages]
        bucket = src.storage_bucket
        parent = {
            "title": src.title, "visibility": src.visibility, "source_ref": src.source_ref,
            "image_policy": src.image_policy, "may_contain_living": src.may_contain_living,
            "source_origin": src.source_origin, "default_record_type": src.default_record_type,
            "place_id": src.place_id, "year_from": src.year_from, "year_to": src.year_to,
            "created_by": src.created_by,
        }
        await session.commit()

        try:
            raw: list[tuple[int, bytes, str | None, str | None]] = []
            for pno, key, sref, ct in metas:
                data, _ = await storage.get_object(bucket, key)
                raw.append((pno, data, sref, ct))
            imgs = [Image.open(io.BytesIO(d)).convert("RGB") for _, d, _, _ in raw]
            pdf_buf = io.BytesIO()
            if imgs:
                imgs[0].save(pdf_buf, format="PDF", save_all=True, append_images=imgs[1:])
        except Exception as exc:
            await set_rls_context(session, tenant_id=tenant_id)
            await session.execute(update(Job).where(Job.id == job_id).values(
                status="error", error=str(exc)[:1000], finished_at=datetime.now(timezone.utc)))
            await session.commit()
            await pub({"kind": "book_fail", "error": str(exc)[:300]})
            return

        new_id = uuid.uuid4()
        new_prefix = f"{tenant_id}/{new_id}/"
        await storage.put_object(bucket, f"{new_prefix}original.pdf", pdf_buf.getvalue(), "application/pdf")
        await set_rls_context(session, tenant_id=tenant_id)
        session.add(Document(
            id=new_id, tenant_id=tenant_id, title=(parent["title"] + " (PDF)")[:512], doc_type="pdf",
            visibility=parent["visibility"], source_kind="familysearch_pdf",
            storage_bucket=bucket, storage_prefix=new_prefix, page_count=len(raw),
            source_ref=parent["source_ref"], derived_from_id=document_id,
            image_policy=parent["image_policy"], may_contain_living=parent["may_contain_living"],
            source_origin=parent["source_origin"], default_record_type=parent["default_record_type"],
            place_id=parent["place_id"], year_from=parent["year_from"], year_to=parent["year_to"],
            created_by=parent["created_by"],
        ))
        await session.commit()

        total = len(raw)
        await pub({"kind": "book_start", "total": total})
        for i, (pno, data, sref, ct) in enumerate(raw, start=1):
            key = f"{new_prefix}pages/{pno}.jpg"
            await storage.put_object(bucket, key, data, ct or "image/jpeg")
            await set_rls_context(session, tenant_id=tenant_id)
            session.add(Page(
                tenant_id=tenant_id, document_id=new_id, visibility=parent["visibility"],
                page_no=pno, storage_key=key, content_type=ct or "image/jpeg",
                byte_size=len(data), source_ref=sref,  # keep the exact image ARK
            ))
            await session.execute(update(Job).where(Job.id == job_id).values(progress={"done": i, "total": total}))
            await session.commit()
            await pub({"kind": "page_ok", "done": i, "total": total})

        await set_rls_context(session, tenant_id=tenant_id)
        await session.execute(update(Job).where(Job.id == job_id).values(
            status="completed", finished_at=datetime.now(timezone.utc),
            result={"document_id": str(new_id), "pages": total}))
        await session.commit()
        await pub({"kind": "all_done", "pages": total, "document_id": str(new_id)})
