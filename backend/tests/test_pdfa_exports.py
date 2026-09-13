"""Disposable PostgreSQL/RLS only; tool, S3 and AI execution are not needed."""
import json
import uuid
from unittest.mock import AsyncMock, patch

import jwt
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.settings import settings


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def _tenant_token(client, email, name):
    response = await client.post("/api/auth/register", json={"email": email, "password": "synthetic-test-password"})
    token = response.json()["access_token"]
    tenant = await client.post("/api/tenants", json={"name": name}, headers=_auth(token))
    switched = await client.post(f"/api/auth/switch/{tenant.json()['id']}", headers=_auth(token))
    return switched.json()["access_token"]


async def fixture_document(client):
    token = await _tenant_token(client, "pdfa@example.com", "PDF/A test")
    tenant = uuid.UUID(jwt.decode(token, options={"verify_signature": False})["tid"])
    doc, export = uuid.uuid4(), uuid.uuid4()
    engine = create_async_engine(settings.admin_database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("""INSERT INTO documents(id,tenant_id,title,doc_type,visibility,storage_bucket,storage_prefix)
                VALUES (:id,:tenant,'Libro sintético','image_set','public','test','test/')"""), {"id": doc, "tenant": tenant})
            await conn.execute(text("""INSERT INTO document_exports(id,tenant_id,document_id,kind,storage_key,checksum,byte_size,manifest)
                VALUES (:id,:tenant,:doc,'pdfa','test/export.pdf','abc',3,CAST(:manifest AS jsonb))"""),
                {"id": export, "tenant": tenant, "doc": doc, "manifest": json.dumps({"pdfa": "validated",
                 "transcriptions": {}, "text_mode": "appendix", "validation": {"status": "validated", "output_sha256": "abc"}})})
        return _auth(token), tenant, doc, export
    finally:
        await engine.dispose()


async def test_pdfa_receipt_is_private_even_when_source_is_public(client):
    owner, tenant, doc, export = await fixture_document(client)
    route = f"/api/documents/{doc}/exports/{export}/validation"
    response = await client.get(route, headers=owner)
    assert response.status_code == 200
    assert response.json()["checksum"] == "abc"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["content-disposition"].endswith('-validation.json"')
    other = _auth(await _tenant_token(client, "pdfa-other@example.com", "Other"))
    for suffix in ("exports", "export-capabilities", f"exports/{export}/validation", f"exports/{export}/content"):
        assert (await client.get(f"/api/documents/{doc}/{suffix}", headers=other)).status_code == 404
    assert (await client.post(f"/api/documents/{doc}/exports", json={"kind": "pdfa"}, headers=other)).status_code == 404


async def test_pdfa_admission_and_durable_dedup_without_tools(client):
    owner, tenant, doc, export = await fixture_document(client)
    route = f"/api/documents/{doc}/exports"
    with patch("app.modules.documents.exports.pdfa_available", AsyncMock(return_value=False)):
        assert (await client.post(route, json={"kind": "pdfa"}, headers=owner)).status_code == 503
    with patch("app.modules.documents.exports.pdfa_available", AsyncMock(return_value=True)):
        first = await client.post(route, json={"kind": "pdfa"}, headers=owner)
        assert first.status_code == 202
    with patch("app.modules.documents.exports.pdfa_available", AsyncMock(return_value=False)):
        second = await client.post(route, json={"kind": "pdfa"}, headers=owner)
        assert second.json()["id"] == first.json()["id"]
    engine = create_async_engine(settings.admin_database_url)
    try:
        async with engine.connect() as conn:
            rows = (await conn.execute(text("SELECT function,payload FROM task_dispatches WHERE tenant_id=:tenant"), {"tenant": tenant})).all()
            assert len(rows) == 1
            assert rows[0].function == "export_document_pdfa"
            assert rows[0].payload["document_id"] == str(doc)
    finally:
        await engine.dispose()


async def test_pdfa_receipt_checksum_mismatch_is_not_certified(client):
    owner, tenant, doc, export = await fixture_document(client)
    engine = create_async_engine(settings.admin_database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("UPDATE document_exports SET checksum='changed' WHERE id=:id"), {"id": export})
    finally:
        await engine.dispose()
    assert (await client.get(f"/api/documents/{doc}/exports/{export}/validation", headers=owner)).status_code == 409
