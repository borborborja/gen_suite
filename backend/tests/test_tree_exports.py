"""Synthetic HTTP + real PostgreSQL/RLS/S3. Worker execution is invoked explicitly."""
import hashlib
import io
import json
import uuid
import zipfile
from unittest.mock import patch

import pytest
from sqlalchemy import select, text, update

from app.core import storage
from app.db.rls import set_rls_context
from app.db.session import SessionLocal
from app.models.document import Page
from app.models.job import Job
from app.models.research import Source
from app.models.task_dispatch import TaskDispatch
from app.models.tree_export import TreeExport
from app.modules.tree import export_bundle, exporter
from app.tasks.tree_export_tasks import export_tree

from .test_documents import _png
from .test_gedcom_evidence import setup, upload


async def create(client, headers, kind):
    response = await client.post("/api/tree/exports", headers=headers, json={"kind": kind})
    assert response.status_code == 202, response.text
    return uuid.UUID(response.json()["id"])


async def execute(client, headers, tenant, job_id):
    await export_tree({}, job_id=str(job_id), tenant_id=str(tenant))
    result = (await client.get(f"/api/jobs/{job_id}", headers=headers)).json()
    assert result["status"] == "completed", result
    report = (await client.get(f"/api/tree/exports/{job_id}/audit", headers=headers)).json()
    response = await client.get(f"/api/tree/exports/{job_id}/content", headers=headers,
                                params={"review_token": report["review_token"]})
    assert response.status_code == 200, response.text
    assert hashlib.sha256(response.content).hexdigest() == report["artifact_sha256"]
    return report, response


@pytest.mark.parametrize("kind,version", [("gedcom551", "5.5.1"), ("gedcom7", "7.0")])
async def test_durable_private_snapshot_export_and_audit_do_not_change_with_tree(client, kind, version):
    headers, tenant = await setup(client, kind)
    assert (await upload(client, headers)).status_code == 200
    job_id = await create(client, headers, kind)
    assert await create(client, headers, kind) == job_id
    assert (await client.post("/api/tree/exports", headers=headers, json={"kind": "gedzip"})).status_code == 409
    async with SessionLocal() as db:
        await set_rls_context(db, tenant_id=tenant)
        dispatch = await db.get(TaskDispatch, job_id)
        assert dispatch.function == "export_tree" and dispatch.status == "pending"
        assert await db.get(TreeExport, job_id) is None
    report, response = await execute(client, headers, tenant, job_id)
    assert report["snapshot"]["isolation"] == "repeatable_read"
    assert f"2 VERS {version}\n" in response.text
    assert report["counts"]["citations_exported"] == 4
    assert response.headers["cache-control"] == "private, no-store"
    if kind == "gedcom7":
        assert "2 TYPE BIRTH\n" in response.text and "2 PEDI ADOPTED\n" in response.text
    await client.post("/api/tree/persons", headers=headers, json={"given": "Posterior"})
    assert (await client.get(f"/api/tree/exports/{job_id}/audit", headers=headers)).json() == report
    _, repeated = await execute(client, headers, tenant, job_id)
    assert repeated.content == response.content
    path = f"/api/tree/exports/{job_id}/content"
    assert (await client.get(path, headers=headers)).status_code == 422
    assert (await client.get(path, headers=headers, params={"review_token": "f" * 64})).status_code == 409
    part = await client.get(path, headers={**headers, "Range": "bytes=0-9"}, params={"review_token": report["review_token"]})
    assert part.status_code == 206 and part.content == response.content[:10]
    assert part.headers["content-range"] == f"bytes 0-9/{len(response.content)}"
    assert (await client.get(path, headers={**headers, "Range": "bytes=999999999-"}, params={"review_token": report["review_token"]})).status_code == 416
    other, _ = await setup(client, kind + "-other")
    assert (await client.get("/api/tree/exports", headers=other)).json() == []
    assert (await client.get(f"/api/tree/exports/{job_id}/audit", headers=other)).status_code == 404
    assert (await client.get(path, headers=other, params={"review_token": report["review_token"]})).status_code == 404
    assert (await client.get(path, params={"review_token": report["review_token"]})).status_code == 401


async def test_gedzip_originals_sources_manifest_and_no_cross_tenant_media(client):
    await storage.ensure_buckets()
    headers, tenant = await setup(client, "zip")
    assert (await upload(client, headers)).status_code == 200
    images = [_png((2, 3, 4)), _png((4, 5, 6))]
    response = await client.post("/api/documents", headers=headers, data={"title": "Llibre original", "visibility": "private"},
        files=[("files", (f"p{n}.png", payload, "image/png")) for n, payload in enumerate(images)])
    assert response.status_code == 201, response.text
    doc_id = uuid.UUID(response.json()["id"])
    async with SessionLocal() as db:
        await set_rls_context(db, tenant_id=tenant)
        source = await db.scalar(select(Source).where(Source.title.like("Llibre%")))
        source.primary_document_id = doc_id
        await db.commit()
    other, _ = await setup(client, "public-media")
    foreign = await client.post("/api/documents", headers=other,
        data={"title": "Foreign public", "visibility": "public", "rights_declaration": "public_domain"},
        files=[("files", ("foreign.png", _png((9, 9, 9)), "image/png"))])
    assert foreign.status_code == 201
    job_id = await create(client, headers, "gedzip")
    report, result = await execute(client, headers, tenant, job_id)
    assert report["media_files"] == 2 and report["media_documents"] == 1 and not report["is_backup"]
    assert report["excluded_records"]["documents"] == 0
    with zipfile.ZipFile(io.BytesIO(result.content)) as archive:
        data = archive.read("gedcom.ged").decode()
        assert "Foreign public" not in data and foreign.json()["id"] not in data
        assert "1 OBJE @M1@" in data and "0 @M1@ OBJE" in data
        assert "2 FORM image/png" in data
        manifest = json.loads(archive.read("gen-suite/media-manifest.json"))
        assert len(manifest) == 2
        for entry, payload in zip(manifest, images):
            assert archive.read(entry["path"]) == payload
            assert f"1 FILE {entry['path']}\n" in data
        assert hashlib.sha256(archive.read("gedcom.ged")).hexdigest() == report["sha256"]


async def test_cancellation_and_corruption_never_publish_an_artifact(client):
    await storage.ensure_buckets()
    headers, tenant = await setup(client, "cancel")
    job_id = await create(client, headers, "gedcom7")
    await client.post(f"/api/jobs/{job_id}/cancel", headers=headers)
    await export_tree({}, job_id=job_id, tenant_id=tenant)
    assert (await client.get(f"/api/tree/exports/{job_id}/audit", headers=headers)).status_code == 404
    response = await client.post("/api/documents", headers=headers, data={"title": "Corrupto", "visibility": "private"},
        files=[("files", ("original.png", _png((1, 1, 1)), "image/png"))])
    assert response.status_code == 201
    async with SessionLocal() as db:
        await set_rls_context(db, tenant_id=tenant)
        await db.execute(update(Page).values(checksum="0" * 64))  # Only this synthetic tenant.
        await db.commit()
    job_id = await create(client, headers, "gedzip")
    await export_tree({}, job_id=job_id, tenant_id=tenant)
    job = (await client.get(f"/api/jobs/{job_id}", headers=headers)).json()
    assert job["status"] == "error" and "checksum" in job["error"]
    assert (await client.get(f"/api/tree/exports/{job_id}/audit", headers=headers)).status_code == 404


async def test_snapshot_sees_one_database_version_despite_concurrent_edit(client):
    headers, tenant = await setup(client, "snapshot")
    assert (await upload(client, headers)).status_code == 200
    original = exporter.build_graph
    async def concurrent_edit(db, tenant_id, **options):
        # Admission already read the snapshot. This separate committed request
        # must not appear in the remaining genealogy/report queries.
        result = await client.post("/api/tree/persons", headers=headers, json={"given": "Concurrente"})
        assert result.status_code == 200
        return await original(db, tenant_id, **options)
    job_id = await create(client, headers, "gedcom7")
    with patch.object(exporter, "build_graph", concurrent_edit):
        report, response = await execute(client, headers, tenant, job_id)
    assert report["counts"]["persons"] == 2 and "Concurrente" not in response.text
    assert (await client.get("/api/tree/stats", headers=headers)).json()["persons"] == 3


async def test_cancel_at_upload_publication_boundary_keeps_orphan_accounted_not_published(client):
    headers, tenant = await setup(client, "cancel-upload")
    await storage.ensure_buckets()
    job_id = await create(client, headers, "gedcom7")
    original = storage.upload_file
    async def cancel_after_upload(*args, **kwargs):
        await original(*args, **kwargs)
        assert (await client.post(f"/api/jobs/{job_id}/cancel", headers=headers)).status_code == 200
    with patch.object(storage, "upload_file", cancel_after_upload):
        await export_tree({}, job_id=job_id, tenant_id=tenant)
    async with SessionLocal() as db:
        await set_rls_context(db, tenant_id=tenant)
        assert await db.get(TreeExport, job_id) is None
        assert (await db.get(Job, job_id)).status == "cancelled"
        rows = (await db.execute(text("SELECT state, reserved_bytes FROM storage_writes WHERE job_id=:job"), {"job": job_id})).all()
        assert rows and rows[0][0] == "succeeded" and rows[0][1] > 0


async def test_tree_export_is_in_full_recovery_reference_manifest(client, tmp_path):
    from app.recovery import database
    from app.settings import settings
    await storage.ensure_buckets()
    headers, tenant = await setup(client, "recovery-export")
    job_id = await create(client, headers, "gedcom7")
    report, _ = await execute(client, headers, tenant, job_id)
    connection = await database.connect(settings)
    try:
        async with connection.transaction(isolation="repeatable_read", readonly=True):
            assert await database.save_references(connection, {"private": settings.minio_bucket_private,
                "public": settings.minio_bucket_public}, tmp_path) == 1
        entries = [json.loads(line) for line in (tmp_path / "references.jsonl").read_text().splitlines()]
        assert entries[0]["kind"] == "tree_export" and entries[0]["role"] == "private"
        assert entries[0]["sha256"] == report["artifact_sha256"]
        assert entries[0]["size"] == report["artifact_byte_size"]
    finally:
        await connection.close()


async def test_export_limits_are_explicit_and_do_not_upload_partial_artifacts(client):
    from app.settings import settings
    headers, tenant = await setup(client, "limits")
    assert (await upload(client, headers)).status_code == 200
    job_id = await create(client, headers, "gedcom7")
    with patch.object(settings, "tree_export_max_rows", 1):
        await export_tree({}, job_id=job_id, tenant_id=tenant)
    job = (await client.get(f"/api/jobs/{job_id}", headers=headers)).json()
    assert job["status"] == "error" and "límite" in job["error"]
    assert (await client.get(f"/api/tree/exports/{job_id}/audit", headers=headers)).status_code == 404


async def test_committed_outbox_executes_export_in_real_arq_worker(client):
    from arq.worker import create_worker
    from app.tasks import dispatch_tasks
    from app.tasks.worker import WorkerSettings
    await storage.ensure_buckets()
    headers, tenant = await setup(client, "arq")
    assert (await upload(client, headers)).status_code == 200
    job_id = await create(client, headers, "gedcom7")
    queue_name = f"gensuite-test-tree-export:{uuid.uuid4()}"
    # Isolate Redis delivery from stale synthetic work from earlier test cases.
    # The production outbox transaction and registered function still execute.
    with patch.object(dispatch_tasks, "queue_options", return_value={"_queue_name": queue_name}):
        await dispatch_tasks.dispatch_pending_tasks({})
    functions = [function for function in WorkerSettings.functions if function.__name__ == "export_tree"]
    assert functions == [export_tree]
    worker = create_worker(WorkerSettings, functions=functions, cron_jobs=[], on_startup=None, on_shutdown=None,
        queue_name=queue_name, burst=True, max_jobs=1, handle_signals=False, poll_delay=.1)
    try:
        await worker.async_run()
    finally:
        await worker.close()
    result = (await client.get(f"/api/jobs/{job_id}", headers=headers)).json()
    assert result["status"] == "completed", result
    async with SessionLocal() as db:
        await set_rls_context(db, tenant_id=tenant)
        assert (await db.get(TaskDispatch, job_id)).status == "dispatched"
        assert (await db.get(TreeExport, job_id)).report["counts"]["persons"] == 2


async def test_gedzip_keeps_the_pdf_original_before_rasterization(client):
    from pypdf import PdfWriter
    await storage.ensure_buckets()
    headers, tenant = await setup(client, "pdf-original")
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=150)
    buffer = io.BytesIO()
    writer.write(buffer)
    original = buffer.getvalue()
    response = await client.post("/api/documents", headers=headers,
        data={"title": "PDF sin rasterizar", "visibility": "private"},
        files=[("files", ("original.pdf", original, "application/pdf"))])
    assert response.status_code == 201, response.text
    async with SessionLocal() as db:
        await set_rls_context(db, tenant_id=tenant)
        assert list((await db.scalars(select(Page))).all()) == []
    job_id = await create(client, headers, "gedzip")
    report, result = await execute(client, headers, tenant, job_id)
    assert report["media_files"] == 1 and report["counts"]["media_sources_added"] == 1
    with zipfile.ZipFile(io.BytesIO(result.content)) as archive:
        files = json.loads(archive.read("gen-suite/media-manifest.json"))
        assert files[0]["mime"] == "application/pdf"
        assert archive.read(files[0]["path"]) == original
        assert "0 @D1@ SOUR" in archive.read("gedcom.ged").decode()
