"""Resumable uploads through the authenticated API; no direct storage URLs."""
from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func, select, tuple_

from ...core import storage
from ...core.deps import get_current_principal, get_tenant_db, require_roles
from ...core.dispatch import schedule
from ...models.ingest import IngestBook, IngestFile, IngestSession
from ...models.job import Job
from ...models.membership import MembershipRole
from ...models.tenant import Tenant
from ...settings import settings

CHUNK_BYTES = 8 * 1024 ** 2
MAX_IMAGE_BYTES = 256 * 1024 ** 2
MAX_PDF_BYTES = 8 * 1024 ** 3
router = APIRouter(prefix="/ingestion", tags=["ingestion"])
_WRITE = (MembershipRole.tenant_admin.value, MembershipRole.researcher.value)


def safe_relative_path(value: str) -> str:
    if not value or len(value) > 1024 or "\\" in value or ":" in value or any(ord(c) < 32 for c in value):
        raise ValueError("Ruta relativa no válida")
    if any(part in ("", ".", "..") for part in value.split("/")):
        raise ValueError("No se admiten rutas absolutas ni saltos de directorio")
    return value


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NewSession(StrictModel):
    client_id: uuid.UUID
    title: str = Field(min_length=1, max_length=512)
    expected_files: int = Field(ge=1, le=100_000)
    reserved_bytes: int = Field(ge=1)
    options: BookOptions = Field(default_factory=lambda: BookOptions())


class BookOptions(StrictModel):
    source_origin: str = Field(default="own_photo", max_length=64)
    may_contain_living: bool = False
    record_type: str | None = Field(default=None, max_length=32)
    municipality: str | None = Field(default=None, max_length=512)
    municipality_lat: float | None = Field(default=None, ge=-90, le=90)
    municipality_lng: float | None = Field(default=None, ge=-180, le=180)
    year_from: int | None = Field(default=None, ge=1, le=9999)
    year_to: int | None = Field(default=None, ge=1, le=9999)
    book_number: int | None = Field(default=None, ge=0)
    is_index: bool = False

    @model_validator(mode="after")
    def ordered_years(self):
        if self.year_from and self.year_to and self.year_from > self.year_to:
            raise ValueError("El año inicial no puede superar al final")
        return self


class ManifestEntry(StrictModel):
    path: str
    group: str = Field(min_length=1, max_length=1024)
    title: str = Field(min_length=1, max_length=512)
    kind: Literal["pdf", "image_set"]
    position: int = Field(ge=1, le=100_000)
    byte_size: int = Field(ge=1, le=MAX_PDF_BYTES)
    last_modified: int = Field(ge=0)
    options: BookOptions = Field(default_factory=BookOptions)

    @field_validator("path")
    @classmethod
    def safe_path(cls, value):
        return safe_relative_path(value)

    @model_validator(mode="after")
    def size_and_kind(self):
        if self.kind == "image_set" and self.byte_size > MAX_IMAGE_BYTES:
            raise ValueError("La imagen supera 256 MiB")
        if self.kind == "pdf" and self.position != 1:
            raise ValueError("Cada PDF debe constituir un libro independiente")
        return self


class ManifestBatch(StrictModel):
    files: list[ManifestEntry] = Field(min_length=1, max_length=1000)


def session_out(row):
    return {"id": str(row.id), "title": row.title, "status": row.status,
            "expected_files": row.expected_files, "reserved_bytes": row.reserved_bytes,
            "declared_files": row.declared_files, "declared_bytes": row.declared_bytes,
            "received_bytes": row.received_bytes, "job_id": str(row.job_id) if row.job_id else None,
            "chunk_bytes": CHUNK_BYTES, "created_at": row.created_at, "updated_at": row.updated_at,
            "inactive_candidate": row.status in {"manifest", "uploading", "error", "partial"} and
                row.updated_at < datetime.now(timezone.utc) - timedelta(days=30), "options": row.options}


def file_out(row, *, parts=False):
    result = {"id": str(row.id), "book_id": str(row.book_id), "path": row.relative_path,
              "position": row.position, "byte_size": row.byte_size, "last_modified": row.last_modified,
              "received_bytes": row.received_bytes, "status": row.status, "checksum": row.checksum,
              "error": row.error}
    if parts:
        # Never expose bucket names or object keys.
        result["parts"] = {offset: {"sha256": part["sha256"], "size": part["size"]}
                           for offset, part in row.parts.items()}
    return result


async def owned(db, tenant_id, session_id, *, lock=False):
    stmt = select(IngestSession).where(IngestSession.id == session_id, IngestSession.tenant_id == tenant_id)
    row = await db.scalar(stmt.with_for_update() if lock else stmt)
    if row is None:
        raise HTTPException(404, "Sesión de importación no encontrada")
    return row


async def create_session(db, tenant_id, user_id, body: NewSession):
    # Serializes quota admission even when several tabs/importers create collections at once.
    await db.scalar(select(Tenant.id).where(Tenant.id == tenant_id).with_for_update())
    session_id = uuid.uuid5(tenant_id, str(body.client_id))
    existing = await db.get(IngestSession, session_id)
    if existing:
        if (existing.title, existing.expected_files, existing.reserved_bytes, existing.options) != (body.title, body.expected_files, body.reserved_bytes, body.options.model_dump()):
            raise HTTPException(409, "Ese identificador ya pertenece a otro manifiesto")
        return existing
    if body.expected_files > settings.ingest_max_files:
        raise HTTPException(413, f"La colección supera {settings.ingest_max_files} archivos")
    from .cleanup import admission_bytes
    reserved = await admission_bytes(db, tenant_id)
    if int(reserved or 0) + body.reserved_bytes > settings.ingest_tenant_quota_bytes:
        raise HTTPException(413, "La colección supera la cuota de admisión de originales del espacio; revisa las sesiones pendientes o la configuración del servidor")
    row = IngestSession(id=session_id, tenant_id=tenant_id, created_by=user_id,
        title=body.title, expected_files=body.expected_files, reserved_bytes=body.reserved_bytes,
        declared_files=0, declared_bytes=0, received_bytes=0, status="manifest", options=body.options.model_dump())
    db.add(row)
    await db.flush()
    return row


async def add_manifest(db, tenant_id, session_id, entries: list[ManifestEntry]):
    row = await owned(db, tenant_id, session_id, lock=True)
    if row.status in {"cleaning", "discarded"}:
        raise HTTPException(409, "La sesión está cerrada para limpieza; no se puede reanudar ni alterar su manifiesto.")
    file_ids = [uuid.uuid5(session_id, entry.path) for entry in entries]
    book_ids = [uuid.uuid5(session_id, "book:" + entry.group) for entry in entries]
    existing_files = {f.id: f for f in (await db.scalars(select(IngestFile).where(IngestFile.id.in_(file_ids)))).all()}
    existing_books = {b.id: b for b in (await db.scalars(select(IngestBook).where(IngestBook.id.in_(book_ids)))).all()}
    occupied = (await db.execute(select(IngestFile.book_id, IngestFile.position, IngestFile.id).where(
        tuple_(IngestFile.book_id, IngestFile.position).in_([(bid, entry.position) for bid, entry in zip(book_ids, entries)])))).all()
    existing_positions = {(bid, position): fid for bid, position, fid in occupied}
    output = []
    for entry, fid, bid in zip(entries, file_ids, book_ids):
        other = existing_positions.get((bid, entry.position))
        if other and other != fid:
            raise HTTPException(409, "Dos archivos ocupan la misma posición de página")
        existing_positions[(bid, entry.position)] = fid
        book = existing_books.get(bid)
        options = entry.options.model_dump()
        if book and (book.group_key, book.title, book.kind, book.options) != (entry.group, entry.title, entry.kind, options):
            raise HTTPException(409, "Los metadatos de un libro no coinciden con su manifiesto guardado")
        old = existing_files.get(fid)
        if old:
            if (old.relative_path, old.book_id, old.position, old.byte_size, old.last_modified) != (entry.path, bid, entry.position, entry.byte_size, entry.last_modified):
                raise HTTPException(409, "El archivo local cambió; crea una sesión nueva para no mezclar originales")
            output.append(file_out(old))
            continue
        if row.status != "manifest":
            raise HTTPException(409, "El manifiesto está cerrado; no admite archivos nuevos")
        if row.declared_files + 1 > row.expected_files or row.declared_bytes + entry.byte_size > row.reserved_bytes:
            raise HTTPException(409, "El manifiesto supera el número o tamaño reservado")
        if book is None:
            book = IngestBook(id=bid, tenant_id=tenant_id, session_id=session_id, group_key=entry.group,
                title=entry.title, kind=entry.kind, options=options, status="pending")
            db.add(book)
            await db.flush([book])
            existing_books[bid] = book
        item = IngestFile(id=fid, tenant_id=tenant_id, session_id=session_id, book_id=bid,
            relative_path=entry.path, path_hash=hashlib.sha256(entry.path.encode()).hexdigest(),
            position=entry.position, byte_size=entry.byte_size, last_modified=entry.last_modified,
            received_bytes=0, status="pending", parts={})
        db.add(item)
        existing_files[fid] = item
        row.declared_files += 1
        row.declared_bytes += entry.byte_size
        output.append(file_out(item))
    # Detect overlapping page positions before the DB constraint becomes an opaque error.
    seen = set()
    for entry, bid in zip(entries, book_ids):
        pair = (bid, entry.position)
        if pair in seen:
            raise HTTPException(409, "Dos entradas del lote ocupan la misma posición")
        seen.add(pair)
    await db.flush()
    return output


async def read_chunk(request: Request, expected: int) -> bytes:
    length = request.headers.get("content-length")
    if length is not None and (len(length) > 20 or not re.fullmatch(r"[0-9]+", length) or int(length) != expected):
        raise HTTPException(413, "Tamaño de fragmento no válido")
    data = bytearray()
    async for part in request.stream():
        if len(data) + len(part) > expected:
            raise HTTPException(413, "El fragmento supera el tamaño declarado")
        data.extend(part)
    if len(data) != expected:
        raise HTTPException(422, "El fragmento llegó incompleto")
    return bytes(data)


async def put_chunk(db, tenant_id, session_id, file_id, offset, checksum, request):
    row = await owned(db, tenant_id, session_id, lock=True)
    if row.status in {"cleaning", "discarded"}:
        raise HTTPException(409, "La sesión está cerrada para limpieza; conserva tus originales locales y crea otra sesión si necesitas importarlos.")
    item = await db.scalar(select(IngestFile).where(IngestFile.id == file_id, IngestFile.session_id == session_id).with_for_update())
    if item is None:
        raise HTTPException(404, "Archivo no encontrado")
    if offset < 0 or offset % CHUNK_BYTES or offset >= item.byte_size:
        raise HTTPException(416, "Desplazamiento de fragmento no válido")
    if not re.fullmatch(r"[a-f0-9]{64}", checksum):
        raise HTTPException(422, "Checksum SHA-256 no válido")
    expected = min(CHUNK_BYTES, item.byte_size - offset)
    data = await read_chunk(request, expected)
    if hashlib.sha256(data).hexdigest() != checksum:
        raise HTTPException(422, "El fragmento no coincide con su checksum")
    previous = item.parts.get(str(offset))
    if previous:
        if previous["sha256"] != checksum or previous["size"] != expected:
            raise HTTPException(409, "El fragmento ya guardado contiene otros datos; no se sobrescriben originales")
        return file_out(item)
    if row.status not in ("manifest", "uploading"):
        raise HTTPException(409, "Esta sesión no admite más fragmentos")
    if offset != item.received_bytes:
        raise HTTPException(409, f"Reanuda desde el byte {item.received_bytes}")
    key = f"{tenant_id}/ingestion/{session_id}/{item.id}/{offset:016d}-{checksum}"
    await storage.put_object(storage.bucket_for("private"), key, data, "application/octet-stream")
    item.parts = {**item.parts, str(offset): {"sha256": checksum, "size": expected, "key": key}}
    item.received_bytes += expected
    row.received_bytes += expected
    item.status = "uploaded" if item.received_bytes == item.byte_size else "uploading"
    await db.flush()
    return file_out(item)


@router.get("")
async def list_sessions(offset: int = Query(0, ge=0), inactive_days: int | None = Query(None, ge=1, le=3650),
                        principal=Depends(get_current_principal), db=Depends(get_tenant_db)):
    statement = select(IngestSession).where(IngestSession.tenant_id == principal.tenant_id)
    if inactive_days is not None:
        statement = statement.where(IngestSession.status.in_(("manifest", "uploading", "error", "partial")),
            IngestSession.updated_at < datetime.now(timezone.utc) - timedelta(days=inactive_days))
    rows = (await db.scalars(statement.order_by(IngestSession.created_at.desc(), IngestSession.id).offset(offset).limit(100))).all()
    return [session_out(row) for row in rows]


@router.post("", status_code=201, dependencies=[Depends(require_roles(*_WRITE))])
async def new_session(body: NewSession, principal=Depends(get_current_principal), db=Depends(get_tenant_db)):
    return session_out(await create_session(db, principal.tenant_id, principal.user_id, body))


@router.get("/{session_id}")
async def get_session(session_id: uuid.UUID, principal=Depends(get_current_principal), db=Depends(get_tenant_db)):
    row = await owned(db, principal.tenant_id, session_id)
    result = session_out(row)
    job = await db.get(Job, row.job_id) if row.job_id else None
    result.update({"job_status": job.status if job else None, "job_error": job.error if job else None})
    return result


@router.post("/{session_id}/manifest", dependencies=[Depends(require_roles(*_WRITE))])
async def append_manifest(session_id: uuid.UUID, body: ManifestBatch, principal=Depends(get_current_principal), db=Depends(get_tenant_db)):
    return await add_manifest(db, principal.tenant_id, session_id, body.files)


@router.post("/{session_id}/seal", dependencies=[Depends(require_roles(*_WRITE))])
async def seal_manifest(session_id: uuid.UUID, principal=Depends(get_current_principal), db=Depends(get_tenant_db)):
    row = await owned(db, principal.tenant_id, session_id, lock=True)
    if row.status in {"cleaning", "discarded"}:
        raise HTTPException(409, "La sesión está cerrada para limpieza.")
    if row.declared_files != row.expected_files or row.declared_bytes != row.reserved_bytes:
        raise HTTPException(409, "El manifiesto no contiene todos los archivos reservados")
    # Contiguous page positions and exactly one original per PDF, including across batches.
    invalid = await db.scalar(select(IngestBook.id).join(IngestFile, IngestFile.book_id == IngestBook.id)
        .where(IngestBook.session_id == session_id).group_by(IngestBook.id, IngestBook.kind)
        .having((func.min(IngestFile.position) != 1) | (func.max(IngestFile.position) != func.count(IngestFile.id)) |
                ((IngestBook.kind == "pdf") & (func.count(IngestFile.id) != 1))).limit(1))
    if invalid:
        raise HTTPException(409, "Hay huecos o duplicados en el orden de páginas de un libro")
    if row.status == "manifest":
        row.status = "uploading"
    await db.flush()
    return session_out(row)


@router.get("/{session_id}/files")
async def list_files(session_id: uuid.UUID, after: uuid.UUID | None = None, limit: int = Query(500, ge=1, le=1000),
                     principal=Depends(get_current_principal), db=Depends(get_tenant_db)):
    await owned(db, principal.tenant_id, session_id)
    stmt = select(IngestFile).where(IngestFile.session_id == session_id)
    if after:
        stmt = stmt.where(IngestFile.id > after)
    return [file_out(f) for f in (await db.scalars(stmt.order_by(IngestFile.id).limit(limit))).all()]


@router.get("/{session_id}/books")
async def list_books(session_id: uuid.UUID, after: uuid.UUID | None = None, limit: int = Query(100, ge=1, le=1000),
                     principal=Depends(get_current_principal), db=Depends(get_tenant_db)):
    await owned(db, principal.tenant_id, session_id)
    stmt = select(IngestBook).where(IngestBook.session_id == session_id)
    if after:
        stmt = stmt.where(IngestBook.id > after)
    return [{"id": str(b.id), "group": b.group_key, "title": b.title, "kind": b.kind, "options": b.options,
             "status": b.status, "error": b.error, "document_id": str(b.document_id) if b.document_id else None}
            for b in (await db.scalars(stmt.order_by(IngestBook.id).limit(limit))).all()]


@router.get("/{session_id}/files/{file_id}")
async def get_file(session_id: uuid.UUID, file_id: uuid.UUID, principal=Depends(get_current_principal), db=Depends(get_tenant_db)):
    await owned(db, principal.tenant_id, session_id)
    item = await db.scalar(select(IngestFile).where(IngestFile.id == file_id, IngestFile.session_id == session_id))
    if item is None:
        raise HTTPException(404, "Archivo no encontrado")
    return file_out(item, parts=True)


@router.put("/{session_id}/files/{file_id}/chunks/{offset}", dependencies=[Depends(require_roles(*_WRITE))])
async def upload_chunk(session_id: uuid.UUID, file_id: uuid.UUID, offset: int, request: Request,
                       principal=Depends(get_current_principal), db=Depends(get_tenant_db)):
    return await put_chunk(db, principal.tenant_id, session_id, file_id, offset,
                           request.headers.get("x-content-sha256", ""), request)


@router.post("/{session_id}/complete", status_code=202, dependencies=[Depends(require_roles(*_WRITE))])
async def complete_session(session_id: uuid.UUID, principal=Depends(get_current_principal), db=Depends(get_tenant_db)):
    row = await owned(db, principal.tenant_id, session_id, lock=True)
    if row.status in {"cleaning", "discarded"}:
        raise HTTPException(409, "La sesión está cerrada para limpieza; no se vuelven a procesar sus fragmentos.")
    if row.status == "completed":
        return session_out(row)
    if row.job_id:
        job = await db.get(Job, row.job_id)
        if job and job.status in ("queued", "running"):
            return session_out(row)
    if row.status not in ("uploading", "error", "partial", "processing") or row.received_bytes != row.reserved_bytes:
        raise HTTPException(409, "Cierra el manifiesto y termina de enviar todos los fragmentos")
    job = Job(tenant_id=principal.tenant_id, created_by=principal.user_id, type="ingest_collection", status="queued",
              params={"session_id": str(session_id)})
    db.add(job)
    await schedule(db, job, "ingest_collection", session_id=str(session_id))
    row.job_id, row.status = job.id, "processing"
    await db.flush()
    return session_out(row)
