"""One private immutable tree export per durable job, with cooperative cancellation."""
from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import select, text, update

from ..core import storage
from ..core.task_execution import serialized_job
from ..db.rls import set_rls_context
from ..db.session import SessionLocal
from ..models.job import Job
from ..models.tree_export import TreeExport
from ..settings import settings
from ..modules.tree import export_bundle, exporter
from .document_tasks import _publish


class ExportCancelled(Exception):
    pass


@serialized_job
async def export_tree(ctx, *, job_id, tenant_id):
    last_checkpoint = 0.0

    async def checkpoint(*, stage, done=0, total=1, force=False):
        nonlocal last_checkpoint
        now = time.monotonic()
        if not force and now - last_checkpoint < 1:
            return
        async with SessionLocal() as db:
            await set_rls_context(db, tenant_id=tenant_id)
            changed = await db.execute(update(Job).where(Job.id == job_id, Job.status.in_(("queued", "running")))
                .values(status="running", progress={"stage": stage, "done": done, "total": total}))
            if not changed.rowcount:
                raise ExportCancelled()
            await db.commit()  # Session ends here: no subsequent query without RLS.
        last_checkpoint = now
        await _publish(tenant_id, job_id, {"kind": "progress", "stage": stage, "done": done, "total": total})

    try:
        async with SessionLocal() as db:
            await set_rls_context(db, tenant_id=tenant_id)
            job = await db.get(Job, job_id)
            if not job or job.type != "tree_export" or (job.params or {}).get("kind") not in {"gedcom551", "gedcom7", "gedzip"}:
                raise export_bundle.ExportError("La tarea de exportación no tiene un formato válido.")
            kind = job.params["kind"]
            await db.execute(update(Job).where(Job.id == job_id, Job.status.in_(("queued", "running")))
                             .values(started_at=job.started_at or datetime.now(timezone.utc)))
            await db.commit()
        await checkpoint(stage="snapshot", force=True)
        # The snapshot transaction ends before slow S3 transfers. Exact DB checksums
        # pin original content afterwards: changes/missing bytes fail closed.
        async with SessionLocal() as db:
            await db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            await db.execute(text("SET TRANSACTION READ ONLY"))
            await set_rls_context(db, tenant_id=tenant_id)
            content, report, media = await export_bundle.snapshot(db, tenant_id, kind)
        if len(content.encode("utf-8")) > settings.tree_export_max_bytes:
            raise export_bundle.ExportError("El GEDCOM supera el límite de bytes de exportación.")
        with TemporaryDirectory(prefix="gensuite-tree-export-") as tmp:
            path = Path(tmp) / ("export.gdz" if kind == "gedzip" else "export.ged")
            await checkpoint(stage="build", force=True)
            if kind == "gedzip":
                await export_bundle.write_bundle(path, content, report, media, checkpoint)
            else:
                await asyncio.to_thread(path.write_bytes, content.encode("utf-8"))
            await checkpoint(stage="upload", force=True)
            # Unique attempt key: uncertain uploads are accounted orphans, never an
            # overwrite of an already published file or a credit guessed from DELETE.
            key = f"{tenant_id}/tree-exports/{job_id}/{uuid.uuid4()}{path.suffix}"
            await storage.upload_file(storage.bucket_for("private"), key, str(path),
                                      "application/zip" if kind == "gedzip" else "application/octet-stream")
            checksum = await asyncio.to_thread(export_bundle.file_checksum, path)
            size = path.stat().st_size
            report = exporter.seal_report(content, report)
            report.update({"export_id": str(job_id), "artifact_sha256": checksum, "artifact_byte_size": size})
            # Bind review to exact immutable artifact as well as the GEDCOM payload.
            report = exporter.seal_report(content, report)
            async with SessionLocal() as db:
                await set_rls_context(db, tenant_id=tenant_id)
                # Race with HTTP cancellation/reaper: only one wins the locked row.
                job = await db.scalar(select(Job).where(Job.id == job_id).with_for_update())
                if not job or job.status != "running":
                    raise ExportCancelled()
                db.add(TreeExport(id=job_id, tenant_id=tenant_id, kind=kind, storage_key=key,
                    checksum=checksum, byte_size=size, report=report))
                job.status, job.finished_at = "completed", datetime.now(timezone.utc)
                job.result = {"export_id": str(job_id), "kind": kind}
                job.progress = {"stage": "completed", "done": len(media), "total": len(media)}
                await db.commit()
        await _publish(tenant_id, job_id, {"kind": "all_done", "export_id": str(job_id)})
    except ExportCancelled:
        return  # Any in-flight S3 receipt remains charged; no published result.
    except Exception as error:
        message = str(error) if isinstance(error, export_bundle.ExportError) else "No se pudo generar la exportación. Revisa la capacidad, la integridad de los originales y el almacenamiento. No se ha publicado un resultado parcial."
        async with SessionLocal() as db:
            await set_rls_context(db, tenant_id=tenant_id)
            await db.execute(update(Job).where(Job.id == job_id, Job.status.in_(("queued", "running")))
                .values(status="error", error=message, finished_at=datetime.now(timezone.utc)))
            await db.commit()
        await _publish(tenant_id, job_id, {"kind": "book_fail", "error": message})
