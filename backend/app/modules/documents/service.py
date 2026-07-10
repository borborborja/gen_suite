"""Document upload, content streaming, rights/visibility, deletion.

Objects are written to MinIO before the DB rows so a failed insert just orphans blobs (a
sweeper can reclaim them later). Images become one page each; a PDF is stored whole with its
internal page count recorded (per-page rasterization arrives in Phase 4 with pymupdf).
"""
from __future__ import annotations

import hashlib
import io
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from PIL import Image
from pypdf import PdfReader

# Cap pixels Pillow will decode: a ~64 MP image is far above any scanned page, but a malicious
# tiny file can claim billions of pixels and OOM the decoder. Pillow raises DecompressionBombError
# past this. (A4 @ 600 dpi ≈ 35 MP, so 64 MP leaves comfortable headroom.)
Image.MAX_IMAGE_PIXELS = 64_000_000
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...core import storage
from ...core.queue import get_queue
from ...models.document import Document, Page
from ...models.job import Job
from ...models.place import Place
from ..tree.mapping import normalize_place


async def _resolve_municipality(session, tenant_id, name: str | None, lat=None, lng=None):
    """Resolve a municipality string to a Place id (deduped per tenant), creating it if new and
    storing geocoded coordinates (for a future map)."""
    if not name or not name.strip():
        return None
    key = normalize_place(name)[:512]
    place = await session.scalar(
        select(Place).where(Place.tenant_id == tenant_id, Place.normalized_key == key)
    )
    if place:
        if lat is not None and place.lat is None:
            place.lat, place.lng = lat, lng
        return place.id
    place = Place(tenant_id=tenant_id, name=name.strip()[:512], normalized_key=key, lat=lat, lng=lng)
    session.add(place)
    await session.flush()
    return place.id

RIGHTS = {"owner", "public_domain", "licensed", "permission_granted"}
_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/tiff": ".tif", "image/webp": ".webp"}


def _ext_for(content_type: str | None, filename: str | None) -> str:
    if filename and "." in filename:
        return "." + filename.rsplit(".", 1)[1].lower()
    return _EXT.get(content_type or "", ".bin")


_RASTER_DPI = 300            # archival default (was 150 — too low to read 18-19th-c. handwriting)
_RASTER_MAX_W = 3000         # generous per-FACE cap so 300-400 DPI detail survives (was 1800)
_SPREAD_RATIO = 1.15         # width/height above this ⇒ a two-page spread to split


def _rasterize_pdf(
    data: bytes, *, dpi: int = _RASTER_DPI, fmt: str = "webp",
    autosplit: bool = True, max_w: int = _RASTER_MAX_W,
) -> list[tuple[bytes, str]]:
    """Render each PDF page so the per-page HTR/vision pipeline can READ it. The model reads exactly
    these pixels, so resolution is the dominant quality lever: too low and dense handwriting becomes
    illegible and the model hallucinates. Returns (bytes, content_type) per output page.

    - ``dpi``: render resolution (300 ≈ archival; the source scans are usually higher-res inside the PDF).
    - ``autosplit``: a landscape page is almost always a two-page *spread* (a book opening); split it
      into left/right faces so each act is read at full resolution instead of half.
    - ``fmt``: 'webp' (≈30% smaller than JPEG at equal quality — all major vision models read WebP) or 'jpeg'.
    - ``max_w``: cap per face so payloads stay sane without throwing away legibility.
    """
    import fitz  # pymupdf

    ct = "image/webp" if fmt == "webp" else "image/jpeg"
    out: list[tuple[bytes, str]] = []
    with fitz.open(stream=data, filetype="pdf") as pdf:
        for page in pdf:
            pix = page.get_pixmap(dpi=dpi)
            mode = "RGBA" if pix.alpha else "RGB"
            im = Image.frombytes(mode, (pix.width, pix.height), pix.samples).convert("RGB")
            if autosplit and im.width > im.height * _SPREAD_RATIO:
                mid = im.width // 2
                faces = [im.crop((0, 0, mid, im.height)), im.crop((mid, 0, im.width, im.height))]
            else:
                faces = [im]
            for face in faces:
                if face.width > max_w:
                    face = face.resize((max_w, round(face.height * max_w / face.width)))
                buf = io.BytesIO()
                if fmt == "webp":
                    face.save(buf, "WEBP", quality=80, method=4)
                else:
                    face.save(buf, "JPEG", quality=85)
                out.append((buf.getvalue(), ct))
    return out


async def create_document(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    created_by: uuid.UUID,
    *,
    title: str,
    visibility: str,
    rights_declaration: str | None,
    ip: str | None,
    files: list[tuple[str | None, str | None, bytes]],
    image_policy: str = "retain",
    may_contain_living: bool = False,
    source_origin: str | None = None,
    record_type: str | None = None,
    municipality: str | None = None,
    municipality_lat: float | None = None,
    municipality_lng: float | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    book_number: int | None = None,
    is_index: bool = False,
) -> Document:
    if visibility not in ("private", "public"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid visibility")
    if image_policy not in ("retain", "data_only"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid image_policy")
    if not files:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no files uploaded")
    if visibility == "public" and not rights_declaration:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "publishing requires a rights declaration")
    if rights_declaration and rights_declaration not in RIGHTS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid rights declaration")

    doc_id = uuid.uuid4()
    bucket = storage.bucket_for(visibility)
    prefix = f"{tenant_id}/{doc_id}/"
    digest = hashlib.sha256()

    is_pdf = len(files) == 1 and (
        (files[0][1] == "application/pdf") or (files[0][0] or "").lower().endswith(".pdf")
    )
    pages_meta: list[tuple[int, str, str, int, int | None, int | None]] = []

    if is_pdf:
        _, _, data = files[0]
        digest.update(data)
        # Store the original PDF; rasterization to per-page JPEGs runs in a background job
        # (a book of hundreds of pages would time out the HTTP upload). Pages are created by the job.
        await storage.put_object(bucket, f"{prefix}original.pdf", data, "application/pdf")
        try:
            page_count = len(PdfReader(io.BytesIO(data)).pages)
        except Exception:
            page_count = 0
        doc_type = "pdf"  # pages_meta stays empty → filled by rasterize_document
    else:
        for i, (fname, ctype, data) in enumerate(files, start=1):
            digest.update(data)
            ct = ctype or "application/octet-stream"
            key = f"{prefix}pages/{i}{_ext_for(ct, fname)}"
            await storage.put_object(bucket, key, data, ct)
            width = height = None
            try:
                with Image.open(io.BytesIO(data)) as im:
                    width, height = im.size
            except Exception:
                pass
            pages_meta.append((i, key, ct, len(data), width, height))
        doc_type = "image_set"
        page_count = len(files)

    place_id = await _resolve_municipality(session, tenant_id, municipality, municipality_lat, municipality_lng)
    doc = Document(
        id=doc_id,
        tenant_id=tenant_id,
        title=title,
        doc_type=doc_type,
        visibility=visibility,
        source_kind="upload",
        storage_bucket=bucket,
        storage_prefix=prefix,
        page_count=page_count,
        fingerprint=digest.hexdigest(),
        created_by=created_by,
        image_policy=image_policy,
        may_contain_living=may_contain_living,
        source_origin=source_origin,
        default_record_type=record_type or None,
        place_id=place_id,
        year_from=year_from,
        year_to=year_to,
        book_number=book_number,
        is_index=is_index,
    )
    if rights_declaration:
        doc.rights_declaration = rights_declaration
        doc.rights_declared_by = created_by
        doc.rights_declared_at = datetime.now(timezone.utc)
        doc.rights_declared_ip = ip
    session.add(doc)
    await session.flush()

    for page_no, key, ct, size, width, height in pages_meta:
        session.add(
            Page(
                tenant_id=tenant_id,
                document_id=doc.id,
                visibility=visibility,
                page_no=page_no,
                storage_key=key,
                content_type=ct,
                byte_size=size,
                width=width,
                height=height,
            )
        )
    await session.flush()

    # PDFs: enqueue background rasterization (pages get created by the worker). pending_job_id is a
    # transient attribute the router surfaces so the UI can stream progress.
    doc.pending_job_id = None
    if is_pdf:
        job = Job(
            tenant_id=tenant_id, type="rasterize", status="queued",
            params={"document_id": str(doc.id)}, created_by=created_by,
        )
        session.add(job)
        await session.commit()  # durable before enqueue so the worker can't race ahead of the row
        queue = await get_queue()
        await queue.enqueue_job(
            "rasterize_document", job_id=str(job.id), tenant_id=str(tenant_id), document_id=str(doc.id)
        )
        doc.pending_job_id = job.id  # transient attr surfaced to the router (not a persisted column)
    return doc


async def compact_pdf_job(
    session: AsyncSession, tenant_id: uuid.UUID, created_by: uuid.UUID, document_id: uuid.UUID
) -> Job:
    """Enqueue building a single PDF (a new derived document) from an image_set's pages."""
    doc = await session.get(Document, document_id)
    if not doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    if doc.doc_type != "image_set":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "only image documents can be compacted to PDF")
    from ..jobs.service import active_job_for
    existing = await active_job_for(session, tenant_id, "compact_pdf", document_id)
    if existing:
        return existing
    job = Job(tenant_id=tenant_id, type="compact_pdf", status="queued",
              params={"document_id": str(document_id)}, created_by=created_by)
    session.add(job)
    await session.commit()  # durable before enqueue
    queue = await get_queue()
    await queue.enqueue_job(
        "compact_to_pdf", job_id=str(job.id), tenant_id=str(tenant_id), document_id=str(document_id))
    return job


async def series_gaps(session: AsyncSession, tenant_id: uuid.UUID) -> list[dict]:
    """Group books by series (parish place + record type) and report missing book numbers, so a gap
    in the collection (e.g. baptisms 11,12,14 → missing 13) is surfaced as a possible data hole."""
    from collections import defaultdict

    rows = (await session.execute(
        select(Document.place_id, Document.default_record_type, Document.book_number,
               Document.id, Document.title)
        .where(Document.tenant_id == tenant_id, Document.book_number.is_not(None),
               Document.is_index.is_(False))
        .order_by(Document.place_id, Document.default_record_type, Document.book_number)
    )).all()
    groups: dict[tuple, list] = defaultdict(list)
    for place_id, rtype, bnum, did, title in rows:
        groups[(place_id, rtype)].append({"book_number": bnum, "id": str(did), "title": title})

    place_names: dict = {}
    pids = {pid for (pid, _) in groups if pid}
    if pids:
        for pid, name in (await session.execute(
            select(Place.id, Place.name).where(Place.id.in_(pids))
        )).all():
            place_names[pid] = name

    out: list[dict] = []
    for (place_id, rtype), books in groups.items():
        nums = sorted(b["book_number"] for b in books)
        present = set(nums)
        missing = [n for n in range(nums[0], nums[-1] + 1) if n not in present]
        out.append({
            "place_id": str(place_id) if place_id else None,
            "place_name": place_names.get(place_id),
            "record_type": rtype,
            "present": nums,
            "missing": missing,
            "books": books,
        })
    return out


async def get_raster_settings(session: AsyncSession, tenant_id: uuid.UUID) -> dict:
    from ...models.tenant import Tenant
    t = await session.get(Tenant, tenant_id)
    return {"dpi": (t.raster_dpi if t else 300), "format": (t.raster_format if t else "webp"),
            "autosplit": (t.raster_autosplit if t else True)}


async def set_raster_settings(session: AsyncSession, tenant_id: uuid.UUID, *, dpi: int, fmt: str, autosplit: bool) -> dict:
    from ...models.tenant import Tenant
    dpi = max(100, min(600, int(dpi)))            # clamp to a sane range
    fmt = "webp" if fmt not in ("webp", "jpeg") else fmt
    await session.execute(update(Tenant).where(Tenant.id == tenant_id).values(
        raster_dpi=dpi, raster_format=fmt, raster_autosplit=bool(autosplit)))
    await session.commit()
    return {"dpi": dpi, "format": fmt, "autosplit": bool(autosplit)}


async def rerasterize_job(session: AsyncSession, tenant_id: uuid.UUID, created_by: uuid.UUID, document_id: uuid.UUID) -> Job:
    """Re-render an uploaded PDF at the current raster settings + reset its derived data (re-process)."""
    doc = await session.get(Document, document_id)
    if not doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    if doc.doc_type != "pdf":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "solo PDFs subidos pueden re-rasterizarse")
    from ..jobs.service import active_job_for
    existing = await active_job_for(session, tenant_id, "rerasterize", document_id)
    if existing:
        return existing
    job = Job(tenant_id=tenant_id, type="rerasterize", status="queued",
              params={"document_id": str(document_id)}, created_by=created_by)
    session.add(job)
    await session.commit()
    queue = await get_queue()
    await queue.enqueue_job("rerasterize_document", job_id=str(job.id), tenant_id=str(tenant_id),
                            document_id=str(document_id))
    return job


async def parse_index_job(
    session: AsyncSession, tenant_id: uuid.UUID, created_by: uuid.UUID, document_id: uuid.UUID
) -> Job:
    """Enqueue parsing of a document's index pages (name→folio) + cross-check vs records."""
    doc = await session.get(Document, document_id)
    if not doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    from ..providers.service import assert_within_budget
    await assert_within_budget(session, tenant_id)
    has_index = doc.is_index or (await session.scalar(
        select(Page.id).where(Page.document_id == document_id, Page.kind == "index").limit(1)
    )) is not None
    if not has_index:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "marca primero las páginas de índice en el mosaico, o sube el índice como documento de tipo índice")
    from ..jobs.service import active_job_for
    existing = await active_job_for(session, tenant_id, "index", document_id)
    if existing:
        return existing
    job = Job(tenant_id=tenant_id, type="index", status="queued",
              params={"document_id": str(document_id)}, created_by=created_by)
    session.add(job)
    await session.commit()
    queue = await get_queue()
    await queue.enqueue_job("parse_index", job_id=str(job.id), tenant_id=str(tenant_id),
                            document_id=str(document_id))
    return job


async def list_index_entries(session: AsyncSession, document_id: uuid.UUID) -> list:
    from ...models.index_entry import IndexEntry
    return list((await session.scalars(
        select(IndexEntry).where(IndexEntry.document_id == document_id).order_by(IndexEntry.norm_surname)
    )).all())


async def list_documents(
    session: AsyncSession, tenant_id: uuid.UUID, scope: str = "mine",
    limit: int = 100, offset: int = 0,
) -> list[Document]:
    stmt = select(Document).order_by(Document.created_at.desc())
    if scope == "mine":
        stmt = stmt.where(Document.tenant_id == tenant_id)
    elif scope == "public":
        stmt = stmt.where(Document.visibility == "public")
    # scope == "all" relies on RLS (own tenant + public)
    stmt = stmt.limit(limit).offset(offset)
    return list((await session.scalars(stmt)).all())


async def get_document(session: AsyncSession, doc_id: uuid.UUID) -> Document:
    doc = await session.get(Document, doc_id)
    if not doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    return doc


async def list_pages(session: AsyncSession, doc_id: uuid.UUID) -> list[Page]:
    await get_document(session, doc_id)
    return list(
        (
            await session.scalars(
                select(Page).where(Page.document_id == doc_id).order_by(Page.page_no)
            )
        ).all()
    )


async def get_page_content(
    session: AsyncSession, doc_id: uuid.UUID, page_no: int, *, thumb: bool = False
) -> tuple[bytes, str]:
    doc = await get_document(session, doc_id)
    page = await session.scalar(
        select(Page).where(Page.document_id == doc_id, Page.page_no == page_no)
    )
    if not page:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "page not found")
    if page.image_purged:
        # Mode B: we hold the extracted facts + citation, but the source image was discarded.
        raise HTTPException(
            status.HTTP_410_GONE, "image discarded (data-only document) — facts retained, image not available"
        )
    data, ct = await storage.get_object(doc.storage_bucket, page.storage_key)
    if thumb:
        # Downscale for the page-mosaic so a 300-page book doesn't ship hundreds of full scans.
        try:
            buf = io.BytesIO()
            with Image.open(io.BytesIO(data)) as im:
                im = im.convert("RGB")
                im.thumbnail((260, 360))
                im.save(buf, format="JPEG", quality=70)
            return buf.getvalue(), "image/jpeg"
        except Exception:
            pass  # fall back to the full image if it can't be decoded
    return data, page.content_type or ct


async def set_page_kind(
    session: AsyncSession, tenant_id: uuid.UUID, doc_id: uuid.UUID, page_no: int, kind: str
) -> Page:
    """Tag a page as record/index/cover/blank (index/cover/blank are skipped by record extraction)."""
    if kind not in ("record", "index", "cover", "blank"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid page kind")
    page = await session.scalar(
        select(Page).where(Page.document_id == doc_id, Page.page_no == page_no)
    )
    if not page:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "page not found")
    page.kind = kind
    await session.flush()
    return page


async def split_document(
    session: AsyncSession, tenant_id: uuid.UUID, created_by: uuid.UUID, doc_id: uuid.UUID,
    *, breaks: list[int], books: list[dict] | None = None,
) -> list[Document]:
    """Split one document (PDF holding 2–3 books) into several. ``breaks`` are the page_no values that
    START a new book (e.g. [151] → book A = pp.1–150 stays, book B = pp.151–end becomes a new doc).
    Pages keep their storage blobs; only their document_id + page_no are reassigned, and their
    transcriptions + records follow. ``books`` (aligned to the segments after the first) may carry
    {book_number, title, is_index}."""
    from ...models.transcription import Transcription
    from ...models.record import Record
    doc = await get_document(session, doc_id)
    pages = list((await session.scalars(
        select(Page).where(Page.document_id == doc_id).order_by(Page.page_no)
    )).all())
    if not pages:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "document has no pages")
    cut = sorted({b for b in breaks if 1 < b <= pages[-1].page_no})
    if not cut:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no valid split points")

    # Build segments [start_idx..end_idx) over the ordered page list.
    bounds = [0] + [next(i for i, p in enumerate(pages) if p.page_no == c) for c in cut] + [len(pages)]
    segments = [pages[bounds[i]:bounds[i + 1]] for i in range(len(bounds) - 1)]
    books = books or []
    created: list[Document] = []

    async def _renumber(seg: list[Page], target_doc: Document) -> None:
        for new_no, pg in enumerate(seg, start=1):
            pg.document_id = target_doc.id
            pg.page_no = new_no
            pg.visibility = target_doc.visibility
            # move this page's transcriptions + records to the target doc
            await session.execute(update(Transcription).where(Transcription.page_id == pg.id).values(
                document_id=target_doc.id, page_no=new_no))
            await session.execute(update(Record).where(
                (Record.page_id == pg.id) | (Record.page_end_id == pg.id)).values(document_id=target_doc.id))
        target_doc.page_count = len(seg)

    # First segment stays in the original document (renumbered from 1).
    await _renumber(segments[0], doc)

    # Each later segment becomes a new document inheriting the parent's metadata.
    for idx, seg in enumerate(segments[1:]):
        meta = books[idx] if idx < len(books) else {}
        new = Document(
            tenant_id=tenant_id, title=meta.get("title") or f"{doc.title} ({idx + 2})",
            doc_type=doc.doc_type, visibility=doc.visibility, source_kind=doc.source_kind,
            image_policy=doc.image_policy, may_contain_living=doc.may_contain_living,
            source_origin=doc.source_origin, source_ref=doc.source_ref, derived_from_id=doc.id,
            default_record_type=doc.default_record_type, storage_bucket=doc.storage_bucket,
            storage_prefix=doc.storage_prefix, place_id=doc.place_id,
            year_from=doc.year_from, year_to=doc.year_to, created_by=created_by,
            book_number=meta.get("book_number"), is_index=bool(meta.get("is_index")),
        )
        session.add(new)
        await session.flush()
        await _renumber(seg, new)
        created.append(new)
    await session.flush()
    return [doc, *created]


async def discard_images(
    session: AsyncSession, tenant_id: uuid.UUID, doc_id: uuid.UUID
) -> Document:
    """Mode B: delete the document's stored page images from object storage but KEEP the extracted
    records/mentions and citations (which only reference page_no, not the bytes). For sources where
    the facts may be retained but the image may not be redistributed."""
    doc = await get_document(session, doc_id)
    if doc.tenant_id != tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not your document")
    await storage.delete_prefix(doc.storage_bucket, doc.storage_prefix)
    pages = (await session.scalars(select(Page).where(Page.document_id == doc_id))).all()
    for p in pages:
        p.image_purged = True
    doc.image_policy = "data_only"
    await session.flush()
    return doc


async def set_visibility(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    doc_id: uuid.UUID,
    visibility: str,
    *,
    rights_declaration: str | None = None,
    ip: str | None = None,
    user_id: uuid.UUID | None = None,
) -> Document:
    doc = await get_document(session, doc_id)
    if doc.tenant_id != tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not your document")
    if visibility == "public":
        if doc.source_kind == "familysearch":
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "FamilySearch-sourced documents cannot be published"
            )
        if not rights_declaration or rights_declaration not in RIGHTS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "valid rights declaration required")

    new_bucket = storage.bucket_for(visibility)
    if new_bucket != doc.storage_bucket:
        await storage.move_prefix(doc.storage_bucket, new_bucket, doc.storage_prefix)
        doc.storage_bucket = new_bucket

    doc.visibility = visibility
    await session.execute(
        update(Page).where(Page.document_id == doc_id).values(visibility=visibility)
    )
    if visibility == "public":
        doc.rights_declaration = rights_declaration
        doc.rights_declared_by = user_id
        doc.rights_declared_at = datetime.now(timezone.utc)
        doc.rights_declared_ip = ip
    return doc


async def delete_document(session: AsyncSession, tenant_id: uuid.UUID, doc_id: uuid.UUID) -> None:
    doc = await get_document(session, doc_id)
    if doc.tenant_id != tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not your document")
    await storage.delete_prefix(doc.storage_bucket, doc.storage_prefix)
    await session.delete(doc)
