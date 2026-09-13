"""Complete current-state recovery, conditional on an operator-enforced maintenance window."""
from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import uuid

from . import database, objects
from .archive import FORMAT, ROLES, RecoveryError, canonical, descriptor, private_file, verify
from .schema import metadata_matches


def bucket_map(settings):
    buckets = {"private": settings.minio_bucket_private, "public": settings.minio_bucket_public}
    if len(set(buckets.values())) != 2 or not all(buckets.values()):
        raise RecoveryError("Se necesitan dos buckets de aplicación distintos.")
    return buckets


def key_fingerprint(settings):
    try:
        value = base64.b64decode(settings.suite_master_key, validate=True)
    except (ValueError, TypeError):
        raise RecoveryError("Configura la clave maestra custodiada fuera de esta copia.") from None
    if len(value) != 32:
        raise RecoveryError("La clave maestra debe tener 32 bytes.")
    return hashlib.sha256(value).hexdigest()


def new_directory(path):
    path = Path(path).absolute()
    if path.exists() or path.is_symlink() or not path.parent.is_dir():
        raise RecoveryError("Usa un directorio nuevo dentro de un destino ya preparado; no se sobrescriben copias.")
    path.mkdir(mode=0o700)
    return path


async def create(settings, s3, path, *, quiesced=False, key_escrow=False):
    if not quiesced or not key_escrow:
        raise RecoveryError("Confirma la parada de todos los escritores y la custodia externa de la clave/configuración.")
    buckets, master_key = bucket_map(settings), key_fingerprint(settings)
    connection = await database.connect(settings)
    try:
        await database.require_quiet(connection)
        source = await database.identity(connection)
        if source["server_major"] != 16 or settings.postgres_user == settings.app_db_user:
            raise RecoveryError("Esta versión requiere PostgreSQL 16 y roles propietario/aplicación separados.")
        root = new_directory(path)
        async with connection.transaction(isolation="repeatable_read", readonly=True):
            snapshot = await connection.fetchval("SELECT pg_export_snapshot()")
            state = await database.metadata(connection, settings.app_db_user)
            if state["revisions"] not in (["0044"], ["0045"], ["0046"], ["0047"], ["0048"], ["0049"], ["0050"], ["0051"], ["0052"], ["0053"]):
                raise RecoveryError("Este recuperador admite esquemas 0044 a 0053; no se interpreta otro esquema por suposición.")
            references = await database.save_references(connection, buckets, root)
            dump = root / "database.dump"
            with private_file(dump):
                pass  # Private permissions before pg_dump opens the file.
            await asyncio.to_thread(database.pg_command, settings, "pg_dump", dump, snapshot=snapshot)
            with dump.open("rb") as stream:
                if stream.read(5) != b"PGDMP":
                    raise RecoveryError("PostgreSQL no generó un archivo custom válido.")
                os.fsync(stream.fileno())
            totals, inventory = await asyncio.to_thread(objects.download_objects, s3, buckets, root)
            await database.require_quiet(connection)
        manifest = {"format": FORMAT, "complete": True, "id": str(uuid.uuid4()),
            "created_at": datetime.now(timezone.utc).isoformat(), "source": source,
            "owner_role": settings.postgres_user, "app_role": settings.app_db_user,
            "master_key_fingerprint": master_key, "buckets": buckets, "database": state,
            "objects": totals, "inventory_sha256": inventory, "references": references,
            "consistency": "operator-quiesced; exported PostgreSQL snapshot; conditional S3 reads; stable inventory",
            "exclusions": ["configuration and plaintext keys (external escrow)", "Redis cache/queue",
                           "historical S3 versions, deleted originals and unfinished multipart uploads",
                           "cluster-global roles/passwords, tablespaces and other databases"],
            "files": {name: await asyncio.to_thread(descriptor, root / name)
                      for name in ("database.dump", "objects.jsonl", "references.jsonl")}}
        # A missing reference fails before the completion marker exists.
        await asyncio.to_thread(verify, root, manifest=manifest)
        with private_file(root / "manifest.json") as stream:
            stream.write(canonical(manifest) + b"\n")
        for directory in [*(root / "objects" / role for role in ROLES), root / "objects", root, root.parent]:
            fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try: os.fsync(fd)
            finally: os.close(fd)
        return manifest
    finally:
        await connection.close()


async def restore(settings, s3, path, *, isolated=False, trusted=False, key_escrow=False):
    if not isolated or not trusted or not key_escrow:
        raise RecoveryError("Confirma un destino vacío/aislado, una copia de confianza y la clave custodiada.")
    manifest = await asyncio.to_thread(verify, path)
    buckets = bucket_map(settings)
    if (not all(re.fullmatch(r"gensuite-restore-[a-z0-9-]+", name) for name in buckets.values())
            or set(buckets.values()) & set(manifest["buckets"].values())):
        raise RecoveryError("Usa buckets nuevos gensuite-restore-*, distintos de los de origen.")
    if key_fingerprint(settings) != manifest["master_key_fingerprint"]:
        raise RecoveryError("La clave maestra no corresponde a esta copia; no se escribe en el destino.")
    if manifest["database"]["revisions"] not in (["0044"], ["0045"], ["0046"], ["0047"], ["0048"], ["0049"], ["0050"], ["0051"], ["0052"], ["0053"]):
        raise RecoveryError("Esta versión solo restaura copias de los esquemas 0044 a 0053.")
    connection = await database.connect(settings)
    try:
        target = await database.require_empty_target(connection, settings, manifest)
        await asyncio.to_thread(objects.require_empty_buckets, s3, buckets)
        await database.prepare_role(connection, settings)
        await asyncio.to_thread(database.pg_command, settings, "pg_restore", Path(path) / "database.dump")
        extended = manifest["format"] == FORMAT
        async with connection.transaction(isolation="repeatable_read", readonly=True):
            actual = await database.metadata(connection, settings.app_db_user, extended=extended)
        if not metadata_matches(actual, manifest["database"]):
            raise RecoveryError("El esquema, contenido, triggers, recuentos, secuencias, ACL o políticas restaurados difieren de la copia.")
        await asyncio.to_thread(objects.upload_objects, s3, buckets, path)
        await database.require_quiet(connection)
        changes = await database.quarantine(connection, buckets, manifest)
        checks = await database.check_rls(connection, settings.app_db_user)
        return {"format": "gen-suite-restore-report/v1", "backup_id": manifest["id"], "target": target,
                "database_verified": True, "object_readback_verified": True, "rls_checks": checks,
                "row_data_verified": extended, "triggers_verified": extended,
                "row_tables_verified": len(actual.get("row_integrity", {}).get("tables", {})),
                "warnings": [] if extended else ["Copia v1: se verifican esquema y recuentos, pero no hay huellas por tabla ni catálogo de triggers. Requiere comprobación adicional del contenido."],
                "quarantine": changes, "app_role_login": False, "production_ready": False,
                "next": "Mantén la instalación aislada. Revisa copias, credenciales, permisos, fuentes y trabajos antes de habilitarla."}
    finally:
        await connection.close()
