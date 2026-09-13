"""Atomic reviewed append; S3 attempts stay charged if DB publication is cancelled."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import time
from urllib.parse import quote
import uuid

from sqlalchemy import func, select, text, update

from ..core import storage
from ..core.dispatch import schedule
from ..core.task_execution import serialized_job
from ..db.rls import set_rls_context
from ..db.session import SessionLocal
from ..models.document import Document, Page
from ..models.job import Job
from ..models.tree_import import SourceDocument, TreeImport
from ..modules.tree import import_bundle, importer
from ..modules.tree.export_bundle import canonical
from ..modules.tree.gedcom7_import import ImportError
from .document_tasks import _publish


class ImportCancelled(Exception): pass


async def disk_call(function, *args):
    # Cancellation must not remove the temp directory under a still-running read.
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try: await task
        finally: raise


async def run_import(*, job_id, tenant_id, import_id, apply):
    last = 0.0
    async def checkpoint(stage, done=0, total=1, *, force=False):
        nonlocal last
        if not force and time.monotonic() - last < 1: return
        async with SessionLocal() as db:
            await set_rls_context(db, tenant_id=tenant_id)
            changed = await db.execute(update(Job).where(Job.id == job_id, Job.status.in_(("queued", "running")))
                .values(status="running", started_at=func.coalesce(Job.started_at, func.now()), progress={"stage": stage, "done": done, "total": total}))
            if not changed.rowcount: raise ImportCancelled()
            await db.commit()
        last = time.monotonic()
        await _publish(tenant_id, job_id, {"kind": "progress", "stage": stage, "done": done, "total": total})

    try:
        await checkpoint("download", force=True)
        async with SessionLocal() as db:
            await set_rls_context(db, tenant_id=tenant_id)
            row = await db.get(TreeImport, import_id)
            job = await db.get(Job, job_id)
            if not row or job.type != ("tree_import_apply" if apply else "tree_import_prepare"):
                raise ImportError("La tarea no corresponde a esta importación.")
            if apply and (row.apply_job_id != job_id or not row.report or (job.params or {}).get("review_token") != row.report.get("review_token")):
                raise ImportCancelled()
            source = {"key": row.storage_key, "size": row.byte_size, "sha256": row.checksum,
                      "kind": row.kind, "filename": row.filename, "report": row.report}
            creator = job.created_by
        with TemporaryDirectory(prefix="gensuite-tree-import-") as tmp:
            path = Path(tmp) / "input.bin"
            obj = await storage.open_object(storage.bucket_for("private"), source["key"])
            size, digest = 0, hashlib.sha256()
            try:
                if obj.get("ContentLength") != source["size"]: raise ImportError("El original cambió de tamaño.")
                with open(path, "wb") as target:
                    while part := await disk_call(obj["Body"].read, import_bundle.CHUNK):
                        size += len(part)
                        if size > source["size"]: raise ImportError("El original supera su tamaño registrado.")
                        target.write(part); digest.update(part)
                        await checkpoint("download", size, source["size"])
            finally:
                await disk_call(obj["Body"].close)
            if size != source["size"] or digest.hexdigest() != source["sha256"]:
                raise ImportError("El original no coincide con su huella de integridad.")
            await checkpoint("inspect", force=True)
            nodes, raw, report, books = await disk_call(import_bundle.prepare, path, source["kind"])
            await checkpoint("review" if not apply else "originals", force=True)
            if apply and report["review_token"] != source["report"].get("review_token"):
                raise ImportError("El análisis ha cambiado. No se puede aplicar una revisión distinta.")
            documents, pages, object_docs = [], [], {}
            if apply and books:
                archive = await disk_call(import_bundle.checked_archive, path)
                try:
                    part_path = Path(tmp) / "media.bin"
                    for number, book in enumerate(books, 1):
                        did = uuid.uuid4()
                        prefix = f"{tenant_id}/{did}/"
                        bucket = storage.bucket_for("private")
                        doc = Document(id=did, tenant_id=tenant_id, title=book["title"], doc_type=book["kind"],
                            storage_bucket=bucket, storage_prefix=prefix, visibility="private", image_policy="retain",
                            may_contain_living=True, source_kind="upload", source_origin="gedzip", created_by=creator,
                            source_ref=f"gedzip:{source['sha256']}#{quote(book['xref'])}",
                            page_count=0 if book["kind"] == "pdf" else len(book["files"]),
                            fingerprint=book["files"][0]["sha256"] if book["kind"] == "pdf" else None)
                        manifest = {"schema_version": 1, "document_id": str(did), "tree_import_id": str(import_id), "files": []}
                        for position, entry in enumerate(book["files"], 1):
                            await checkpoint("originals", number - 1, len(books), force=True)
                            await disk_call(import_bundle.extract_checked, archive, entry, part_path)
                            key = prefix + ("original.pdf" if book["kind"] == "pdf" else f"pages/{position}.{entry['extension']}")
                            await storage.upload_file(bucket, key, str(part_path), entry["mime"])
                            manifest["files"].append({"filename": entry["path"], "byte_size": entry["size"], "sha256": entry["sha256"], "position": position})
                            if book["kind"] != "pdf":
                                pages.append(Page(id=uuid.uuid4(), tenant_id=tenant_id, document_id=did,
                                    visibility="private", page_no=position, storage_key=key, content_type=entry["mime"],
                                    byte_size=entry["size"], checksum=entry["sha256"], width=entry["width"], height=entry["height"],
                                    original_filename=entry["path"], source_ref=f"gedzip:{source['sha256']}#{quote(entry['path'])}"))
                        await storage.put_object(bucket, prefix + "original-manifest.json", canonical(manifest), "application/json")
                        documents.append(doc)
                        object_docs.setdefault(book["xref"], []).append(doc)
                finally:
                    await disk_call(archive.close)
            await checkpoint("commit", force=True)
            async with SessionLocal() as db:
                await set_rls_context(db, tenant_id=tenant_id)
                # Consistent lock order with HTTP apply. Serialize final place dedup
                # and append per tenant; no tree rows existed during remote I/O.
                await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": int.from_bytes(tenant_id.bytes[:8], "big", signed=True)})
                row = await db.scalar(select(TreeImport).where(TreeImport.id == import_id).with_for_update())
                job = await db.scalar(select(Job).where(Job.id == job_id).with_for_update())
                if not job or job.status != "running" or apply and row.apply_job_id != job_id: raise ImportCancelled()
                if apply:
                    db.add_all(documents)
                    await db.flush()
                    db.add_all(pages)
                    result = await importer.import_gedcom(db, tenant_id, nodes, filename=source["filename"],
                        raw_text=raw, encoding="UTF-8", created_by=creator, version="7.0", return_maps=True)
                    identities = result.pop("identity_map")
                    for node in (n for n in nodes if n.tag == "SOUR"):
                        source_model = identities["sources"].get(node.xref)
                        if not source_model: continue
                        linked = {doc.id: doc for link in node.all("OBJE") for doc in object_docs.get(link.value, [])}
                        for did in linked:
                            db.add(SourceDocument(tenant_id=tenant_id, source_id=source_model.id, document_id=did))
                        if linked: source_model.primary_document_id = next(iter(linked))
                    raster_jobs = []
                    for doc in documents:
                        if doc.doc_type == "pdf":
                            raster = Job(tenant_id=tenant_id, created_by=creator, type="rasterize", status="queued", params={"document_id": str(doc.id)})
                            db.add(raster)
                            await schedule(db, raster, "rasterize_document", document_id=str(doc.id))
                            raster_jobs.append(str(raster.id))
                    result.update(import_id=str(result["import_id"]), documents=len(documents), raster_jobs=raster_jobs)
                    job.result = result
                else:
                    row.report = report
                    job.result = {"import_id": str(import_id), "review_ready": True}
                job.status, job.finished_at = "completed", datetime.now(timezone.utc)
                job.progress = {"stage": "completed", "done": 1, "total": 1}
                await db.commit()
        await _publish(tenant_id, job_id, {"kind": "all_done", "import_id": str(import_id)})
    except ImportCancelled:
        return
    except Exception as error:
        message = str(error) if isinstance(error, ImportError) else "No se pudo completar la importación. No se incorporó un árbol parcial; el original sigue disponible. Revisa formato, integridad y capacidad."
        async with SessionLocal() as db:
            await set_rls_context(db, tenant_id=tenant_id)
            await db.execute(update(Job).where(Job.id == job_id, Job.status.in_(("queued", "running")))
                .values(status="error", error=message, finished_at=datetime.now(timezone.utc)))
            await db.commit()
        await _publish(tenant_id, job_id, {"kind": "book_fail", "error": message})


@serialized_job
async def prepare_tree_import(ctx, *, job_id, tenant_id):
    await run_import(job_id=job_id, tenant_id=tenant_id, import_id=job_id, apply=False)


@serialized_job
async def apply_tree_import(ctx, *, job_id, tenant_id, import_id):
    await run_import(job_id=job_id, tenant_id=tenant_id, import_id=uuid.UUID(str(import_id)), apply=True)
