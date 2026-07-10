from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.deps import _client_ip, get_current_principal, get_tenant_db, require_roles
from ...core.security import Principal
from ...models.membership import MembershipRole
from . import service
from .schemas import DocumentOut, PageOut, PublishRequest

router = APIRouter(prefix="/documents", tags=["documents"])

_WRITE = (MembershipRole.tenant_admin.value, MembershipRole.researcher.value)

# Upload caps — reject before the bytes ever land in memory/parsers, so a single huge or
# highly-compressed file can't exhaust RAM/CPU (decompression-bomb DoS).
_MAX_PDF_BYTES = 300 * 1024 * 1024      # a long parish book of scanned pages
_MAX_IMAGE_BYTES = 60 * 1024 * 1024     # one page image
_MAX_TOTAL_BYTES = 600 * 1024 * 1024    # whole multi-file upload
_MAX_FILES = 1000


async def _read_capped(files: list[UploadFile]) -> list[tuple[str | None, str | None, bytes]]:
    if len(files) > _MAX_FILES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"too many files (máx. {_MAX_FILES})")
    out: list[tuple[str | None, str | None, bytes]] = []
    total = 0
    for f in files:
        is_pdf = (f.content_type == "application/pdf") or (f.filename or "").lower().endswith(".pdf")
        cap = _MAX_PDF_BYTES if is_pdf else _MAX_IMAGE_BYTES
        # Starlette populates .size from Content-Length; reject up front when present.
        if f.size is not None and f.size > cap:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "archivo demasiado grande")
        data = await f.read()
        if len(data) > cap:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "archivo demasiado grande")
        total += len(data)
        if total > _MAX_TOTAL_BYTES:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "subida total demasiado grande")
        out.append((f.filename, f.content_type, data))
    return out


def _out(doc) -> DocumentOut:
    return DocumentOut(
        id=doc.id, title=doc.title, doc_type=doc.doc_type, visibility=doc.visibility,
        source_kind=doc.source_kind, page_count=doc.page_count,
        rights_declaration=doc.rights_declaration, image_policy=doc.image_policy,
        may_contain_living=doc.may_contain_living, source_origin=doc.source_origin,
        source_ref=doc.source_ref, derived_from_id=doc.derived_from_id,
        default_record_type=doc.default_record_type,
        pending_job_id=getattr(doc, "pending_job_id", None),
        year_from=doc.year_from, year_to=doc.year_to,
        book_number=doc.book_number, is_index=doc.is_index, indexes_for_id=doc.indexes_for_id,
        created_at=doc.created_at,
    )


@router.post(
    "", response_model=DocumentOut, status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(*_WRITE))],
)
async def upload_document(
    request: Request,
    title: str = Form(...),
    visibility: str = Form("private"),
    rights_declaration: str | None = Form(None),
    image_policy: str = Form("retain"),
    may_contain_living: bool = Form(False),
    source_origin: str | None = Form(None),
    record_type: str | None = Form(None),
    municipality: str | None = Form(None),
    municipality_lat: float | None = Form(None),
    municipality_lng: float | None = Form(None),
    year_from: int | None = Form(None),
    year_to: int | None = Form(None),
    book_number: int | None = Form(None),
    is_index: bool = Form(False),
    files: list[UploadFile] = File(...),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> DocumentOut:
    payloads = await _read_capped(files)
    doc = await service.create_document(
        db, principal.tenant_id, principal.user_id,
        title=title, visibility=visibility, rights_declaration=rights_declaration,
        ip=_client_ip(request), files=payloads,
        image_policy=image_policy, may_contain_living=may_contain_living, source_origin=source_origin,
        record_type=record_type, municipality=municipality,
        municipality_lat=municipality_lat, municipality_lng=municipality_lng,
        year_from=year_from, year_to=year_to, book_number=book_number, is_index=is_index,
    )
    return _out(doc)


@router.post(
    "/{doc_id}/discard-images", response_model=DocumentOut,
    dependencies=[Depends(require_roles(*_WRITE))],
)
async def discard_images(
    doc_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> DocumentOut:
    """Mode B: discard the stored page images, keeping only the extracted facts + citations."""
    return _out(await service.discard_images(db, principal.tenant_id, doc_id))


@router.get("", response_model=list[DocumentOut])
async def list_documents(
    scope: str = Query("mine", pattern="^(mine|public|all)$"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> list[DocumentOut]:
    return [_out(d) for d in await service.list_documents(db, principal.tenant_id, scope, limit, offset)]


class RasterSettings(BaseModel):
    dpi: int
    format: str       # webp | jpeg
    autosplit: bool


@router.get("/raster-settings", response_model=RasterSettings)
async def get_raster_settings(
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> RasterSettings:
    return RasterSettings(**await service.get_raster_settings(db, principal.tenant_id))


@router.put("/raster-settings", response_model=RasterSettings, dependencies=[Depends(require_roles(*_WRITE))])
async def set_raster_settings(
    body: RasterSettings,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> RasterSettings:
    out = await service.set_raster_settings(
        db, principal.tenant_id, dpi=body.dpi, fmt=body.format, autosplit=body.autosplit)
    return RasterSettings(**out)


@router.post("/{doc_id}/rerasterize", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_roles(*_WRITE))])
async def rerasterize(
    doc_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> dict:
    """Re-render this PDF at the current quality settings + reset its derived data for re-processing."""
    job = await service.rerasterize_job(db, principal.tenant_id, principal.user_id, doc_id)
    return {"id": str(job.id), "status": job.status}


@router.get("/series-gaps")
async def series_gaps(
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> list[dict]:
    """Per parish-series (place + record type), the present book numbers and any missing ones."""
    return await service.series_gaps(db, principal.tenant_id)


@router.get("/{doc_id}", response_model=DocumentOut)
async def get_document(doc_id: uuid.UUID, db: AsyncSession = Depends(get_tenant_db)) -> DocumentOut:
    return _out(await service.get_document(db, doc_id))


@router.post("/{doc_id}/compact-pdf", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_roles(*_WRITE))])
async def compact_pdf(
    doc_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_tenant_db),
) -> dict:
    """Combine an image document's pages into a single derived PDF document (preserving provenance)."""
    job = await service.compact_pdf_job(db, principal.tenant_id, principal.user_id, doc_id)
    return {"id": str(job.id), "status": job.status}


@router.get("/{doc_id}/pages", response_model=list[PageOut])
async def list_pages(doc_id: uuid.UUID, db: AsyncSession = Depends(get_tenant_db)) -> list[PageOut]:
    return [
        PageOut(
            id=p.id, page_no=p.page_no, folio_label=p.folio_label, kind=p.kind,
            content_type=p.content_type,
            width=p.width, height=p.height, byte_size=p.byte_size, source_ref=p.source_ref,
        )
        for p in await service.list_pages(db, doc_id)
    ]


@router.get("/{doc_id}/pages/{page_no}/content")
async def page_content(
    doc_id: uuid.UUID, page_no: int, thumb: bool = Query(False),
    db: AsyncSession = Depends(get_tenant_db),
) -> Response:
    data, content_type = await service.get_page_content(db, doc_id, page_no, thumb=thumb)
    return Response(content=data, media_type=content_type)


class PageKindRequest(BaseModel):
    kind: str  # record | index | cover | blank


@router.patch("/{doc_id}/pages/{page_no}/kind", response_model=PageOut,
              dependencies=[Depends(require_roles(*_WRITE))])
async def set_page_kind(
    doc_id: uuid.UUID, page_no: int, body: PageKindRequest,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> PageOut:
    p = await service.set_page_kind(db, principal.tenant_id, doc_id, page_no, body.kind)
    return PageOut(id=p.id, page_no=p.page_no, folio_label=p.folio_label, kind=p.kind,
                   content_type=p.content_type, width=p.width, height=p.height,
                   byte_size=p.byte_size, source_ref=p.source_ref)


class SplitRequest(BaseModel):
    breaks: list[int]            # page_no values that START a new book
    books: list[dict] | None = None  # per new segment: {book_number?, title?, is_index?}


@router.post("/{doc_id}/split", response_model=list[DocumentOut],
             dependencies=[Depends(require_roles(*_WRITE))])
async def split_document(
    doc_id: uuid.UUID, body: SplitRequest,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> list[DocumentOut]:
    """Split a PDF that holds several books into one document per book."""
    docs = await service.split_document(
        db, principal.tenant_id, principal.user_id, doc_id,
        breaks=body.breaks, books=body.books)
    return [_out(d) for d in docs]


@router.post(
    "/{doc_id}/publish", response_model=DocumentOut, dependencies=[Depends(require_roles(*_WRITE))]
)
async def publish(
    doc_id: uuid.UUID,
    body: PublishRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> DocumentOut:
    doc = await service.set_visibility(
        db, principal.tenant_id, doc_id, "public",
        rights_declaration=body.rights_declaration, ip=_client_ip(request),
        user_id=principal.user_id,
    )
    return _out(doc)


@router.post(
    "/{doc_id}/unpublish", response_model=DocumentOut, dependencies=[Depends(require_roles(*_WRITE))]
)
async def unpublish(
    doc_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> DocumentOut:
    return _out(await service.set_visibility(db, principal.tenant_id, doc_id, "private"))


@router.post("/{doc_id}/parse-index", status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(require_roles(*_WRITE))])
async def parse_index(
    doc_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> dict:
    """Parse the document's index pages (name→folio) and cross-check against extracted records."""
    job = await service.parse_index_job(db, principal.tenant_id, principal.user_id, doc_id)
    return {"id": str(job.id), "status": job.status}


class IndexEntryOut(BaseModel):
    id: uuid.UUID
    name_raw: str | None
    folio_label: str | None
    record_no: str | None
    year: int | None
    matched: bool | None


@router.get("/{doc_id}/index")
async def document_index(doc_id: uuid.UUID, db: AsyncSession = Depends(get_tenant_db)) -> dict:
    """The parsed index entries + a matched/missing summary (which acts the index lists but extraction
    didn't find)."""
    entries = await service.list_index_entries(db, doc_id)
    out = [IndexEntryOut(id=e.id, name_raw=e.name_raw, folio_label=e.folio_label,
                         record_no=e.record_no, year=e.year, matched=e.matched).model_dump(mode="json")
           for e in entries]
    matched = sum(1 for e in entries if e.matched)
    return {"entries": out, "total": len(entries), "matched": matched, "missing": len(entries) - matched}


@router.delete("/{doc_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_roles(*_WRITE))])
async def delete_document(
    doc_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> None:
    await service.delete_document(db, principal.tenant_id, doc_id)
