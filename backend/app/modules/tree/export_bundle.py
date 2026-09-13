"""Bounded snapshot planning and private GEDZIP writing, with original-byte checks."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shutil
import zipfile
from pathlib import Path

from sqlalchemy import select, text

from ...core import storage
from ...models.document import Document, Page
from ...settings import settings
from ..storage_audit.inventory import validate_key
from . import exporter, gedcom, gedcom7
from .gedcom import Node

CHUNK = 1024 * 1024
_MIME_EXT = {"application/pdf": "pdf", "image/jpeg": "jpg", "image/png": "png",
             "image/tiff": "tif", "image/webp": "webp"}


class ExportError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


async def admission(db, tenant_id, media):
    tables = ["persons", "names", "families", "family_children", "events", "places", "research_sources", "repositories", "citations", "source_documents"]
    if media:
        tables += ["documents", "pages"]
    rows = size = 0
    for table in tables:
        # Fixed identifiers, never supplied by a client. Text length includes TOAST
        # payloads, unlike pg_column_size of a compressed/detoasted row.
        count, length = (await db.execute(text(f"SELECT count(*), COALESCE(sum(octet_length(row_to_json(t)::text)),0) FROM {table} t WHERE tenant_id=:tenant"),
                                         {"tenant": tenant_id})).one()
        rows += count
        size += length
        if rows > settings.tree_export_max_rows or size > settings.tree_export_max_graph_bytes:
            raise ExportError("El árbol supera el límite de filas o memoria de exportación. Ajusta la capacidad del worker antes de reintentarlo.")


async def snapshot(db, tenant_id, kind):
    """Caller supplies one REPEATABLE READ, read-only RLS transaction; no commits."""
    await admission(db, tenant_id, kind == "gedzip")
    records, report, evidence = await exporter.build_graph(db, tenant_id, version="5.5.1" if kind == "gedcom551" else "7.0")
    report["snapshot"] = {"isolation": "repeatable_read", "captured_at": str(await db.scalar(text("SELECT transaction_timestamp()")))}
    media = []
    if kind == "gedzip":
        documents = list((await db.scalars(select(Document).where(Document.tenant_id == tenant_id,
            Document.image_policy == "retain").order_by(Document.id))).all())
        doc_ids = {doc.id for doc in documents}
        pages_by_doc = {}
        for page in (await db.scalars(select(Page).where(Page.tenant_id == tenant_id, Page.document_id.in_(doc_ids))
                                     .order_by(Page.document_id, Page.page_no, Page.id))).all():
            pages_by_doc.setdefault(page.document_id, []).append(page)
        object_refs = {}
        for index, doc in enumerate(documents, 1):
            if doc.doc_type not in {"pdf", "image_set"}:
                raise ExportError("Hay documentos retenidos con un formato no exportable; no se ha generado un paquete parcial.")
            rows = []
            if doc.doc_type == "pdf":
                rows.append({"key": doc.storage_prefix + "original.pdf", "sha256": doc.fingerprint,
                             "mime": "application/pdf", "size": None, "filename": "original.pdf"})
            else:
                pages = pages_by_doc.get(doc.id, [])
                if not pages or len(pages) != doc.page_count or any(p.image_purged for p in pages):
                    raise ExportError("Faltan páginas originales de un documento retenido; no se ha generado un paquete parcial.")
                rows = [{"key": p.storage_key, "sha256": p.checksum, "mime": p.content_type,
                         "size": p.byte_size, "filename": p.original_filename, "page_no": p.page_no} for p in pages]
            obj = Node(tag="OBJE", xref=f"@M{index}@")
            object_refs[doc.id] = obj.xref
            for number, row in enumerate(rows, 1):
                if not isinstance(row["sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", row["sha256"]):
                    raise ExportError("Hay originales históricos sin checksum previo. Verifica su integridad antes de exportar GEDZIP.")
                if row["mime"] not in _MIME_EXT:
                    raise ExportError("El tipo de archivo original no está admitido en este perfil GEDZIP.")
                validate_key(tenant_id, row["key"])
                if doc.storage_bucket not in {storage.bucket_for("private"), storage.bucket_for("public")}:
                    raise ExportError("El documento referencia un almacenamiento no configurado.")
                archive_path = f"media/{doc.id}/{number}.{_MIME_EXT[row['mime']]}"
                media.append({**row, "bucket": doc.storage_bucket, "document_id": str(doc.id), "path": archive_path})
                obj.children.append(Node(tag="FILE", value=archive_path, is_pointer=False, children=[
                    Node(tag="FORM", value=row["mime"]), Node(tag="TITL", value=doc.title)]))
                if len(media) > settings.tree_export_max_files:
                    raise ExportError("La exportación supera el límite de archivos del worker.")
            records.insert(-1, obj)
        represented = set()
        for source in [r for r in records if r.tag == "SOUR"]:
            for doc_id in sorted(evidence.source_documents.get(source.xref, set()), key=str):
                if doc_id in object_refs:
                    source.children.append(Node(tag="OBJE", value=object_refs[doc_id], is_pointer=True))
                    represented.add(doc_id)
        # Retained documents need not already have a genealogy citation to survive.
        # They become bibliography entries, never evidence for an invented person.
        for index, doc in enumerate(documents, 1):
            if doc.id not in represented:
                records.insert(-1, Node(tag="SOUR", xref=f"@D{index}@", children=[Node(tag="TITL", value=doc.title),
                    Node(tag="OBJE", value=object_refs[doc.id], is_pointer=True),
                    Node(tag="NOTE", value="Documento de la biblioteca sin enlace a una fuente exportada.")]))
        report["media_scope"] = "own_retained_documents"
        report["media_documents"] = len(documents)
        report["media_files"] = len(media)
        report["excluded_records"]["documents"] -= len(documents)
        report["counts"]["media_sources_added"] = len(documents) - len(represented)
        report["warnings"][0] = "Incluye originales retenidos propios; excluye documentos de otros espacios, documentos de solo datos, derivados PDF/A, revisiones, inferencias, historial, permisos y configuración. No es una copia de seguridad."
        report["warnings"].append("Los documentos se enlazan a fuentes, no se atribuyen automáticamente como evidencia de nuevas personas. No incluye los manifiestos históricos de ingesta ni medios personales.")
    if kind == "gedcom551":
        content = gedcom.serialize(records)
    else:
        records, changes = gedcom7.convert(records)
        content = gedcom7.serialize(records)
        report.update({"format": "GEDZIP (GEDCOM 7.0)" if kind == "gedzip" else "GEDCOM 7.0",
                       "conversions": changes, "extensions": changes["extensions"]})
        report["warnings"].append("Las estructuras antiguas se conservan como JSON en _GS_LEGACY; las fechas no reconocidas quedan como PHRASE. Su interpretación y la importación GEDCOM 7/GEDZIP en gen_suite siguen pendientes.")
    if len(content.encode("utf-8")) > settings.tree_export_max_graph_bytes:
        raise ExportError("El GEDCOM generado supera el límite de memoria configurado.")
    return content, report, media


async def write_bundle(path: Path, content: str, report: dict, media: list, checkpoint):
    """One S3 response and 1 MiB buffer at a time; no filesystem paths from input."""
    manifest = []
    total = len(content.encode("utf-8"))
    if total > settings.tree_export_max_bytes:
        raise ExportError("La exportación supera el límite de bytes configurado.")
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        archive.writestr("gedcom.ged", content.encode("utf-8"))
        for index, item in enumerate(media):
            await checkpoint(stage="media", done=index, total=len(media))
            obj = await storage.open_object(item["bucket"], item["key"])
            body = obj["Body"]
            try:
                size = obj.get("ContentLength")
                if type(size) is not int or size <= 0 or (item["size"] is not None and size != item["size"]):
                    raise ExportError("Un original cambió de tamaño o no acredita su longitud.")
                if total + size > settings.tree_export_max_bytes:
                    raise ExportError("Los originales superan el límite de bytes de exportación.")
                if shutil.disk_usage(path.parent).free < size + 16 * CHUNK:
                    raise ExportError("No hay suficiente espacio temporal para el siguiente original.")
                digest, received = hashlib.sha256(), 0
                with archive.open(item["path"], "w", force_zip64=True) as output:
                    while received < size:
                        await checkpoint(stage="media", done=index, total=len(media))
                        chunk = await asyncio.to_thread(body.read, min(CHUNK, size - received))
                        if not chunk:
                            raise ExportError("La descarga de un original terminó antes de tiempo.")
                        received += len(chunk)
                        digest.update(chunk)
                        await asyncio.to_thread(output.write, chunk)
                    if await asyncio.to_thread(body.read, 1):
                        raise ExportError("El original supera la longitud acreditada.")
                if digest.hexdigest() != item["sha256"]:
                    raise ExportError("Un original no coincide con su checksum. No se ha publicado una exportación parcial.")
                total += size
                manifest.append({"path": item["path"], "document_id": item["document_id"], "filename": item["filename"],
                    "page_no": item.get("page_no"), "mime": item["mime"], "size": size, "sha256": digest.hexdigest()})
            finally:
                await asyncio.to_thread(body.close)
        report["media_manifest_sha256"] = hashlib.sha256(canonical(manifest)).hexdigest()
        archive.writestr("gen-suite/media-manifest.json", canonical(manifest))
        archive.writestr("gen-suite/interchange-report.json", canonical(exporter.seal_report(content, report)))
    if path.stat().st_size > settings.tree_export_max_bytes:
        raise ExportError("El paquete con sus metadatos supera el límite de bytes configurado.")


def file_checksum(path):
    with open(path, "rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()
