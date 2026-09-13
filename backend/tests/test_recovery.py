"""Real PostgreSQL recovery snapshot/manifest test; guarded disposable database only."""
import asyncio
import uuid

from app.core.security import decode_token
from app.recovery import archive, database
from app.settings import settings
from .test_research import _tenant_token
from .test_tree import _auth


async def test_recovery_snapshot_catalog_and_reference_manifest_use_real_postgres(client, tmp_path):
    token = await _tenant_token(client, "recovery-fixture@example.com", "Recovery source")
    tid = uuid.UUID(decode_token(token, "access")["tid"])
    response = await client.post("/api/tree/persons", headers=_auth(token), json={"given": "Recovery", "surname": "Fixture"})
    assert response.status_code in (200, 201), response.text
    other = await _tenant_token(client, "recovery-other@example.com", "Other recovery source")
    response = await client.post("/api/tree/persons", headers=_auth(other), json={"given": "Other", "surname": "Fixture"})
    assert response.status_code in (200, 201), response.text
    connection = await database.connect(settings)
    try:
        async with connection.transaction(isolation="repeatable_read", readonly=True):
            state = await database.metadata(connection, settings.app_db_user)
            assert state["counts"]["tenants"] >= 2
            assert state["counts"]["persons"] == 2
            assert state["revisions"] == ["0053"]
            # The actual catalog must be JSON serializable before a bundle can finish.
            assert archive.canonical(state)
            assert all(isinstance(row["attidentity"], str) and isinstance(row["attgenerated"], str)
                       for row in state["columns"])
            assert any(row["tablename"] == "embedding_items" for row in state["policies"])
            assert any(row["table_name"] == "persons" for row in state["grants"])
            snapshot = await connection.fetchval("SELECT pg_export_snapshot()")
            count = await database.save_references(connection, {"private": settings.minio_bucket_private, "public": settings.minio_bucket_public}, tmp_path)
            assert count == 0  # No invented originals for an empty library.
            dump = tmp_path / "database.dump"
            with archive.private_file(dump): pass
            await asyncio.to_thread(database.pg_command, settings, "pg_dump", dump, snapshot=snapshot)
            with dump.open("rb") as stream: assert stream.read(5) == b"PGDMP"
        assert await database.check_rls(connection, settings.app_db_user) > 0
        assert await connection.fetchval("SELECT id FROM tenants WHERE id=$1", tid) == tid
    finally:
        await connection.close()
