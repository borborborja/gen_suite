"""Integration: disposable PostgreSQL/RLS; small in-memory object storage, no paid calls."""
import hashlib
import io
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from app.core import storage
from app.models.ingest import IngestSession
from app.settings import settings
from app.tasks.ingestion_tasks import ingest_collection
from .test_documents import _auth, _png, _tenant_token


async def test_upload_resume_publish_and_retry_keep_exact_originals(client, monkeypatch):
    objects = {}
    async def put(bucket, key, data, content_type):
        objects[(bucket, key)] = data
    async def upload(bucket, key, path, content_type):
        objects[(bucket, key)] = Path(path).read_bytes()
    async def open_object(bucket, key):
        return {"Body": io.BytesIO(objects[(bucket, key)])}
    async def get(bucket, key):
        return objects[(bucket, key)], "image/png"
    monkeypatch.setattr(storage, "put_object", put)
    monkeypatch.setattr(storage, "upload_file", upload)
    monkeypatch.setattr(storage, "open_object", open_object)
    monkeypatch.setattr(storage, "get_object", get)
    headers = _auth(await _tenant_token(client, "ingest@example.com", "Originales"))
    other = _auth(await _tenant_token(client, "other-ingest@example.com", "Otro"))
    prefix = "/api/documents/ingestion"
    originals = [_png((0, 100, 0)), _png((0, 0, 200))]
    body = {"client_id": str(uuid.uuid4()), "title": "Bautismos", "expected_files": 2,
            "reserved_bytes": sum(map(len, originals))}
    created = await client.post(prefix, headers=headers, json=body)
    assert created.status_code == 201, created.text
    sid = created.json()["id"]
    assert (await client.post(prefix, headers=headers, json=body)).json()["id"] == sid
    assert (await client.get(f"{prefix}/{sid}", headers=other)).status_code == 404
    entries = [{"path": f"Libro/{i}.png", "group": "Libro", "title": "Libro", "kind": "image_set", "position": i,
                "byte_size": len(data), "last_modified": 1} for i, data in enumerate(originals, 1)]
    response = await client.post(f"{prefix}/{sid}/manifest", headers=headers, json={"files": entries})
    assert response.status_code == 200, response.text
    files = response.json()
    assert (await client.post(f"{prefix}/{sid}/seal", headers=headers)).status_code == 200
    assert (await client.post(f"{prefix}/{sid}/complete", headers=headers)).status_code == 409
    for item, original in zip(files, originals):
        url = f"{prefix}/{sid}/files/{item['id']}/chunks/0"
        checksum = hashlib.sha256(original).hexdigest()
        upload_headers = {**headers, "X-Content-SHA256": checksum, "Content-Type": "application/octet-stream"}
        for _ in range(2):  # A lost HTTP response may cause an identical upload retry.
            response = await client.put(url, headers=upload_headers, content=original)
            assert response.status_code == 200, response.text
        assert (await client.get(f"{prefix}/{sid}/files/{item['id']}", headers=other)).status_code == 404
    status = (await client.get(f"{prefix}/{sid}", headers=headers)).json()
    assert status["received_bytes"] == body["reserved_bytes"]
    queued = await client.post(f"{prefix}/{sid}/complete", headers=headers)
    assert queued.status_code == 202, queued.text
    job_id = queued.json()["job_id"]
    assert (await client.post(f"{prefix}/{sid}/complete", headers=headers)).json()["job_id"] == job_id
    admin = create_async_engine(settings.admin_database_url)
    try:
        async with admin.connect() as connection:
            tenant = await connection.scalar(select(IngestSession.tenant_id).where(IngestSession.id == uuid.UUID(sid)))
    finally:
        await admin.dispose()
    await ingest_collection({}, job_id=job_id, tenant_id=str(tenant), session_id=sid)
    await ingest_collection({}, job_id=job_id, tenant_id=str(tenant), session_id=sid)  # terminal delivery is harmless
    status = (await client.get(f"{prefix}/{sid}", headers=headers)).json()
    assert status["status"] == "completed", status
    books = (await client.get(f"{prefix}/{sid}/books", headers=headers)).json()
    assert len(books) == 1 and books[0]["document_id"]
    document_id = books[0]["document_id"]
    pages = (await client.get(f"/api/documents/{document_id}/pages", headers=headers)).json()
    assert len(pages) == 2
    for page, original in zip(pages, originals):
        response = await client.get(f"/api/documents/{document_id}/pages/{page['page_no']}/content", headers=headers)
        assert response.content == original
        assert page["checksum"] == hashlib.sha256(original).hexdigest()
    assert (await client.get(f"/api/documents/{document_id}", headers=other)).status_code == 404
