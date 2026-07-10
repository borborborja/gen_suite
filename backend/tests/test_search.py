from __future__ import annotations

import io
import uuid

from httpx import AsyncClient
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core import storage
from app.db.rls import set_rls_context
from app.db.session import SessionLocal
from app.modules.search import service as search_service
from app.settings import settings
from app.tasks.transcription_tasks import transcribe_document

from .test_transcription import _font


def _auth(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


def _png(word: str) -> bytes:
    img = Image.new("RGB", (640, 160), "white")
    ImageDraw.Draw(img).text((20, 50), word, fill="black", font=_font(48))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


async def _tenant(client: AsyncClient) -> tuple[str, str]:
    reg = await client.post(
        "/api/auth/register", json={"email": "search@example.com", "password": "supersecret123"}
    )
    tok = reg.json()["access_token"]
    tid = (await client.post("/api/tenants", json={"name": "Casa S"}, headers=_auth(tok))).json()["id"]
    sw = await client.post(f"/api/auth/switch/{tid}", headers=_auth(tok))
    return sw.json()["access_token"], tid


async def _upload_and_transcribe(client, h, tenant_id, words: list[str]) -> str:
    files = [("files", (f"p{i}.png", _png(w), "image/png")) for i, w in enumerate(words, 1)]
    doc = (
        await client.post(
            "/api/documents", headers=h, data={"title": "Búsqueda", "visibility": "private"}, files=files
        )
    ).json()
    job = (
        await client.post(
            "/api/transcription/jobs", headers=h,
            json={"document_id": doc["id"], "engine": "tesseract", "lang": "eng"},
        )
    ).json()
    await transcribe_document(
        {}, job_id=job["id"], tenant_id=tenant_id, document_id=doc["id"],
        override={"engine": "tesseract"}, options={"lang": "eng", "psm": 7},
    )
    return doc["id"]


async def test_keyword_search(client: AsyncClient):
    await storage.ensure_buckets()
    token, tenant_id = await _tenant(client)
    h = _auth(token)
    doc_id = await _upload_and_transcribe(client, h, tenant_id, ["HELLO"])

    hits = (await client.get("/api/search", params={"q": "HELLO", "mode": "keyword"}, headers=h)).json()
    assert any(hit["document_id"] == doc_id for hit in hits)
    # No match → empty.
    none = (await client.get("/api/search", params={"q": "zzzznomatch", "mode": "keyword"}, headers=h)).json()
    assert none == []


async def test_vector_search_orders_by_cosine(client: AsyncClient):
    await storage.ensure_buckets()
    token, tenant_id = await _tenant(client)
    h = _auth(token)
    doc_id = await _upload_and_transcribe(client, h, tenant_id, ["ALPHA", "BETA"])

    rows = (await client.get(f"/api/transcription/documents/{doc_id}", headers=h)).json()
    by_page = {r["page_no"]: r["id"] for r in rows}

    # Inject orthogonal unit embeddings: page 1 -> e0, page 2 -> e1.
    def vec(i: int) -> str:
        v = [0.0] * 1024
        v[i] = 1.0
        return "[" + ",".join(repr(x) for x in v) + "]"

    admin = create_async_engine(settings.admin_database_url)
    async with admin.begin() as conn:
        await conn.execute(
            text("UPDATE transcriptions SET embedding = (:v)::vector WHERE id = :id"),
            {"v": vec(0), "id": by_page[1]},
        )
        await conn.execute(
            text("UPDATE transcriptions SET embedding = (:v)::vector WHERE id = :id"),
            {"v": vec(1), "id": by_page[2]},
        )
    await admin.dispose()

    # A query vector closest to page 1's embedding must rank page 1 first.
    qvec = [0.0] * 1024
    qvec[0] = 1.0
    async with SessionLocal() as session:
        await set_rls_context(session, tenant_id=uuid.UUID(tenant_id))
        hits = await search_service.vector_search(session, qvec, "all", uuid.UUID(tenant_id), 5)
    assert hits[0]["page_no"] == 1
    assert hits[0]["score"] > 0.99
