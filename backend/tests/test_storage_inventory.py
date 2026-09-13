"""PostgreSQL + isolated MinIO inventory tests. Only disposable, explicitly allowed DBs."""
import asyncio
import uuid
import pytest
from sqlalchemy import func, select, text
from app.core import storage
from app.core.security import decode_token
from app.db.rls import set_rls_context
from app.db.session import SessionLocal, engine
from app.models.storage_inventory import StorageInventoryItem
from app.models.task_dispatch import TaskDispatch
from app.settings import settings
from app.tasks import storage_inventory_tasks as task
from .test_documents import _auth, _tenant_token

BASE = "/api/storage/inventories"


async def setup(client, monkeypatch, *, versioned):
    assert settings.minio_bucket_private.startswith(("gensuite-ci-", "gensuite-test-"))
    assert settings.minio_bucket_public.startswith(("gensuite-ci-", "gensuite-test-"))
    identifier = uuid.uuid4().hex
    monkeypatch.setattr(settings, "minio_bucket_private", f"gensuite-test-inventory-p-{identifier}")
    monkeypatch.setattr(settings, "minio_bucket_public", f"gensuite-test-inventory-u-{identifier}")
    await storage.ensure_buckets()
    if versioned:
        await asyncio.to_thread(storage._s3().put_bucket_versioning, Bucket=settings.minio_bucket_private, VersioningConfiguration={"Status": "Enabled"})
    token = await _tenant_token(client, "inventory@example.com", "Inventory fixture")
    tid = uuid.UUID(decode_token(token, "access")["tid"])
    return tid, _auth(token)


async def create(client, headers, *, reason="Synthetic inventory"):
    body = {"client_id": str(uuid.uuid4()), "reason": reason}
    response = await client.post(BASE, headers=headers, json=body)
    assert response.status_code == 202, response.text
    replay = await client.post(BASE, headers=headers, json=body)
    assert replay.status_code == 202 and replay.json()["job_id"] == response.json()["job_id"], replay.text
    assert (await client.post(BASE, headers=headers, json={**body, "reason": "Changed"})).status_code == 409
    return response.json()


@pytest.mark.parametrize("versioned", [False, True])
async def test_counts_history_copies_orphans_multipart_and_preserves_every_object(client, monkeypatch, versioned):
    tid, headers = await setup(client, monkeypatch, versioned=versioned)
    private, public = settings.minio_bucket_private, settings.minio_bucket_public
    key = f"{tid}/{uuid.uuid4()}/original.pdf"
    await storage.put_object(private, key, b"first", "application/pdf")
    await storage.put_object(private, key, b"second", "application/pdf")
    await storage.copy_object(private, public, key)  # Both payload copies must count.
    orphan = f"{tid}/legacy/Àvia%2F+100%.jpg"
    await storage.put_object(private, orphan, b"orphan", "image/jpeg")
    deleted = f"{tid}/old.jpg"
    await storage.put_object(private, deleted, b"gone", "image/jpeg")
    await storage.delete_object(private, deleted)  # Marker in versioned storage, real removal otherwise.
    # Legacy/external object fixture, deliberately outside any database tenant.
    await asyncio.to_thread(storage._s3().put_object, Bucket=private, Key=f"{uuid.uuid4()}/other-tenant.jpg", Body=b"not-counted", ContentType="image/jpeg")
    multipart_key = f"{tid}/incomplete.pdf"
    upload = await asyncio.to_thread(storage._s3().create_multipart_upload, Bucket=private, Key=multipart_key)
    await asyncio.to_thread(storage._s3().upload_part, Bucket=private, Key=multipart_key,
        UploadId=upload["UploadId"], PartNumber=1, Body=b"unfinished")
    request = await create(client, headers)
    assert (await client.post(BASE, headers=headers, json={"client_id": str(uuid.uuid4()), "reason": "Second"})).status_code == 409
    await task.inventory_storage({}, tenant_id=tid, job_id=request["job_id"], scan_id=request["id"])
    result = (await client.get(f"{BASE}/{request['id']}", headers=headers)).json()
    assert result["job_status"] == "completed", result
    assert result["observed_payload_bytes"] == 28 + (9 if versioned else 0)
    assert result["bytes"] == {"current_versions": 18, "noncurrent_versions": 9 if versioned else 0, "multipart_parts": 10}
    assert result["counts"]["delete_markers"] == (1 if versioned else 0)
    assert result["counts"]["uninspected_uploads"] == 0
    assert result["upload_inspection_complete"] and not result["consistent_snapshot"] and not result["quota_enforced"]
    assert "storage_key" not in str(result) and private not in str(result)
    assert (await storage.get_object(private, key))[0] == b"second"
    assert (await storage.get_object(public, key))[0] == b"second"
    assert (await storage.get_object(private, orphan))[0] == b"orphan"
    parts = await asyncio.to_thread(storage._s3().list_parts, Bucket=private, Key=multipart_key, UploadId=upload["UploadId"])
    assert parts["Parts"][0]["Size"] == 10
    other = _auth(await _tenant_token(client, "other-inventory@example.com", "Other"))
    assert (await client.get(BASE, headers=other)).json() == []
    assert (await client.get(f"{BASE}/{request['id']}", headers=other)).status_code == 404
    assert (await client.post(f"{BASE}/{request['id']}/retry", headers=other)).status_code == 404


async def test_inventory_resumes_after_a_committed_page_and_detects_overlap(client, monkeypatch):
    tid, headers = await setup(client, monkeypatch, versioned=False)
    private = settings.minio_bucket_private
    for n in range(105):
        await storage.put_object(private, f"{tid}/legacy/{n:03}.jpg", b"x", "image/jpeg")
    request = await create(client, headers)
    real_reader = task.inventory.versions_page
    def fail_second_page(client, bucket, tenant_id, cursor):
        if cursor: raise RuntimeError("Synthetic disconnected listing")
        return real_reader(client, bucket, tenant_id, cursor)
    monkeypatch.setattr(task.inventory, "versions_page", fail_second_page)
    await task.inventory_storage({}, tenant_id=tid, job_id=request["job_id"], scan_id=request["id"])
    failed = (await client.get(f"{BASE}/{request['id']}", headers=headers)).json()
    assert failed["job_status"] == "error" and failed["observed_payload_bytes"] == 100, failed
    assert not failed["listing_finished"]
    # A stale job status alone does not prove that its worker has released its lock.
    lock = int.from_bytes(uuid.UUID(request["job_id"]).bytes[:8], "big", signed=True)
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": lock})
        await connection.commit()
        try:
            assert (await client.post(f"{BASE}/{request['id']}/retry", headers=headers)).status_code == 409
        finally:
            await connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock}); await connection.commit()
    assert (await client.post(f"{BASE}/{request['id']}/retry", headers=headers)).status_code == 202
    # Overlapping page must fail, not silently double-count or ignore repeated versions.
    def overlap(client, bucket, tenant_id, cursor):
        rows, following = real_reader(client, bucket, tenant_id, cursor)
        if cursor:
            rows[0]["storage_key"] = f"{tid}/legacy/000.jpg"
        return rows, following
    monkeypatch.setattr(task.inventory, "versions_page", overlap)
    await task.inventory_storage({}, tenant_id=tid, job_id=request["job_id"], scan_id=request["id"])
    failed = (await client.get(f"{BASE}/{request['id']}", headers=headers)).json()
    assert failed["job_status"] == "error" and failed["observed_payload_bytes"] == 100, failed
    assert (await client.post(f"{BASE}/{request['id']}/retry", headers=headers)).status_code == 202
    monkeypatch.setattr(task.inventory, "versions_page", real_reader)
    await task.inventory_storage({}, tenant_id=tid, job_id=request["job_id"], scan_id=request["id"])
    result = (await client.get(f"{BASE}/{request['id']}", headers=headers)).json()
    assert result["job_status"] == "completed" and result["observed_payload_bytes"] == 105, result
    async with SessionLocal() as session:
        await set_rls_context(session, tenant_id=tid)
        assert (await session.get(TaskDispatch, uuid.UUID(request["job_id"]))).generation == 2
        assert await session.scalar(select(func.count()).select_from(StorageInventoryItem)) == 105


async def test_cancelled_or_invalidated_scan_does_not_read_s3_or_resume(client, monkeypatch):
    tid, headers = await setup(client, monkeypatch, versioned=False)
    request = await create(client, headers)
    def forbidden(): raise AssertionError("Cancelled inventory must not reach S3")
    monkeypatch.setattr(task.storage, "_s3", forbidden)
    result = await client.post(f"{BASE}/{request['id']}/cancel", headers=headers)
    assert result.status_code == 202 and result.json()["job_status"] == "cancelled"
    await task.inventory_storage({}, tenant_id=tid, job_id=request["job_id"], scan_id=request["id"])
    assert (await client.post(f"{BASE}/{request['id']}/retry", headers=headers)).status_code == 409
    another = await create(client, headers, reason="After restore")
    async with SessionLocal() as session:
        await set_rls_context(session, tenant_id=tid)
        await session.execute(text("UPDATE storage_scans SET invalidated_at=now() WHERE id=:id"), {"id": uuid.UUID(another["id"])})
        await session.execute(text("UPDATE jobs SET status='paused' WHERE id=:id"), {"id": uuid.UUID(another["job_id"])})
        await session.commit()
    assert (await client.post(f"{BASE}/{another['id']}/retry", headers=headers)).status_code == 409
    assert not (await client.get(f"{BASE}/{another['id']}", headers=headers)).json()["storage_configuration_matches"]


async def test_viewer_can_read_totals_but_not_start_cancel_or_retry_inventory(client):
    token = await _tenant_token(client, "storage-owner@example.com", "Storage roles")
    tid = decode_token(token, "access")["tid"]; owner = _auth(token)
    request = await create(client, owner)
    registered = await client.post("/api/auth/register", json={"email": "storage-viewer@example.com", "password": "supersecret123"})
    assert registered.status_code in (200, 201), registered.text
    member = await client.post("/api/tenants/members", headers=owner, json={"email": "storage-viewer@example.com", "role": "viewer"})
    assert member.status_code in (200, 201), member.text
    switched = await client.post(f"/api/auth/switch/{tid}", headers=_auth(registered.json()["access_token"]))
    assert switched.status_code == 200, switched.text
    viewer = _auth(switched.json()["access_token"])
    assert (await client.get(f"{BASE}/{request['id']}", headers=viewer)).status_code == 200
    assert (await client.post(BASE, headers=viewer, json={"client_id": str(uuid.uuid4()), "reason": "Denied"})).status_code == 403
    for action in ("cancel", "retry"):
        assert (await client.post(f"{BASE}/{request['id']}/{action}", headers=viewer)).status_code == 403
