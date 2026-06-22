from __future__ import annotations

import io
import uuid

from httpx import AsyncClient
from PIL import Image, ImageDraw, ImageFont

from app.core import storage
from app.tasks.transcription_tasks import transcribe_document

_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _text_png(text: str) -> bytes:
    img = Image.new("RGB", (640, 160), "white")
    draw = ImageDraw.Draw(img)
    draw.text((20, 50), text, fill="black", font=ImageFont.truetype(_FONT, 48))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


async def _tenant(client: AsyncClient) -> str:
    reg = await client.post(
        "/api/auth/register", json={"email": "ocr@example.com", "password": "supersecret123"}
    )
    tok = reg.json()["access_token"]
    t = await client.post("/api/tenants", json={"name": "Casa OCR"}, headers=_auth(tok))
    tid = t.json()["id"]
    sw = await client.post(f"/api/auth/switch/{tid}", headers=_auth(tok))
    return sw.json()["access_token"], tid


async def test_transcription_pipeline_with_tesseract(client: AsyncClient):
    await storage.ensure_buckets()
    token, tenant_id = await _tenant(client)
    h = _auth(token)

    up = await client.post(
        "/api/documents",
        headers=h,
        data={"title": "OCR test", "visibility": "private"},
        files=[("files", ("p1.png", _text_png("HELLO WORLD"), "image/png"))],
    )
    doc_id = up.json()["id"]

    # Create the job via the API (enqueues to Redis); then run the worker function directly
    # so the test doesn't depend on a separate ARQ process.
    started = await client.post(
        "/api/transcription/jobs",
        headers=h,
        json={"document_id": doc_id, "engine": "tesseract", "lang": "eng"},
    )
    assert started.status_code == 202, started.text
    job = started.json()
    assert job["status"] == "queued"

    await transcribe_document(
        {},
        job_id=job["id"],
        tenant_id=tenant_id,
        document_id=doc_id,
        override={"engine": "tesseract"},
        options={"lang": "eng", "psm": 6},
    )

    job_after = (await client.get(f"/api/jobs/{job['id']}", headers=h)).json()
    assert job_after["status"] == "completed"
    assert job_after["result"]["done"] == 1

    trans = (await client.get(f"/api/transcription/documents/{doc_id}", headers=h)).json()
    assert len(trans) == 1
    assert trans[0]["status"] == "ok"
    assert "HELLO" in (trans[0]["text"] or "").upper()


async def test_transcription_requires_provider_when_no_engine(client: AsyncClient):
    """With no engine override and no binding, the job fails clearly (no provider configured)."""
    await storage.ensure_buckets()
    token, tenant_id = await _tenant(client)
    h = _auth(token)
    up = await client.post(
        "/api/documents", headers=h,
        data={"title": "no provider", "visibility": "private"},
        files=[("files", ("p.png", _text_png("X"), "image/png"))],
    )
    doc_id = up.json()["id"]
    started = await client.post("/api/transcription/jobs", headers=h, json={"document_id": doc_id})
    job_id = started.json()["id"]
    await transcribe_document(
        {}, job_id=job_id, tenant_id=tenant_id, document_id=doc_id, override={}, options={}
    )
    job_after = (await client.get(f"/api/jobs/{job_id}", headers=h)).json()
    assert job_after["status"] == "error"
    assert "provider" in (job_after["error"] or "").lower()
