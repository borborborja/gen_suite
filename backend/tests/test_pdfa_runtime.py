"""Opt-in complete pipeline: real PostgreSQL, Redis/ARQ, S3 and PDF/A binaries.

Synthetic scans only. Requires the optional PDF/A image (or equivalent isolated
tools), not the ordinary backend. Missing dependencies fail when explicitly enabled.
"""
import asyncio
import hashlib
import os
import uuid

import fitz
import pytest
from arq.worker import create_worker

from app.core import storage
from app.core.security import decode_token
from app.db.rls import set_rls_context
from app.db.session import SessionLocal
from app.models.transcription import Transcription
from app.settings import settings
from app.tasks.dispatch_tasks import dispatch_pending_tasks
from app.tasks.worker import PdfaWorkerSettings
from .test_documents import _auth, _png, _tenant_token

pytestmark = pytest.mark.skipif(os.environ.get("GEN_SUITE_RUN_PDFA_INTEGRATION") != "yes",
    reason="Opt-in: disposable PG/Redis/S3 and actual PDF/A tools required")


async def test_pdfa_http_outbox_worker_storage_receipt_and_revision(client):
    assert settings.minio_bucket_private.startswith(("gensuite-ci-", "gensuite-test-"))
    assert settings.minio_bucket_public.startswith(("gensuite-ci-", "gensuite-test-"))
    await storage.ensure_buckets()
    token = await _tenant_token(client, "pdfa-runtime@example.com", "PDF/A runtime")
    tid = uuid.UUID(decode_token(token, "access")["tid"])
    headers = _auth(token)
    originals = [_png((200, 0, 0)), _png((0, 200, 0))]
    response = await client.post("/api/documents", headers=headers,
        data={"title": "Libro sintético", "visibility": "private"},
        files=[("files", (f"page-{index}.png", data, "image/png")) for index, data in enumerate(originals)])
    assert response.status_code == 201, response.text
    doc_id = response.json()["id"]
    async with SessionLocal() as db:
        await set_rls_context(db, tenant_id=tid)
        transcription = Transcription(tenant_id=tid, document_id=uuid.UUID(doc_id), page_no=1,
            engine="manual", text="Parròquia de Sant Martí. Lluïsa. FINAL_VERIFICABLE", status="corrected")
        db.add(transcription)
        await db.commit()  # fixture session ends; never commits an HTTP request
        transcription_id = str(transcription.id)

    worker = create_worker(PdfaWorkerSettings, handle_signals=False, poll_delay=.1)
    running = asyncio.create_task(worker.async_run())
    try:
        async with asyncio.timeout(60):
            while True:
                if running.done():
                    await running
                    raise AssertionError("Worker exited before readiness")
                capabilities = await client.get(f"/api/documents/{doc_id}/export-capabilities", headers=headers)
                if capabilities.json()["pdfa_available"]:
                    break
                await asyncio.sleep(.2)
        first = await client.post(f"/api/documents/{doc_id}/exports", headers=headers, json={"kind": "pdfa"})
        assert first.status_code == 202, first.text
        second = await client.post(f"/api/documents/{doc_id}/exports", headers=headers, json={"kind": "pdfa"})
        assert second.json()["id"] == first.json()["id"]
        await dispatch_pending_tasks({})  # committed outbox → real Redis queue
        async with asyncio.timeout(120):
            while True:
                job = (await client.get(f"/api/jobs/{first.json()['id']}", headers=headers)).json()
                if job["status"] in {"completed", "error", "cancelled"}:
                    break
                await asyncio.sleep(.2)
        assert job["status"] == "completed", job
        exports = (await client.get(f"/api/documents/{doc_id}/exports", headers=headers)).json()
        assert len(exports) == 1 and exports[0]["pdfa"] == "validated" and not exports[0]["stale"]
        export = exports[0]
        route = f"/api/documents/{doc_id}/exports/{export['id']}"
        content = await client.get(route + "/content", headers=headers)
        assert content.status_code == 200 and content.headers["content-type"] == "application/pdf"
        assert hashlib.sha256(content.content).hexdigest() == export["checksum"]
        ranged = await client.get(route + "/content", headers={**headers, "Range": "bytes=0-15"})
        assert ranged.status_code == 206 and ranged.content == content.content[:16]
        receipt = (await client.get(route + "/validation", headers=headers)).json()
        proof = receipt["validation"]
        assert proof["output_sha256"] == export["checksum"]
        assert hashlib.sha256(proof["report_xml"].encode()).hexdigest() == proof["report_sha256"]
        assert proof["content_check"]["pages"] == 4
        assert proof["ocr_performed"] is False
        with fitz.open(stream=content.content, filetype="pdf") as pdf:
            assert len(pdf) == 4
            assert pdf[0].get_pixmap().pixel(1, 1)[0] > 190
            assert pdf[1].get_pixmap().pixel(1, 1)[1] > 190
            assert "Lluïsa" in "".join(page.get_text() for page in pdf)
        for number, original in enumerate(originals, 1):
            source = await client.get(f"/api/documents/{doc_id}/pages/{number}/content", headers=headers)
            assert source.content == original
        revised = await client.patch(f"/api/transcription/{transcription_id}", headers=headers,
            json={"text": "Corrección posterior que no debe sustituir el PDF conservado"})
        assert revised.status_code == 200, revised.text
        assert (await client.get(f"/api/documents/{doc_id}/exports", headers=headers)).json()[0]["stale"]
        assert (await client.get(route + "/content", headers=headers)).content == content.content
        assert (await client.get(route + "/validation", headers=headers)).json() == receipt
    finally:
        await worker.close()
        await asyncio.gather(running, return_exceptions=True)
