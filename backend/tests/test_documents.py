from __future__ import annotations

import io

from httpx import AsyncClient
from PIL import Image

from app.core import storage


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _png(color: tuple[int, int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(buf, "PNG")
    return buf.getvalue()


async def _tenant_token(client: AsyncClient, email: str, tenant: str) -> str:
    reg = await client.post("/api/auth/register", json={"email": email, "password": "supersecret123"})
    tok = reg.json()["access_token"]
    t = await client.post("/api/tenants", json={"name": tenant}, headers=_auth(tok))
    sw = await client.post(f"/api/auth/switch/{t.json()['id']}", headers=_auth(tok))
    return sw.json()["access_token"]


async def test_upload_pages_publish_and_cross_tenant_read(client: AsyncClient):
    await storage.ensure_buckets()
    h = _auth(await _tenant_token(client, "owner@example.com", "Casa A"))

    up = await client.post(
        "/api/documents",
        headers=h,
        data={"title": "Libro de bautismos", "visibility": "private"},
        files=[
            ("files", ("p1.png", _png((200, 0, 0)), "image/png")),
            ("files", ("p2.png", _png((0, 200, 0)), "image/png")),
        ],
    )
    assert up.status_code == 201, up.text
    doc = up.json()
    assert doc["doc_type"] == "image_set" and doc["page_count"] == 2 and doc["visibility"] == "private"
    doc_id = doc["id"]

    pages = (await client.get(f"/api/documents/{doc_id}/pages", headers=h)).json()
    assert len(pages) == 2 and pages[0]["width"] == 8

    content = await client.get(f"/api/documents/{doc_id}/pages/1/content", headers=h)
    assert content.status_code == 200 and content.headers["content-type"] == "image/png"
    assert content.content == _png((200, 0, 0))

    # Publishing requires a rights declaration; once public it's readable cross-tenant.
    pub = await client.post(
        f"/api/documents/{doc_id}/publish", headers=h, json={"rights_declaration": "public_domain"}
    )
    assert pub.status_code == 200 and pub.json()["visibility"] == "public"

    other = _auth(await _tenant_token(client, "other@example.com", "Casa B"))
    # Other tenant sees it in the public library and can read its pages...
    public_list = (await client.get("/api/documents?scope=public", headers=other)).json()
    assert doc_id in {d["id"] for d in public_list}
    cross = await client.get(f"/api/documents/{doc_id}/pages/1/content", headers=other)
    assert cross.status_code == 200
    # ...but cannot delete or unpublish someone else's document.
    assert (await client.delete(f"/api/documents/{doc_id}", headers=other)).status_code == 403

    # A private document of tenant A is invisible to tenant B.
    priv = await client.post(
        "/api/documents", headers=h,
        data={"title": "Privado", "visibility": "private"},
        files=[("files", ("x.png", _png((0, 0, 200)), "image/png"))],
    )
    priv_id = priv.json()["id"]
    assert (await client.get(f"/api/documents/{priv_id}", headers=other)).status_code == 404


async def test_public_upload_without_rights_is_rejected(client: AsyncClient):
    await storage.ensure_buckets()
    h = _auth(await _tenant_token(client, "r@example.com", "Casa R"))
    resp = await client.post(
        "/api/documents", headers=h,
        data={"title": "Sin derechos", "visibility": "public"},
        files=[("files", ("p.png", _png((1, 2, 3)), "image/png"))],
    )
    assert resp.status_code == 400


async def test_jobs_list_empty_and_missing(client: AsyncClient):
    h = _auth(await _tenant_token(client, "j@example.com", "Casa J"))
    assert (await client.get("/api/jobs", headers=h)).json() == []
    missing = await client.get("/api/jobs/00000000-0000-0000-0000-000000000000", headers=h)
    assert missing.status_code == 404
