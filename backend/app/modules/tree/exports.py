"""Durable tree exports. Generating a file is a write; reading it remains private."""
from __future__ import annotations

import asyncio
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select, text

from ...core import storage
from ...core.deps import get_current_principal, get_tenant_db, require_roles
from ...core.dispatch import schedule
from ...models.job import Job
from ...models.membership import MembershipRole
from ...models.tree_export import TreeExport
from ..documents.exports import parse_range

router = APIRouter(prefix="/exports")
KINDS = {"gedcom551": ("ged", "application/octet-stream"), "gedcom7": ("ged", "application/vnd.familysearch.gedcom"),
         "gedzip": ("gdz", "application/zip")}


class ExportRequest(BaseModel):
    kind: Literal["gedcom551", "gedcom7", "gedzip"] = "gedcom7"


@router.post("", status_code=202, dependencies=[Depends(require_roles(
    MembershipRole.tenant_admin.value, MembershipRole.researcher.value))])
async def create_export(body: ExportRequest, principal=Depends(get_current_principal), db=Depends(get_tenant_db)):
    # Serialize creation per tenant; at most one heavy export can be queued/running.
    key = int.from_bytes(principal.tenant_id.bytes[:8], "big", signed=True)
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
    job = await db.scalar(select(Job).where(Job.tenant_id == principal.tenant_id,
        Job.type == "tree_export", Job.status.in_(("queued", "running"))))
    if job and (job.params or {}).get("kind") != body.kind:
        raise HTTPException(409, "Ya hay otra exportación del árbol en curso. Espera o cancélala antes de iniciar otra.")
    if not job:
        job = Job(tenant_id=principal.tenant_id, created_by=principal.user_id, type="tree_export", status="queued",
                  params={"kind": body.kind, "scope": "own_retained_documents" if body.kind == "gedzip" else "genealogy"})
        db.add(job)
        await schedule(db, job, "export_tree")
    return {"id": str(job.id), "status": job.status, "kind": body.kind}


@router.get("")
async def list_exports(principal=Depends(get_current_principal), db=Depends(get_tenant_db)):
    jobs = (await db.scalars(select(Job).where(Job.tenant_id == principal.tenant_id, Job.type == "tree_export")
                            .order_by(Job.created_at.desc(), Job.id).limit(100))).all()
    artifacts = {row.id: row for row in (await db.scalars(select(TreeExport).where(
        TreeExport.tenant_id == principal.tenant_id, TreeExport.id.in_([job.id for job in jobs])))).all()}
    return [{"id": str(job.id), "kind": (job.params or {}).get("kind"), "status": job.status,
        "progress": job.progress, "error": job.error, "created_at": job.created_at,
        "byte_size": artifacts[job.id].byte_size if job.id in artifacts else None,
        "checksum": artifacts[job.id].checksum if job.id in artifacts else None} for job in jobs]


async def artifact(db, tenant_id, export_id):
    row = await db.scalar(select(TreeExport).where(TreeExport.id == export_id, TreeExport.tenant_id == tenant_id))
    if not row:
        raise HTTPException(404, "Exportación no encontrada o todavía no terminada")
    return row


@router.get("/{export_id}/audit")
async def audit_export(export_id: uuid.UUID, principal=Depends(get_current_principal), db=Depends(get_tenant_db)):
    row = await artifact(db, principal.tenant_id, export_id)
    return JSONResponse(row.report, headers={"Cache-Control": "private, no-store",
        "Content-Disposition": f'attachment; filename="{row.id}-audit.json"'})


@router.get("/{export_id}/content")
async def download_export(export_id: uuid.UUID, request: Request,
        review_token: str = Query(pattern=r"^[a-f0-9]{64}$"),
        principal=Depends(get_current_principal), db=Depends(get_tenant_db)):
    row = await artifact(db, principal.tenant_id, export_id)
    if row.report.get("review_token") != review_token:
        raise HTTPException(409, "Revisa el informe de esta exportación antes de descargarla")
    selected = parse_range(request.headers.get("range"), row.byte_size)
    if request.headers.get("if-range") not in (None, f'"{row.checksum}"'):
        selected = None
    extension, mime = KINDS[row.kind]
    headers = {"Accept-Ranges": "bytes", "ETag": f'"{row.checksum}"', "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff", "Content-Disposition": f'attachment; filename="{row.id}.{extension}"',
        "Content-Length": str(selected[1] - selected[0] + 1 if selected else row.byte_size)}
    if selected:
        headers["Content-Range"] = f"bytes {selected[0]}-{selected[1]}/{row.byte_size}"
    obj = await storage.open_object(storage.bucket_for("private"), row.storage_key,
                                   byte_range=f"bytes={selected[0]}-{selected[1]}" if selected else None)
    async def chunks():
        try:
            while chunk := await asyncio.to_thread(obj["Body"].read, 1024 * 1024):
                yield chunk
        finally:
            await asyncio.to_thread(obj["Body"].close)
    return StreamingResponse(chunks(), status_code=206 if selected else 200, headers=headers, media_type=mime)
