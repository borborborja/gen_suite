"""Synthetic HTTP + real isolated PostgreSQL/RLS/S3; no remote documents or AI."""
import hashlib
import io
import uuid
from unittest.mock import patch
import zipfile

import pytest
from sqlalchemy import func, select, update

from app.core import storage
from app.db.rls import set_rls_context
from app.db.session import SessionLocal
from app.models.citation import Citation
from app.models.document import Document, Page
from app.models.job import Job
from app.models.person import Person
from app.models.research import Source
from app.models.task_dispatch import TaskDispatch
from app.models.tree_import import SourceDocument, TreeImport
from app.modules.tree import export_bundle, gedcom7, gedcom
from app.tasks.tree_import_tasks import prepare_tree_import, apply_tree_import

from .test_documents import _png
from .test_gedcom_evidence import RICH, setup


def fixture(kind):
    parsed, _ = gedcom.parse(RICH)
    parsed.insert(1, gedcom.Node("SUBM", xref="@U1@", children=[gedcom.Node("NAME", "Synthetic")]))
    roots, _ = gedcom7.convert(parsed)
    if kind == "gedzip":
        roots[0].children.append(gedcom.Node("NOTE", "Synthetic"))
        source = next(n for n in roots if n.xref == "@BOOK@")
        for number in (1, 2):
            ref = f"@M{number}@"
            source.children.append(gedcom.Node("OBJE", ref, is_pointer=True))
            roots.insert(-1, gedcom.Node("OBJE", xref=ref, children=[gedcom.Node("FILE", f"p{number}.png", children=[gedcom.Node("FORM", "image/png")])]))
    content = gedcom7.serialize(roots).encode()
    if kind == "gedcom7": return content
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("gedcom.ged", content)
        for number in (1, 2): archive.writestr(f"p{number}.png", _png((number, 3, 4)))
    return stream.getvalue()


async def upload(client, headers, kind="gedcom7", payload=None, client_id=None):
    response = await client.post("/api/tree/imports", headers={**headers, "Content-Type": "application/octet-stream"},
        params={"kind": kind, "filename": "original.gdz" if kind == "gedzip" else "original.ged", "client_id": str(client_id or uuid.uuid4())},
        content=payload if payload is not None else fixture(kind))
    assert response.status_code == 202, response.text
    return uuid.UUID(response.json()["id"])


async def review(client, headers, tenant, iid):
    await prepare_tree_import({}, job_id=iid, tenant_id=tenant)
    job = (await client.get(f"/api/jobs/{iid}", headers=headers)).json()
    assert job["status"] == "completed", job
    return (await client.get(f"/api/tree/imports/{iid}/audit", headers=headers)).json()


async def apply(client, headers, tenant, iid, report, *, run=True, retry=False):
    response = await client.post(f"/api/tree/imports/{iid}/apply", headers=headers,
        json={"review_token": report["review_token"], "acknowledge_append": True, "retry_failed": retry})
    assert response.status_code == 202, response.text
    jid = uuid.UUID(response.json()["apply_job_id"])
    if run:
        await apply_tree_import({}, job_id=jid, tenant_id=tenant, import_id=iid)
        job = (await client.get(f"/api/jobs/{jid}", headers=headers)).json()
        assert job["status"] == "completed", job
    return jid


@pytest.mark.parametrize("kind", ["gedcom7", "gedzip"])
async def test_review_then_atomic_private_append_with_sources_and_originals(client, kind):
    await storage.ensure_buckets()
    headers, tid = await setup(client, "import-" + kind)
    original = fixture(kind)
    intent = uuid.uuid4()
    iid = await upload(client, headers, kind, original, intent)
    assert await upload(client, headers, kind, original, intent) == iid
    assert (await client.get(f"/api/tree/imports/{iid}/audit", headers=headers)).status_code == 409
    report = await review(client, headers, tid, iid)
    assert report["input_sha256"] == hashlib.sha256(original).hexdigest()
    async with SessionLocal() as db:
        await set_rls_context(db, tenant_id=tid)
        assert await db.scalar(select(func.count()).select_from(Person)) == 0
        assert await db.scalar(select(func.count()).select_from(Document)) == 0
        assert (await db.get(TaskDispatch, iid)).function == "prepare_tree_import"
    body = {"review_token": "0" * 64, "acknowledge_append": True}
    assert (await client.post(f"/api/tree/imports/{iid}/apply", headers=headers, json=body)).status_code == 409
    body.update(review_token=report["review_token"], acknowledge_append=False)
    assert (await client.post(f"/api/tree/imports/{iid}/apply", headers=headers, json=body)).status_code == 422
    jid = await apply(client, headers, tid, iid, report)
    assert await apply(client, headers, tid, iid, report) == jid
    async with SessionLocal() as db:
        await set_rls_context(db, tenant_id=tid)
        persons = (await db.scalars(select(Person))).all()
        assert len(persons) == 2 and {p.privacy_status for p in persons} == {"private"}
        assert await db.scalar(select(func.count()).select_from(Citation)) == 4
        assert await db.scalar(select(func.count()).select_from(Source)) == 2
        links = (await db.scalars(select(SourceDocument))).all()
        assert len(links) == (2 if kind == "gedzip" else 0)
        if kind == "gedzip":
            pages = (await db.scalars(select(Page).order_by(Page.original_filename))).all()
            assert len(pages) == 2
            for number, page in enumerate(pages, 1):
                obj = await storage.open_object(storage.bucket_for("private"), page.storage_key)
                try: assert obj["Body"].read() == _png((number, 3, 4))
                finally: obj["Body"].close()
                assert page.checksum == hashlib.sha256(_png((number, 3, 4))).hexdigest()
            evidence = await export_bundle.snapshot(db, tid, "gedzip")
            assert len(evidence[2]) == 2
            assert evidence[0].count("1 OBJE @M") == 2
    assert (await client.get(f"/api/tree/imports/{iid}/original", headers=headers)).content == original
    sources = (await client.get("/api/research/sources", headers=headers)).json()
    assert sum(len(source["documents"]) for source in sources) == (2 if kind == "gedzip" else 0)
    chunk = await client.get(f"/api/tree/imports/{iid}/original", headers={**headers, "Range": "bytes=0-9"})
    assert chunk.status_code == 206 and chunk.content == original[:10]
    other, _ = await setup(client, "import-other-" + kind)
    assert (await client.get("/api/tree/imports", headers=other)).json() == []
    for suffix in ("audit", "original"):
        assert (await client.get(f"/api/tree/imports/{iid}/{suffix}", headers=other)).status_code == 404
    assert (await client.post(f"/api/tree/imports/{iid}/apply", headers=other, json={**body, "acknowledge_append": True})).status_code == 404


async def test_invalid_file_and_cancelled_preparation_leave_only_original(client):
    headers, tid = await setup(client, "import-invalid")
    iid = await upload(client, headers, payload=b"malformed")
    await prepare_tree_import({}, job_id=iid, tenant_id=tid)
    assert (await client.get(f"/api/jobs/{iid}", headers=headers)).json()["status"] == "error"
    assert (await client.get(f"/api/tree/imports/{iid}/original", headers=headers)).content == b"malformed"
    canceled = await upload(client, headers)
    await client.post(f"/api/jobs/{canceled}/cancel", headers=headers)
    await prepare_tree_import({}, job_id=canceled, tenant_id=tid)
    assert (await client.get(f"/api/tree/imports/{canceled}/audit", headers=headers)).status_code == 409
    async with SessionLocal() as db:
        await set_rls_context(db, tenant_id=tid)
        assert await db.scalar(select(func.count()).select_from(Person)) == 0


async def test_cancel_after_media_upload_keeps_charged_orphans_not_partial_tree_then_explicit_retry(client):
    from app.models.storage_quota import StorageWrite
    headers, tid = await setup(client, "import-cancel")
    iid = await upload(client, headers, "gedzip")
    report = await review(client, headers, tid, iid)
    jid = await apply(client, headers, tid, iid, report, run=False)
    original_upload = storage.upload_file
    async def cancel_after(*args, **kwargs):
        await original_upload(*args, **kwargs)
        await client.post(f"/api/jobs/{jid}/cancel", headers=headers)
    with patch.object(storage, "upload_file", cancel_after):
        await apply_tree_import({}, job_id=jid, tenant_id=tid, import_id=iid)
    async with SessionLocal() as db:
        await set_rls_context(db, tenant_id=tid)
        assert await db.scalar(select(func.count()).select_from(Person)) == 0
        assert await db.scalar(select(func.count()).select_from(Document)) == 0
        writes = (await db.scalars(select(StorageWrite).where(StorageWrite.job_id == jid))).all()
        assert writes and any(w.state == "succeeded" for w in writes)
    response = await client.post(f"/api/tree/imports/{iid}/apply", headers=headers,
        json={"review_token": report["review_token"], "acknowledge_append": True})
    assert response.status_code == 409
    assert await apply(client, headers, tid, iid, report, retry=True) != jid


async def test_changed_original_cannot_be_applied(client):
    headers, tid = await setup(client, "import-corrupt")
    iid = await upload(client, headers)
    report = await review(client, headers, tid, iid)
    async with SessionLocal() as db:
        await set_rls_context(db, tenant_id=tid)
        row = await db.get(TreeImport, iid)
        await storage.put_object(storage.bucket_for("private"), row.storage_key, b"changed", "application/octet-stream")
    jid = await apply(client, headers, tid, iid, report, run=False)
    await apply_tree_import({}, job_id=jid, tenant_id=tid, import_id=iid)
    assert (await client.get(f"/api/jobs/{jid}", headers=headers)).json()["status"] == "error"
    async with SessionLocal() as db:
        await set_rls_context(db, tenant_id=tid)
        assert await db.scalar(select(func.count()).select_from(Person)) == 0


async def test_pdf_is_preserved_and_only_rasterization_is_scheduled(client):
    from pypdf import PdfWriter
    headers, tid = await setup(client, "import-pdf")
    pdf = io.BytesIO(); writer = PdfWriter(); writer.add_blank_page(width=72, height=72); writer.write(pdf)
    payload = b"0 HEAD\n1 GEDC\n2 VERS 7.0\n0 @S@ SOUR\n1 TITL Book\n1 OBJE @M@\n0 @M@ OBJE\n1 FILE book.pdf\n2 FORM application/pdf\n0 TRLR\n"
    bundle = io.BytesIO()
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("gedcom.ged", payload); archive.writestr("book.pdf", pdf.getvalue())
    iid = await upload(client, headers, "gedzip", bundle.getvalue())
    report = await review(client, headers, tid, iid)
    assert report["media"]["books"][0]["files"][0]["page_count"] == 1
    await apply(client, headers, tid, iid, report)
    async with SessionLocal() as db:
        await set_rls_context(db, tenant_id=tid)
        doc = await db.scalar(select(Document))
        assert doc.doc_type == "pdf" and doc.page_count == 0
        assert doc.fingerprint == hashlib.sha256(pdf.getvalue()).hexdigest()
        obj = await storage.open_object(doc.storage_bucket, doc.storage_prefix + "original.pdf")
        try: assert obj["Body"].read() == pdf.getvalue()
        finally: obj["Body"].close()
        functions = set((await db.scalars(select(TaskDispatch.function))).all())
        assert functions == {"prepare_tree_import", "apply_tree_import", "rasterize_document"}


async def test_upload_auth_limits_idempotency_and_recovery_reference(client, tmp_path):
    import json
    from app.recovery import database
    from app.settings import settings
    headers, tid = await setup(client, "import-limits")
    params = {"kind": "gedcom7", "filename": "original.ged", "client_id": str(uuid.uuid4())}
    assert (await client.post("/api/tree/imports", params=params, content=b"x")).status_code == 401
    assert (await client.post("/api/tree/imports", headers=headers, params={**params, "filename": "../x"}, content=b"x")).status_code == 422
    with patch.object(settings, "tree_import_max_bytes", 3):
        assert (await client.post("/api/tree/imports", headers=headers, params=params, content=b"oversized")).status_code == 413
    iid = await upload(client, headers, client_id=uuid.UUID(params["client_id"]))
    assert (await client.post("/api/tree/imports", headers=headers, params=params, content=b"different")).status_code == 409
    connection = await database.connect(settings)
    try:
        async with connection.transaction():
            await database.save_references(connection, {"private": settings.minio_bucket_private, "public": settings.minio_bucket_public}, tmp_path)
    finally: await connection.close()
    entries = [json.loads(line) for line in (tmp_path / "references.jsonl").read_text().splitlines()]
    imported = [row for row in entries if row["kind"] == "tree_import"]
    assert len(imported) == 1 and f"/tree-imports/{iid}/" in imported[0]["key"]
    assert imported[0]["sha256"] == hashlib.sha256(fixture("gedcom7")).hexdigest()


async def test_real_arq_outbox_prepares_and_applies_reviewed_import(client):
    from arq.worker import create_worker
    from app.tasks import dispatch_tasks
    from app.tasks.worker import WorkerSettings
    headers, tid = await setup(client, "arq-import")
    iid = await upload(client, headers)
    async def deliver(function_name):
        name = f"gensuite-test-tree-import:{uuid.uuid4()}"
        with patch.object(dispatch_tasks, "queue_options", return_value={"_queue_name": name}):
            await dispatch_tasks.dispatch_pending_tasks({})
        functions = [function for function in WorkerSettings.functions if function.__name__ == function_name]
        assert len(functions) == 1
        worker = create_worker(WorkerSettings, functions=functions, cron_jobs=[], on_startup=None, on_shutdown=None,
            queue_name=name, burst=True, max_jobs=1, handle_signals=False, poll_delay=.1)
        try: await worker.async_run()
        finally: await worker.close()
    await deliver("prepare_tree_import")
    report = (await client.get(f"/api/tree/imports/{iid}/audit", headers=headers)).json()
    jid = await apply(client, headers, tid, iid, report, run=False)
    await deliver("apply_tree_import")
    result = (await client.get(f"/api/jobs/{jid}", headers=headers)).json()
    assert result["status"] == "completed", result
    assert result["result"]["individuals"] == 2
