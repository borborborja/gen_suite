"""Real PostgreSQL + isolated MinIO contracts. Not run against installed application buckets."""
import asyncio
import hashlib
import uuid
import pytest
from sqlalchemy import select, text
from app.core import storage
from app.core.security import decode_token
from app.db.rls import set_rls_context
from app.db.session import SessionLocal, engine
from app.models.ingest import IngestFile, IngestSession
from app.models.ingest_cleanup import IngestCleanup, IngestCleanupObject
from app.models.task_dispatch import TaskDispatch
from app.settings import settings
from app.tasks.ingestion_tasks import ingest_collection
from app.tasks import ingest_cleanup_tasks as task
from .test_documents import _auth, _png, _tenant_token

BASE = "/api/documents/ingestion"


async def setup(client, monkeypatch, *, mode="discard", versioned=False):
    # The DB fixture already requires explicit disposable-DB authorization. Add a storage
    # guard, then use a new unique private bucket without changing any existing bucket.
    assert settings.minio_bucket_private.startswith(("gensuite-ci-", "gensuite-test-"))
    assert settings.minio_bucket_public.startswith(("gensuite-ci-", "gensuite-test-"))
    bucket = f"gensuite-test-cleanup-{uuid.uuid4().hex}"
    monkeypatch.setattr(settings, "minio_bucket_private", bucket)
    await storage.ensure_buckets()
    if versioned:
        await asyncio.to_thread(storage._s3().put_bucket_versioning, Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
    token = await _tenant_token(client, "cleanup@example.com", "Cleanup fixture")
    tid = uuid.UUID(decode_token(token, "access")["tid"]); headers = _auth(token)
    original = _png((11, 22, 33))
    created = await client.post(BASE, headers=headers, json={"client_id": str(uuid.uuid4()), "title": "Test staging", "expected_files": 1, "reserved_bytes": len(original)})
    assert created.status_code == 201, created.text
    sid = created.json()["id"]
    entries = [{"path": "Book/1.png", "group": "Book", "title": "Book", "kind": "image_set", "position": 1,
                "byte_size": len(original), "last_modified": 1}]
    files = await client.post(f"{BASE}/{sid}/manifest", headers=headers, json={"files": entries})
    assert files.status_code == 200, files.text
    fid = files.json()[0]["id"]
    assert (await client.post(f"{BASE}/{sid}/seal", headers=headers)).status_code == 200
    response = await client.put(f"{BASE}/{sid}/files/{fid}/chunks/0", headers={**headers, "X-Content-SHA256": hashlib.sha256(original).hexdigest()}, content=original)
    assert response.status_code == 200, response.text
    async with SessionLocal() as session:
        await set_rls_context(session, tenant_id=tid)
        key = (await session.get(IngestFile, uuid.UUID(fid))).parts["0"]["key"]
    if versioned: await storage.put_object(bucket, key, original, "application/octet-stream")
    if mode == "compact":
        queued = await client.post(f"{BASE}/{sid}/complete", headers=headers)
        assert queued.status_code == 202, queued.text
        await ingest_collection({}, tenant_id=tid, job_id=queued.json()["job_id"], session_id=sid)
        assert (await client.get(f"{BASE}/{sid}", headers=headers)).json()["status"] == "completed"
    return tid, sid, fid, headers, original, key


async def approve(client, sid, headers):
    compared = await client.post(f"{BASE}/{sid}/cleanup/preview", headers=headers)
    assert compared.status_code == 200, compared.text
    body = {"preview_hash": compared.json()["preview_hash"], "reason": "Synthetic staging disposal with local originals retained",
            "authorize_permanent_staging_deletion": True, "local_originals_confirmed": True}
    response = await client.post(f"{BASE}/{sid}/cleanup", headers=headers, json=body)
    assert response.status_code == 202, response.text
    assert (await client.post(f"{BASE}/{sid}/cleanup", headers=headers, json=body)).json()["id"] == response.json()["id"]
    return response.json()


@pytest.mark.parametrize("mode,versioned", [("discard", False), ("discard", True), ("compact", False), ("compact", True)])
async def test_staging_cleanup_preserves_originals_fences_replay_and_accounts_versions(client, monkeypatch, mode, versioned):
    tid, sid, fid, headers, original, key = await setup(client, monkeypatch, mode=mode, versioned=versioned)
    intent = await approve(client, sid, headers)
    quota = (await client.get(f"{BASE}/quota", headers=headers)).json()
    assert quota["admission_bytes"] == len(original)  # Approval alone releases nothing.
    assert (await client.put(f"{BASE}/{sid}/files/{fid}/chunks/0", headers={**headers, "X-Content-SHA256": hashlib.sha256(original).hexdigest()}, content=original)).status_code == 409
    assert (await client.post(f"{BASE}/{sid}/complete", headers=headers)).status_code == 409
    await task.cleanup_ingestion({}, tenant_id=tid, job_id=intent["job_id"], cleanup_id=intent["id"])
    result = (await client.get(f"{BASE}/{sid}/cleanup", headers=headers)).json()
    assert result["phase"] == "completed", result
    assert result["deleted_objects"] == (2 if versioned else 1)
    assert result["deleted_bytes"] == len(original) * (2 if versioned else 1)
    assert (await client.get(f"{BASE}/quota", headers=headers)).json()["admission_bytes"] == (len(original) if mode == "compact" else 0)
    assert not (await client.get(f"{BASE}/{sid}/files/{fid}", headers=headers)).json()["parts"]
    remaining, _ = await asyncio.to_thread(task.version_page, storage._s3(), settings.minio_bucket_private, tid, sid, {})
    assert remaining == []
    if mode == "compact":
        book = (await client.get(f"{BASE}/{sid}/books", headers=headers)).json()[0]
        content = await client.get(f"/api/documents/{book['document_id']}/pages/1/content", headers=headers)
        assert content.content == original
    other = _auth(await _tenant_token(client, "other-cleanup@example.com", "Other"))
    assert (await client.get(f"{BASE}/{sid}/cleanup", headers=other)).status_code == 404
    assert (await client.post(f"{BASE}/{sid}/cleanup/retry", headers=other)).status_code == 404


async def test_lost_delete_acknowledgement_retries_exact_version_and_same_job(client, monkeypatch):
    tid, sid, _, headers, _, key = await setup(client, monkeypatch)
    intent = await approve(client, sid, headers)
    real_delete = task.delete_version
    def lose_ack(*args):
        real_delete(*args)
        raise RuntimeError("Synthetic lost acknowledgement after deletion")
    monkeypatch.setattr(task, "delete_version", lose_ack)
    await task.cleanup_ingestion({}, tenant_id=tid, job_id=intent["job_id"], cleanup_id=intent["id"])
    failed = (await client.get(f"{BASE}/{sid}/cleanup", headers=headers)).json()
    assert failed["job_status"] == "error" and failed["deleted_objects"] == 0 and failed["planned_objects"] == 1
    assert (await client.get(f"{BASE}/quota", headers=headers)).json()["admission_bytes"] > 0
    retried = await client.post(f"{BASE}/{sid}/cleanup/retry", headers=headers)
    assert retried.status_code == 202 and retried.json()["job_id"] == intent["job_id"], retried.text
    monkeypatch.setattr(task, "delete_version", real_delete)
    await task.cleanup_ingestion({}, tenant_id=tid, job_id=intent["job_id"], cleanup_id=intent["id"])
    result = (await client.get(f"{BASE}/{sid}/cleanup", headers=headers)).json()
    assert result["phase"] == "completed" and result["deleted_objects"] == 1
    async with SessionLocal() as session:
        await set_rls_context(session, tenant_id=tid)
        assert (await session.get(TaskDispatch, uuid.UUID(intent["job_id"]))).generation == 1
        records = (await session.scalars(select(IngestCleanupObject))).all()
        assert len(records) == 1 and records[0].storage_key == key and records[0].version_id == "null"


async def test_stale_preview_and_live_original_worker_lock_cannot_authorize_cleanup(client, monkeypatch):
    tid, sid, _, headers, _, _ = await setup(client, monkeypatch, mode="compact")
    compared = await client.post(f"{BASE}/{sid}/cleanup/preview", headers=headers)
    body = {"preview_hash": compared.json()["preview_hash"], "reason": "Synthetic confirmation",
            "authorize_permanent_staging_deletion": True, "local_originals_confirmed": True}
    async with SessionLocal() as session:
        await set_rls_context(session, tenant_id=tid)
        collection = await session.get(IngestSession, uuid.UUID(sid))
        job_id = collection.job_id
        collection.title = "Concurrent edit"
        await session.commit()
    assert (await client.post(f"{BASE}/{sid}/cleanup", headers=headers, json=body)).status_code == 409
    compared = await client.post(f"{BASE}/{sid}/cleanup/preview", headers=headers)
    body["preview_hash"] = compared.json()["preview_hash"]
    lock = int.from_bytes(job_id.bytes[:8], "big", signed=True)
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": lock})
        await connection.commit()
        try:
            assert (await client.post(f"{BASE}/{sid}/cleanup", headers=headers, json=body)).status_code == 409
        finally:
            await connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock}); await connection.commit()
    async with SessionLocal() as session:
        await set_rls_context(session, tenant_id=tid)
        assert not (await session.scalars(select(IngestCleanup))).all()


async def test_unversioned_staging_listing_advances_past_one_hundred_fragments(client, monkeypatch):
    tid, sid, _, _, original, key = await setup(client, monkeypatch)
    bucket = settings.minio_bucket_private
    # Real legacy/orphan synthetic fragments: no new application rows and no deletions.
    keys = {key}
    for _ in range(104):
        fragment = f"{tid}/ingestion/{sid}/{uuid.uuid4()}/0000000000000000-" + hashlib.sha256(b"x").hexdigest()
        keys.add(fragment)
        await asyncio.to_thread(storage._s3().put_object, Bucket=bucket, Key=fragment, Body=b"x")
    first, cursor = await asyncio.to_thread(task.version_page, storage._s3(), bucket, tid, sid, {})
    assert len(first) == 100 and cursor
    second, end = await asyncio.to_thread(task.version_page, storage._s3(), bucket, tid, sid, cursor)
    assert len(second) == 5 and not end
    assert {row["storage_key"] for row in first + second} == keys
    assert all(row["version_id"] == "null" for row in first + second)
    assert (await storage.get_object(bucket, key))[0] == original
