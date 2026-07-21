"""Manual citations: person/event → library document page, CRUD + tenant isolation."""
from __future__ import annotations

import io

from httpx import AsyncClient
from PIL import Image

from app.core import storage

from .test_documents import _tenant_token as _doc_tenant_token
from .test_tree import _auth
from .test_tree_manager import _person


def _png(color=(120, 40, 20)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(buf, "PNG")
    return buf.getvalue()


async def _upload_doc(client: AsyncClient, h: dict, title: str, pages: int = 2) -> str:
    await storage.ensure_buckets()
    up = await client.post(
        "/api/documents", headers=h, data={"title": title, "visibility": "private"},
        files=[("files", (f"p{i}.png", _png((10 * i, 0, 0)), "image/png")) for i in range(1, pages + 1)],
    )
    assert up.status_code == 201, up.text
    return up.json()["id"]


async def test_manual_citation_crud(client: AsyncClient):
    h = _auth(await _doc_tenant_token(client, "cit@example.com", "Casa"))
    juan = await _person(client, h, "Juan", "Vega", "M")
    doc_id = await _upload_doc(client, h, "Bautismos 1850-1900")

    r = await client.post("/api/tree/citations", headers=h, json={
        "target_type": "person", "target_id": juan, "document_id": doc_id,
        "page_no": 2, "note": "Partida de bautismo, folio 12v"})
    assert r.status_code == 200, r.text
    cit_id = r.json()["id"]

    cites = (await client.get(f"/api/tree/persons/{juan}/citations", headers=h)).json()
    assert len(cites) == 1
    c = cites[0]
    assert c["id"] == cit_id and c["target_type"] == "person"
    assert c["document_id"] == doc_id and c["document_title"] == "Bautismos 1850-1900"
    assert c["page_no"] == 2 and c["note"] == "Partida de bautismo, folio 12v"

    # cita de un hecho, solo con nota
    ev = await client.post(f"/api/tree/persons/{juan}/events",
                           json={"type": "birth", "date_raw": "1870"}, headers=h)
    ev_id = ev.json()["id"]
    r = await client.post("/api/tree/citations", headers=h, json={
        "target_type": "event", "target_id": ev_id, "note": "Según el padrón de 1880"})
    assert r.status_code == 200
    assert len((await client.get(f"/api/tree/persons/{juan}/citations", headers=h)).json()) == 2

    # patch: mover la cita a otra página y cambiar nota
    r = await client.patch(f"/api/tree/citations/{cit_id}", headers=h,
                           json={"document_id": doc_id, "page_no": 1, "note": "folio 1r"})
    assert r.status_code == 200
    cites = (await client.get(f"/api/tree/persons/{juan}/citations", headers=h)).json()
    byid = {c["id"]: c for c in cites}
    assert byid[cit_id]["page_no"] == 1 and byid[cit_id]["note"] == "folio 1r"

    # delete
    assert (await client.delete(f"/api/tree/citations/{cit_id}", headers=h)).status_code == 200
    assert len((await client.get(f"/api/tree/persons/{juan}/citations", headers=h)).json()) == 1


async def test_citation_validation_and_isolation(client: AsyncClient):
    h = _auth(await _doc_tenant_token(client, "a@example.com", "Casa A"))
    juan = await _person(client, h, "Juan", "Vega", "M")
    doc_id = await _upload_doc(client, h, "Libro", pages=1)

    # ni documento ni nota → 400; target inexistente → 404; página fuera de rango → 404
    assert (await client.post("/api/tree/citations", headers=h, json={
        "target_type": "person", "target_id": juan})).status_code == 400
    assert (await client.post("/api/tree/citations", headers=h, json={
        "target_type": "person", "target_id": doc_id, "note": "x"})).status_code == 404
    assert (await client.post("/api/tree/citations", headers=h, json={
        "target_type": "person", "target_id": juan, "document_id": doc_id,
        "page_no": 99})).status_code == 404

    r = await client.post("/api/tree/citations", headers=h, json={
        "target_type": "person", "target_id": juan, "document_id": doc_id, "page_no": 1})
    cit_id = r.json()["id"]

    # otro tenant no puede tocarla (RLS): patch/delete → 404
    h2 = _auth(await _doc_tenant_token(client, "b@example.com", "Casa B"))
    assert (await client.patch(f"/api/tree/citations/{cit_id}", headers=h2,
                               json={"note": "hola"})).status_code == 404
    assert (await client.delete(f"/api/tree/citations/{cit_id}", headers=h2)).status_code == 404
