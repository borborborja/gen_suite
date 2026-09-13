"""Opt-in real recovery drill. Never truncates, drops, cleans or starts services.

Requires an empty migrated source DB, a second empty PG16 cluster and four empty
precreated S3 buckets, all disposable. Configuration is explicit environment only.
Keeps the bundle, both databases and all objects for inspection, including on error.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import io
import os
from pathlib import Path
import uuid

import boto3
from botocore.config import Config
from httpx import ASGITransport, AsyncClient
from PIL import Image

from app.core import events, queue, storage
from app.core.crypto import SecretBox
from app.core.security import decode_token
from app.db.rls import set_rls_context
from app.db.session import SessionLocal, engine
from app.main import app
from app.models.ai_call import AiCall
from app.models.family import Family, FamilyChild
from app.models.provider import ProviderCredential
from app.models.transcription import Transcription
from app.recovery import archive, database, objects, service
from app.settings import Settings, settings
from app.tasks.document_tasks import compact_to_pdf
from app.tasks.tree_export_tasks import export_tree


def configuration():
    if os.environ.get("GEN_SUITE_RUN_RECOVERY_INTEGRATION") != "yes":
        raise RuntimeError("Habilita explícitamente el ensayo con servicios desechables.")
    if settings.environment != "development" or not settings.postgres_db.startswith("gensuite_test_"):
        raise RuntimeError("El origen debe ser una BD gensuite_test_* desechable en desarrollo.")
    if settings.smtp_host or settings.backup_to_s3:
        raise RuntimeError("Desactiva SMTP y copias externas automáticas durante el ensayo sintético.")
    if not all(name.startswith("gensuite-test-") for name in service.bucket_map(settings).values()):
        raise RuntimeError("Usa buckets de origen gensuite-test-* vacíos.")
    fields = ("postgres_host", "postgres_port", "postgres_db", "postgres_user", "postgres_password",
        "app_db_user", "app_db_password", "minio_endpoint", "minio_access_key", "minio_secret_key",
        "minio_bucket_private", "minio_bucket_public", "minio_secure", "minio_region",
        "minio_addressing_style", "suite_master_key")
    target = Settings(_env_file=None, **{**settings.model_dump(),
        **{field: os.environ["RECOVERY_TEST_TARGET_" + field.upper()] for field in fields}})
    if not target.postgres_db.startswith("gensuite_restore_"):
        raise RuntimeError("Usa una BD destino gensuite_restore_* vacía en otro clúster.")
    if target.minio_endpoint == settings.minio_endpoint:
        raise RuntimeError("Este ensayo exige también un servidor S3 destino independiente.")
    return target


def s3_client(config):
    return boto3.client("s3", endpoint_url=("https://" if config.minio_secure else "http://") + config.minio_endpoint,
        aws_access_key_id=config.minio_access_key, aws_secret_access_key=config.minio_secret_key,
        region_name=config.minio_region, config=Config(signature_version="s3v4",
            retries={"total_max_attempts": 1}, s3={"addressing_style": config.minio_addressing_style or "auto"}))


async def preflight(target, target_s3, directory):
    if directory.exists() or directory.is_symlink() or not directory.parent.is_dir():
        raise RuntimeError("El bundle debe ser un directorio nuevo dentro de una carpeta preparada.")
    source = await database.connect(settings)
    destination = await database.connect(target)
    try:
        await database.require_quiet(source)
        if await source.fetchval("SELECT count(*) FROM users") or await source.fetchval("SELECT count(*) FROM tenants"):
            raise RuntimeError("El origen no está vacío; no se modifica ni se trunca.")
        assert await source.fetchval("SELECT version_num FROM alembic_version") == "0053"
        manifest = {"source": await database.identity(source), "owner_role": settings.postgres_user,
                    "app_role": settings.app_db_user}
        await database.require_empty_target(destination, target, manifest)
        assert not set(service.bucket_map(settings).values()) & set(service.bucket_map(target).values())
        assert all(name.startswith("gensuite-restore-") for name in service.bucket_map(target).values())
        assert service.key_fingerprint(target) == service.key_fingerprint(settings)
        await asyncio.to_thread(objects.require_empty_buckets, storage._s3(), service.bucket_map(settings))
        await asyncio.to_thread(objects.require_empty_buckets, target_s3, service.bucket_map(target))
    finally:
        await source.close(); await destination.close()


async def request(client, method, path, expected, **kwargs):
    response = await client.request(method, path, **kwargs)
    if response.status_code != expected:
        # The fixture contains no personal data; still keep failed response bodies out of logs.
        raise AssertionError(f"{method} {path}: {response.status_code}, esperado {expected}")
    return response.json()


async def seed():
    """Seed through real HTTP and a few explicit synthetic runtime-state fixtures."""
    fixtures = []
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for index in range(2):
            registered = await request(client, "POST", "/api/auth/register", 201,
                json={"email": f"restore-{uuid.uuid4().hex}@example.com", "password": "synthetic-test-password"})
            initial = {"Authorization": "Bearer " + registered["access_token"]}
            tenant = await request(client, "POST", "/api/tenants", 201, headers=initial,
                json={"name": f"Archivo sintético {index}"})
            switched = await request(client, "POST", f"/api/auth/switch/{tenant['id']}", 200, headers=initial)
            token = switched["access_token"]; claims = decode_token(token, "access")
            headers = {"Authorization": "Bearer " + token}
            tid, actor = uuid.UUID(tenant["id"]), uuid.UUID(claims["sub"])
            people = [await request(client, "POST", "/api/tree/persons", 200, headers=headers,
                json={"given": name, "surname": f"Sintético {index}"}) for name in ("Josep", "Lluïsa", "Maria")]
            buffer = io.BytesIO()
            Image.new("RGB", (16, 24), (120 + index, 30, 60)).save(buffer, "PNG")
            original = buffer.getvalue()
            doc = await request(client, "POST", "/api/documents", 201, headers=headers,
                data={"title": f"Batejos sintètics {index}", "visibility": "private"},
                files=[("files", ("pàgina.png", original, "image/png"))])
            await request(client, "POST", "/api/tree/citations", 200, headers=headers,
                json={"target_type": "person", "target_id": people[2]["id"], "document_id": doc["id"],
                    "page_no": 1, "quote": "Maria, filla de Josep i Lluïsa", "quality": "original",
                    "locator": {"folio": "12v", "line_start": 4}})
            async with SessionLocal() as db:
                await set_rls_context(db, tenant_id=tid)
                family = Family(tenant_id=tid, husband_id=uuid.UUID(people[0]["id"]), wife_id=uuid.UUID(people[1]["id"]))
                db.add(family); await db.flush()
                db.add(FamilyChild(tenant_id=tid, family_id=family.id, person_id=uuid.UUID(people[2]["id"]), seq=0))
                transcription = Transcription(tenant_id=tid, document_id=uuid.UUID(doc["id"]), page_no=1,
                    engine="manual", text="Lectura inicial: Maria", embedding=[1.0] + [0.0] * 1023)
                db.add(transcription); await db.flush()
                transcription_id = transcription.id
                await db.commit()
            await request(client, "PATCH", f"/api/transcription/{transcription_id}", 200,
                headers=headers, json={"text": "Parròquia de Sant Martí. Maria, filla de Josep i Lluïsa."})
            if index:
                await request(client, "POST", f"/api/documents/{doc['id']}/publish", 200,
                    headers=headers, json={"rights_declaration": "public_domain"})
            export = await request(client, "POST", f"/api/documents/{doc['id']}/exports", 202,
                headers=headers, json={"kind": "pdf"})
            await compact_to_pdf({}, tenant_id=tid, job_id=export["id"], document_id=doc["id"])
            job = await request(client, "GET", f"/api/jobs/{export['id']}", 200, headers=headers)
            assert job["status"] == "completed", "No se ha generado el PDF real del fixture"
            tree_export = await request(client, "POST", "/api/tree/exports", 202, headers=headers, json={"kind": "gedzip"})
            await export_tree({}, tenant_id=tid, job_id=tree_export["id"])
            tree_job = await request(client, "GET", f"/api/jobs/{tree_export['id']}", 200, headers=headers)
            assert tree_job["status"] == "completed", "No se ha generado el GEDZIP sintético"
            await request(client, "POST", "/api/storage/inventories", 202, headers=headers,
                json={"client_id": str(uuid.uuid4()), "reason": "Inventario sintético pendiente para probar cuarentena"})
            await request(client, "PUT", "/api/providers/budget", 200, headers=headers,
                json={"monthly_budget_cents": 1000})
            run = await request(client, "POST", "/api/research/runs", 201, headers=headers,
                json={"client_id": str(uuid.uuid4()), "title": "Investigación sintética pendiente", "document_ids": [doc["id"]],
                    "budget_microusd": 0, "transcribe": False, "extract": False, "index": False,
                    "discover_all_tree": False, "pdf": True, "archive": False})
            await request(client, "POST", f"/api/research/runs/{run['id']}/start", 200,
                headers=headers, json={"expected_version": run["version"]})
            ciphertext, nonce = SecretBox(settings.suite_master_key).encrypt("synthetic-credential-not-a-provider-key")
            async with SessionLocal() as db:
                await set_rls_context(db, tenant_id=tid)
                db.add(ProviderCredential(tenant_id=tid, scope="tenant", provider_key="openai", label="Synthetic only",
                    api_key_ciphertext=ciphertext, api_key_nonce=nonce, is_active=True))
                db.add(AiCall(tenant_id=tid, research_run_id=uuid.UUID(run["id"]), task_type="transcription",
                    operation_key=hashlib.sha256(tid.bytes).hexdigest(), request_hash="a" * 64, profile={},
                    state="inflight", reserved_microusd=1))
                await db.commit()
            # Reserve without sending: restoration must quarantine this, never repeat a PUT.
            from app.modules.storage_audit.quota import reserve_independent
            await reserve_independent(settings.minio_bucket_private, f"{tid}/pending-synthetic.bin", 7)
            fixtures.append({"tenant": tid, "document": uuid.UUID(doc["id"]), "original": original,
                "key": f"{tid}/{doc['id']}/pages/1.png", "public": bool(index)})
    return fixtures


async def close_app_connections():
    await engine.dispose()
    for module, attribute in ((events, "_redis"), (queue, "_pool")):
        client = getattr(module, attribute)
        if client is not None:
            await client.aclose(); setattr(module, attribute, None)


async def content_fingerprints(config):
    connection = await database.connect(config)
    try:
        # These tables are unchanged by quarantine. Includes text revisions, vectors,
        # original evidence links, manifests, and the complete source attribution.
        tables = ("persons", "names", "families", "family_children", "research_sources",
            "citations", "pages", "transcriptions", "document_exports", "tree_exports", "tree_imports", "source_documents", "task_dispatches", "change_log")
        result = {}
        for table in tables:
            rows = await connection.fetch(f"SELECT to_jsonb(t)::text AS row FROM {database.identifier(table)} t ORDER BY to_jsonb(t)::text")
            result[table] = hashlib.sha256("\n".join(row["row"] for row in rows).encode()).hexdigest()
        return result
    finally: await connection.close()


async def verify_restored(target, s3, fixtures):
    connection = await database.connect(target)
    try:
        role = await connection.fetchrow("SELECT rolcanlogin,rolsuper,rolbypassrls FROM pg_roles WHERE rolname=$1", target.app_db_user)
        assert role and not any(role.values())
        for table, condition in (("jobs", "status IN ('queued','running')"),
                ("research_runs", "status='running'"), ("ai_calls", "state='inflight'"),
                ("provider_credentials", "is_active"), ("tenants", "monthly_budget_cents IS NOT NULL"),
                ("storage_writes", "state IN ('reserved','sending','unknown')"),
                ("storage_scans", "invalidated_at IS NULL"), ("storage_quotas", "phase<>'uninitialized' OR reserved_bytes<>0")):
            assert await connection.fetchval(f"SELECT count(*) FROM {database.identifier(table)} WHERE {condition}") == 0, table
        assert await connection.fetchval("SELECT count(*) FROM storage_writes WHERE state='quarantined'") == 2
        assert await connection.fetchval("SELECT count(*) FROM ai_calls WHERE state='unknown'") == 2
        for row in await connection.fetch("SELECT api_key_ciphertext,api_key_nonce FROM provider_credentials"):
            assert SecretBox(target.suite_master_key).decrypt(row[0], row[1]) == "synthetic-credential-not-a-provider-key"
        for fixture in fixtures:
            bucket = target.minio_bucket_public if fixture["public"] else target.minio_bucket_private
            assert await connection.fetchval("SELECT storage_bucket FROM documents WHERE id=$1", fixture["document"]) == bucket
            response = await asyncio.to_thread(s3.get_object, Bucket=bucket, Key=fixture["key"])
            try: assert response["Body"].read() == fixture["original"]
            finally: response["Body"].close()
            async with connection.transaction():
                await connection.execute(f"SET LOCAL ROLE {database.identifier(target.app_db_user)}")
                await connection.execute("SELECT set_config('app.tenant_id',$1,true),set_config('app.user_role','tenant_admin',true)", str(fixture["tenant"]))
                assert await connection.fetchval("SELECT count(*) FROM persons") == 3
                assert await connection.fetchval("SELECT count(*) FROM citations") == 1
                assert await connection.fetchval("SELECT count(*) FROM research_sources") == 1
                assert await connection.fetchval("SELECT count(*) FROM family_children") == 1
                assert await connection.fetchval("SELECT count(*) FROM transcriptions WHERE tenant_id=$1", fixture["tenant"]) == 2
                assert await connection.fetchval("SELECT count(*) FROM persons WHERE tenant_id<>$1", fixture["tenant"]) == 0
    finally: await connection.close()


async def must_refuse(operation):
    try: await operation
    except archive.RecoveryError: return
    raise AssertionError("La operación insegura no fue rechazada")


async def run(directory):
    target = configuration(); target_s3 = s3_client(target)
    await preflight(target, target_s3, directory)
    try:
        fixtures = await seed()
    finally:
        await close_app_connections()
    before = await content_fingerprints(settings)
    other_connection = await database.connect(settings)
    denied = directory.with_name(directory.name + "-busy-source")
    try:
        await must_refuse(service.create(settings, storage._s3(), denied, quiesced=True, key_escrow=True))
        assert not denied.exists()
    finally: await other_connection.close()
    manifest = await service.create(settings, storage._s3(), directory, quiesced=True, key_escrow=True)
    assert archive.verify(directory) == manifest
    assert manifest["format"] == archive.FORMAT
    assert set(manifest["database"]["row_integrity"]["tables"]) == set(manifest["database"]["counts"])
    assert isinstance(manifest["database"]["triggers"], list)
    wrong_key = base64.b64encode(b"x" * 32).decode()
    assert wrong_key != settings.suite_master_key
    await must_refuse(service.restore(target.model_copy(update={"suite_master_key": wrong_key}), target_s3,
        directory, isolated=True, trusted=True, key_escrow=True))
    report = await service.restore(target, target_s3, directory, isolated=True, trusted=True, key_escrow=True)
    assert report["database_verified"] and report["object_readback_verified"] and report["rls_checks"] > 0
    assert report["row_data_verified"] and report["triggers_verified"]
    assert report["row_tables_verified"] == len(manifest["database"]["counts"])
    assert report["app_role_login"] is False and report["production_ready"] is False
    assert await content_fingerprints(target) == before
    assert await content_fingerprints(settings) == before  # Source remains unchanged too.
    await verify_restored(target, target_s3, fixtures)
    await must_refuse(service.restore(target, target_s3, directory, isolated=True, trusted=True, key_escrow=True))
    assert await content_fingerprints(target) == before  # A populated destination cannot be overwritten.
    print(f"PASS: copia/restauración reales; {sum(v['count'] for v in manifest['objects'].values())} objetos; "
          f"{manifest['references']} referencias; {report['rls_checks']} comprobaciones RLS; cuarentena y originales verificados.")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    asyncio.run(run(args.directory.absolute()))


if __name__ == "__main__": main()
