"""Private staged imports: upload, immutable review, explicit append, never merge."""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, text

from ...core import storage
from ...core.deps import get_current_principal, get_tenant_db, require_roles
from ...core.dispatch import schedule
from ...models.job import Job
from ...models.membership import MembershipRole
from ...models.tree_import import TreeImport
from ...settings import settings
from ..documents.exports import parse_range

router = APIRouter(prefix="/imports")
_WRITE = [Depends(require_roles(MembershipRole.tenant_admin.value, MembershipRole.researcher.value))]


async def get_import(db, tid, iid, *, lock=False):
    query = select(TreeImport).where(TreeImport.tenant_id == tid, TreeImport.id == iid)
    row = await db.scalar(query.with_for_update() if lock else query)
    if not row: raise HTTPException(404, "Importación no encontrada")
    return row


async def describe(db, row):
    prepare = await db.get(Job, row.id)
    apply = await db.get(Job, row.apply_job_id) if row.apply_job_id else None
    return {"id": str(row.id), "client_id": str(row.client_id), "kind": row.kind, "filename": row.filename,
        "byte_size": row.byte_size, "checksum": row.checksum, "created_at": row.created_at,
        "status": prepare.status, "error": prepare.error, "progress": prepare.progress,
        "review_ready": row.report is not None, "apply_job_id": str(row.apply_job_id) if apply else None,
        "apply_status": apply.status if apply else None, "apply_error": apply.error if apply else None,
        "result": apply.result if apply and apply.status == "completed" else None}


@router.post("", status_code=202, dependencies=_WRITE)
async def upload_import(request: Request, client_id: uuid.UUID, kind: Literal["gedcom7", "gedzip"],
        filename: str = Query(min_length=1, max_length=255),
        principal=Depends(get_current_principal), db=Depends(get_tenant_db)):
    if any(ord(c) < 32 or ord(c) == 127 for c in filename) or "/" in filename or "\\" in filename:
        raise HTTPException(422, "Usa un nombre de archivo, sin rutas ni caracteres de control")
    declared = request.headers.get("content-length")
    if declared and (not declared.isdecimal() or int(declared) > settings.tree_import_max_bytes):
        raise HTTPException(413, "El archivo supera el límite de importación")
    # Tenant/client scoped lock; the same upload intent cannot create two jobs.
    lock = int.from_bytes(hashlib.sha256(principal.tenant_id.bytes + client_id.bytes).digest()[:8], "big", signed=True)
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock})
    with TemporaryDirectory(prefix="gensuite-tree-upload-") as tmp:
        path, size, digest = Path(tmp) / "input.bin", 0, hashlib.sha256()
        with open(path, "wb") as target:
            async for chunk in request.stream():
                size += len(chunk)
                if size > settings.tree_import_max_bytes:
                    raise HTTPException(413, "El archivo supera el límite de importación")
                digest.update(chunk)
                target.write(chunk)
        if not size: raise HTTPException(422, "El archivo está vacío")
        existing = await db.scalar(select(TreeImport).where(TreeImport.tenant_id == principal.tenant_id, TreeImport.client_id == client_id))
        if existing:
            if (existing.checksum, existing.byte_size, existing.kind) != (digest.hexdigest(), size, kind):
                raise HTTPException(409, "Este identificador de envío ya corresponde a otro contenido")
            return await describe(db, existing)
        iid = uuid.uuid4()
        key = f"{principal.tenant_id}/tree-imports/{iid}/{uuid.uuid4()}.{'gdz' if kind == 'gedzip' else 'ged'}"
        # Job is not committed yet: staging receipts are tenant-owned, not attached
        # to an invisible job. An uncertain upload stays charged, never guessed away.
        await storage.upload_file(storage.bucket_for("private"), key, str(path), "application/octet-stream")
    job = Job(id=iid, tenant_id=principal.tenant_id, created_by=principal.user_id,
              type="tree_import_prepare", status="queued", params={"kind": kind})
    db.add(job)
    await schedule(db, job, "prepare_tree_import")
    row = TreeImport(id=iid, tenant_id=principal.tenant_id, client_id=client_id, kind=kind,
                     filename=filename, storage_key=key, checksum=digest.hexdigest(), byte_size=size)
    db.add(row)
    await db.flush()
    return await describe(db, row)


@router.get("")
async def list_imports(principal=Depends(get_current_principal), db=Depends(get_tenant_db)):
    rows = (await db.scalars(select(TreeImport).where(TreeImport.tenant_id == principal.tenant_id)
                            .order_by(TreeImport.created_at.desc(), TreeImport.id).limit(100))).all()
    return [await describe(db, row) for row in rows]


@router.get("/{import_id}/audit")
async def audit_import(import_id: uuid.UUID, principal=Depends(get_current_principal), db=Depends(get_tenant_db)):
    row = await get_import(db, principal.tenant_id, import_id)
    if row.report is None: raise HTTPException(409, "El análisis todavía no ha terminado")
    return JSONResponse(row.report, headers={"Cache-Control": "private, no-store"})


class ApplyRequest(BaseModel):
    review_token: str = Field(pattern=r"^[a-f0-9]{64}$")
    acknowledge_append: Literal[True]
    retry_failed: bool = False


@router.post("/{import_id}/apply", status_code=202, dependencies=_WRITE)
async def apply_import(import_id: uuid.UUID, body: ApplyRequest,
        principal=Depends(get_current_principal), db=Depends(get_tenant_db)):
    row = await get_import(db, principal.tenant_id, import_id, lock=True)
    if not row.report or row.report.get("review_token") != body.review_token:
        raise HTTPException(409, "Revisa el informe exacto antes de incorporar el árbol")
    if row.apply_job_id:
        previous = await db.get(Job, row.apply_job_id)
        if previous.status not in {"error", "canceled", "cancelled", "failed"}:
            return await describe(db, row)
        if not body.retry_failed:
            raise HTTPException(409, "El intento anterior no terminó; confirma expresamente el reintento")
    job = Job(tenant_id=principal.tenant_id, created_by=principal.user_id, type="tree_import_apply",
              status="queued", params={"import_id": str(row.id), "review_token": body.review_token})
    db.add(job)
    await schedule(db, job, "apply_tree_import", import_id=str(row.id))
    row.apply_job_id = job.id
    await db.flush()
    return await describe(db, row)


@router.get("/{import_id}/original")
async def original_import(import_id: uuid.UUID, request: Request,
        principal=Depends(get_current_principal), db=Depends(get_tenant_db)):
    row = await get_import(db, principal.tenant_id, import_id)
    selected = parse_range(request.headers.get("range"), row.byte_size)
    if request.headers.get("if-range") not in {None, f'"{row.checksum}"'}: selected = None
    headers = {"Accept-Ranges": "bytes", "ETag": f'"{row.checksum}"', "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff", "Content-Disposition": f'attachment; filename="{row.id}.{"gdz" if row.kind == "gedzip" else "ged"}"',
        "Content-Length": str(selected[1] - selected[0] + 1 if selected else row.byte_size)}
    if selected: headers["Content-Range"] = f"bytes {selected[0]}-{selected[1]}/{row.byte_size}"
    obj = await storage.open_object(storage.bucket_for("private"), row.storage_key,
                                   byte_range=f"bytes={selected[0]}-{selected[1]}" if selected else None)
    async def chunks():
        try:
            while part := await asyncio.to_thread(obj["Body"].read, 1024**2): yield part
        finally:
            await asyncio.to_thread(obj["Body"].close)
    return StreamingResponse(chunks(), status_code=206 if selected else 200, headers=headers, media_type="application/octet-stream")
