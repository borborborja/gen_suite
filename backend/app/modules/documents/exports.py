"""Private export API. Downloads are authenticated, streaming and range-capable."""
from __future__ import annotations

import asyncio
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select

from ...core import storage
from ...core.deps import get_current_principal, get_tenant_db, require_roles
from ...core.dispatch import pdfa_available, schedule
from ...models.document import Document
from ...models.document_export import DocumentExport
from ...models.job import Job
from ...models.membership import MembershipRole
from ...models.transcription import Transcription
from ..jobs.service import active_job_for

router = APIRouter()
_WRITE = (MembershipRole.tenant_admin.value, MembershipRole.researcher.value)


class ExportRequest(BaseModel):
    kind: str = "pdf"


async def _owned(db, tenant_id, document_id, *, lock=False):
    stmt = select(Document).where(Document.id == document_id, Document.tenant_id == tenant_id)
    doc = await db.scalar(stmt.with_for_update() if lock else stmt)
    if doc is None:
        raise HTTPException(404, "Documento no encontrado")
    return doc


@router.post("/{document_id}/exports", status_code=202, dependencies=[Depends(require_roles(*_WRITE))])
async def create_export(document_id: uuid.UUID, body: ExportRequest,
                        principal=Depends(get_current_principal), db=Depends(get_tenant_db)):
    await _owned(db, principal.tenant_id, document_id, lock=True)
    formats = {"pdf": ("compact_pdf", "compact_to_pdf"),
               "pdfa": ("document_pdfa", "export_document_pdfa"),
               "bagit": ("document_archive", "export_document_archive")}
    if body.kind not in formats:
        raise HTTPException(422, "Formato no admitido: utiliza pdf, pdfa o bagit")
    job_type, function = formats[body.kind]
    job = await active_job_for(db, principal.tenant_id, job_type, document_id)
    if job is None:
        if body.kind == "pdfa" and not await pdfa_available():
            raise HTTPException(503, "El worker PDF/A no está disponible. Activa o revisa el perfil pdfa de Docker Compose.")
        job = Job(tenant_id=principal.tenant_id, type=job_type, created_by=principal.user_id,
                  status="queued", params={"document_id": str(document_id), "kind": body.kind})
        db.add(job)
        await schedule(db, job, function, document_id=str(document_id))
    return {"id": str(job.id), "status": job.status}


@router.get("/{document_id}/export-capabilities")
async def export_capabilities(document_id: uuid.UUID, principal=Depends(get_current_principal), db=Depends(get_tenant_db)):
    await _owned(db, principal.tenant_id, document_id)
    return {"pdfa_available": await pdfa_available(), "pdfa_profile": "PDF/A-2b", "text_mode": "appendix"}


@router.get("/{document_id}/exports/{export_id}/validation")
async def export_validation(document_id: uuid.UUID, export_id: uuid.UUID,
                            principal=Depends(get_current_principal), db=Depends(get_tenant_db)):
    await _owned(db, principal.tenant_id, document_id)
    row = await db.scalar(select(DocumentExport).where(DocumentExport.id == export_id,
        DocumentExport.document_id == document_id, DocumentExport.tenant_id == principal.tenant_id))
    proof = (row.manifest or {}).get("validation") if row else None
    if not proof:
        raise HTTPException(404, "Esta exportación no tiene informe PDF/A")
    if row.kind != "pdfa" or proof.get("output_sha256") != row.checksum or proof.get("status") != "validated":
        raise HTTPException(409, "El informe no corresponde al checksum de la exportación")
    return JSONResponse({"export_id": str(row.id), "document_id": str(document_id),
        "checksum": row.checksum, "transcriptions": row.manifest.get("transcriptions"), "validation": proof},
        headers={"Cache-Control": "private, no-store", "Content-Disposition": f'attachment; filename="{row.id}-validation.json"'})


@router.get("/{document_id}/exports")
async def list_exports(document_id: uuid.UUID, principal=Depends(get_current_principal), db=Depends(get_tenant_db)):
    await _owned(db, principal.tenant_id, document_id)
    snapshot = {str(t.page_no): str(t.id) for t in (await db.scalars(select(Transcription).where(
        Transcription.document_id == document_id, Transcription.is_active.is_(True)))).all()}
    rows = (await db.scalars(select(DocumentExport).where(DocumentExport.document_id == document_id)
                            .order_by(DocumentExport.created_at.desc()).limit(100))).all()
    return [{"id": str(row.id), "kind": row.kind, "byte_size": row.byte_size,
             "checksum": row.checksum, "created_at": row.created_at,
             "stale": row.manifest.get("transcriptions") != snapshot,
             "pdfa": row.manifest.get("pdfa", "not_validated"),
             "text_mode": row.manifest.get("text_mode"), "scope": "document"} for row in rows]


def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    if header is None:
        return None
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", header)
    if not match or not any(match.groups()) or size <= 0:
        raise HTTPException(416, "Rango no válido", headers={"Content-Range": f"bytes */{size}"})
    start_raw, end_raw = match.groups()
    if start_raw:
        start, end = int(start_raw), min(int(end_raw), size - 1) if end_raw else size - 1
    else:
        start, end = max(0, size - int(end_raw)), size - 1
    if start >= size or start > end:
        raise HTTPException(416, "Rango no disponible", headers={"Content-Range": f"bytes */{size}"})
    return start, end


@router.get("/{document_id}/exports/{export_id}/content")
async def download_export(document_id: uuid.UUID, export_id: uuid.UUID, request: Request,
                          principal=Depends(get_current_principal), db=Depends(get_tenant_db)):
    doc = await _owned(db, principal.tenant_id, document_id)
    row = await db.scalar(select(DocumentExport).where(DocumentExport.id == export_id,
                                                       DocumentExport.document_id == document_id))
    if row is None:
        raise HTTPException(404, "Exportación no encontrada")
    selected = parse_range(request.headers.get("range"), row.byte_size)
    headers = {"Accept-Ranges": "bytes", "ETag": f'"{row.checksum}"', "Cache-Control": "private, no-store",
               "Content-Disposition": f'attachment; filename="{row.id}.{"zip" if row.kind == "bagit" else "pdf"}"'}
    if selected:
        start, end = selected
        headers["Content-Range"] = f"bytes {start}-{end}/{row.byte_size}"
        headers["Content-Length"] = str(end - start + 1)
    else:
        headers["Content-Length"] = str(row.byte_size)
    obj = await storage.open_object(doc.storage_bucket, row.storage_key,
                                   byte_range=f"bytes={selected[0]}-{selected[1]}" if selected else None)
    async def chunks():
        try:
            while chunk := await asyncio.to_thread(obj["Body"].read, 1024 * 1024):
                yield chunk
        finally:
            await asyncio.to_thread(obj["Body"].close)
    return StreamingResponse(chunks(), status_code=206 if selected else 200, headers=headers,
                             media_type="application/zip" if row.kind == "bagit" else "application/pdf")
