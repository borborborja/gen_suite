"""Regression tests for the extraction record endpoints (merge-next/split previously called
_record_out with missing arguments and 500'd on every request)."""
from __future__ import annotations

import io
import uuid

from httpx import AsyncClient
from PIL import Image
from sqlalchemy import select

from app.core import storage
from app.db.rls import set_rls_context
from app.db.session import SessionLocal
from app.models.document import Page
from app.models.job import Job
from app.models.mention import PersonMention
from app.models.record import Record


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _png(color: tuple[int, int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(buf, "PNG")
    return buf.getvalue()


async def _tenant_token(client: AsyncClient) -> str:
    reg = await client.post("/api/auth/register", json={"email": "rec@example.com", "password": "supersecret123"})
    tok = reg.json()["access_token"]
    t = await client.post("/api/tenants", json={"name": "Casa Rec"}, headers=_auth(tok))
    sw = await client.post(f"/api/auth/switch/{t.json()['id']}", headers=_auth(tok))
    return sw.json()["access_token"]


async def _upload_doc(client: AsyncClient, h: dict) -> str:
    await storage.ensure_buckets()
    up = await client.post(
        "/api/documents", headers=h, data={"title": "Libro", "visibility": "private"},
        files=[("files", ("p1.png", _png((200, 0, 0)), "image/png")),
               ("files", ("p2.png", _png((0, 200, 0)), "image/png"))],
    )
    assert up.status_code == 201, up.text
    return up.json()["id"]


async def test_split_record_returns_full_record_out(client: AsyncClient):
    h = _auth(await _tenant_token(client))
    doc_id = await _upload_doc(client, h)

    me = (await client.get("/api/auth/me", headers=h)).json()
    tenant_id = uuid.UUID(me["active_tenant_id"])

    # Seed a spanning record (page 1 → page 2) with one mention, directly in the DB.
    async with SessionLocal() as session:
        await set_rls_context(session, tenant_id=tenant_id)
        pages = list((await session.execute(
            select(Page).where(Page.document_id == uuid.UUID(doc_id)).order_by(Page.page_no)
        )).scalars())
        assert len(pages) == 2
        rec = Record(
            tenant_id=tenant_id, document_id=uuid.UUID(doc_id),
            page_id=pages[0].id, page_end_id=pages[1].id, is_continued=True,
            record_type="baptism", status="extracted", summary="Bautismo de prueba",
            extraction_engine="test", extraction_model="test",
        )
        session.add(rec)
        await session.flush()
        session.add(PersonMention(
            tenant_id=tenant_id, record_id=rec.id, role="principal",
            given="Joan", surname="Vidal", name_raw="Joan Vidal",
        ))
        await session.commit()
        rec_id = str(rec.id)

    r = await client.post(f"/api/extraction/records/{rec_id}/split", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["is_continued"] is False and body["page_end_id"] is None
    # the fixed endpoint returns the record's mentions (it used to 500 before loading them)
    assert [m["role"] for m in body["mentions"]] == ["principal"]

    # listing still shows the (now single-page) record
    listing = await client.get(f"/api/extraction/documents/{doc_id}", headers=h)
    assert listing.status_code == 200 and len(listing.json()) == 1


async def test_job_cancel_sets_terminal_fields(client: AsyncClient):
    h = _auth(await _tenant_token(client))
    doc_id = await _upload_doc(client, h)

    job = await client.post("/api/transcription/jobs", headers=h, json={"document_id": doc_id, "engine": "tesseract"})
    assert job.status_code == 202, job.text
    job_id = job.json()["id"]

    # unified cancel: status + finished_at + default error, from any module's cancel endpoint
    c = await client.post(f"/api/jobs/{job_id}/cancel", headers=h)
    assert c.status_code == 200, c.text
    body = c.json()
    assert body["status"] == "cancelled"
    assert body["finished_at"] is not None
    assert body["error"]

    # idempotent on an already-terminal job
    again = await client.post(f"/api/jobs/{job_id}/cancel", headers=h)
    assert again.status_code == 200 and again.json()["status"] == "cancelled"

    async with SessionLocal() as session:
        row = await session.get(Job, uuid.UUID(job_id))
        # session without tenant context sees nothing (RLS holds for jobs too)
        assert row is None
