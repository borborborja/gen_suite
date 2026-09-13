"""Operator PostgreSQL snapshot/restore. Never used by tenant HTTP sessions or workers."""
from __future__ import annotations

from pathlib import Path
import hashlib
import re
import subprocess

import asyncpg

from .archive import RecoveryError, canonical, private_file


def identifier(value):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", value):
        raise RecoveryError("Nombre de base de datos, rol o tabla no admitido para recuperación.")
    return '"' + value + '"'


async def connect(settings):
    identifier(settings.postgres_db); identifier(settings.postgres_user); identifier(settings.app_db_user)
    return await asyncpg.connect(host=settings.postgres_host, port=settings.postgres_port,
        user=settings.postgres_user, password=settings.postgres_password, database=settings.postgres_db,
        server_settings={"application_name": "gensuite-recovery"}, command_timeout=3600)


async def require_quiet(connection):
    await connection.execute("SELECT pg_stat_clear_snapshot()")
    if await connection.fetchval("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND backend_type='client backend'"):
        raise RecoveryError("Hay otras conexiones a PostgreSQL. Detén API, worker, importadores y otros clientes.")


async def identity(connection):
    return {"system_identifier": str(await connection.fetchval("SELECT system_identifier FROM pg_control_system()")),
            "database": await connection.fetchval("SELECT current_database()"),
            "server_major": int(await connection.fetchval("SHOW server_version_num")) // 10000}


ROW_HASH_ALGORITHM = "sha256-pg16-jsonb-text-framed/v1"


async def row_fingerprints(connection, tables):
    """Hash the full multiset of rows, one cursor batch at a time, without PK assumptions.

    Canonical output settings and C collation make timestamps, intervals, floats and
    row ordering independent of session timezone/locale. Sorting can spill to PG disk;
    Python keeps a batch, not the full table. Length framing preserves multiplicity.
    Must be run as the backup owner, inside the same snapshot used by pg_dump.
    """
    result = {}
    async with connection.transaction():
        await connection.execute("SET LOCAL TimeZone='UTC'; SET LOCAL DateStyle='ISO, YMD'; SET LOCAL IntervalStyle='iso_8601'; SET LOCAL extra_float_digits=3; SET LOCAL bytea_output='hex'")
        for table in tables:
            digest, count = hashlib.sha256(), 0
            async for row in connection.cursor(
                    f'SELECT to_jsonb(t)::text AS payload FROM public.{identifier(table)} t ORDER BY to_jsonb(t)::text COLLATE "C"', prefetch=64):
                payload = row["payload"].encode("utf-8")
                digest.update(len(payload).to_bytes(8, "big")); digest.update(payload)
                count += 1
            result[table] = {"rows": count, "sha256": digest.hexdigest()}
    return {"algorithm": ROW_HASH_ALGORITHM, "tables": result}


async def metadata(connection, app_role, *, extended=True):
    tables = await connection.fetch("SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p') ORDER BY c.relname")
    counts = {row["relname"]: await connection.fetchval(f"SELECT count(*) FROM public.{identifier(row['relname'])}") for row in tables}
    policies = await connection.fetch("SELECT tablename,policyname,permissive,roles,cmd,qual,with_check FROM pg_policies WHERE schemaname='public' ORDER BY tablename,policyname")
    grants = await connection.fetch("SELECT table_name,privilege_type,is_grantable FROM information_schema.role_table_grants WHERE table_schema='public' AND grantee=$1 ORDER BY table_name,privilege_type", app_role)
    revisions = await connection.fetch("SELECT version_num FROM alembic_version ORDER BY version_num")
    sequences = {}
    for row in await connection.fetch("SELECT sequencename FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename"):
        sequences[row["sequencename"]] = dict(await connection.fetchrow(f"SELECT last_value,is_called FROM public.{identifier(row['sequencename'])}"))
    # PostgreSQL's internal "char" type is returned as bytes by asyncpg. Cast these
    # specific catalog fields to text, rather than coercing arbitrary manifest data.
    columns = await connection.fetch("SELECT c.relname,a.attname,format_type(a.atttypid,a.atttypmod) AS type,a.attnotnull,a.attidentity::text AS attidentity,a.attgenerated::text AS attgenerated,pg_get_expr(d.adbin,d.adrelid) AS default_expr FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum WHERE n.nspname='public' AND c.relkind IN ('r','p') AND a.attnum>0 AND NOT a.attisdropped ORDER BY c.relname,a.attnum")
    constraints = await connection.fetch("SELECT c.relname,k.conname,pg_get_constraintdef(k.oid,true) AS definition FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' ORDER BY c.relname,k.conname")
    indexes = await connection.fetch("SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='public' ORDER BY tablename,indexname")
    functions = await connection.fetch("SELECT p.proname,pg_get_function_identity_arguments(p.oid) AS args,pg_get_functiondef(p.oid) AS definition FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.prokind IN ('f','p') ORDER BY p.proname,pg_get_function_identity_arguments(p.oid)")
    extensions = await connection.fetch("SELECT extname,extversion FROM pg_extension ORDER BY extname")
    result = {"counts": counts, "rls": [dict(row) for row in tables], "policies": [dict(row) for row in policies],
            "grants": [dict(row) for row in grants], "revisions": [row["version_num"] for row in revisions], "sequences": sequences,
            "columns": [dict(row) for row in columns], "constraints": [dict(row) for row in constraints],
            "indexes": [dict(row) for row in indexes], "functions": [dict(row) for row in functions], "extensions": [dict(row) for row in extensions]}
    if extended:
        triggers = await connection.fetch("SELECT c.relname,t.tgname,t.tgenabled::text AS enabled,pg_get_triggerdef(t.oid,true) AS definition FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND NOT t.tgisinternal ORDER BY c.relname,t.tgname")
        result["triggers"] = [dict(row) for row in triggers]
        result["row_integrity"] = await row_fingerprints(connection, counts)
        if any(result["row_integrity"]["tables"][table]["rows"] != count for table, count in counts.items()):
            raise RecoveryError("Los datos cambiaron durante la verificación; no hay una instantánea estable.")
    return result


def pg_command(settings, action, path, *, snapshot=None):
    identifier(settings.postgres_db); identifier(settings.postgres_user)
    if action not in ("pg_dump", "pg_restore"):
        raise RecoveryError("Operación PostgreSQL no admitida.")
    environment = {"PGPASSWORD": settings.postgres_password, "PGCONNECT_TIMEOUT": "15", "PATH": "/usr/bin:/usr/local/bin:/bin"}
    version = subprocess.run([action, "--version"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             text=True, timeout=15, env=environment)
    if version.returncode or not re.search(r"\(PostgreSQL\) 16\.", version.stdout):
        raise RecoveryError("Usa los clientes PostgreSQL 16 de la imagen backend para esta recuperación.")
    command = [action, "-h", settings.postgres_host, "-p", str(settings.postgres_port),
               "-U", settings.postgres_user, "-d", settings.postgres_db, "--no-password"]
    if action == "pg_dump":
        command += ["--format=custom", "--snapshot", snapshot, "--file", str(path)]
    elif action == "pg_restore":
        command += ["--no-owner", "--exit-on-error", "--single-transaction", str(path)]
    else:
        raise RecoveryError("Operación PostgreSQL no admitida.")
    # No connection URI, password in argv, shell, --clean, --create or unbounded stdout.
    result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, timeout=24 * 3600,
        env=environment)
    if result.returncode:
        raise RecoveryError("Falló la operación PostgreSQL. No se publica una copia/restauración como completada.")


REFERENCES = """
SELECT d.storage_bucket AS bucket,p.storage_key AS key,p.checksum AS sha256,p.byte_size::bigint AS size,'page' AS kind
  FROM pages p JOIN documents d ON d.id=p.document_id WHERE NOT p.image_purged
UNION ALL SELECT storage_bucket,storage_prefix || 'original.pdf',fingerprint,NULL::bigint,'original_pdf'
  FROM documents WHERE doc_type='pdf' AND image_policy='retain'
UNION ALL SELECT d.storage_bucket,e.storage_key,e.checksum,e.byte_size,'document_export'
  FROM document_exports e JOIN documents d ON d.id=e.document_id
UNION ALL SELECT $1,storage_key,NULL,NULL::bigint,'person_media' FROM person_media
UNION ALL SELECT $1,result_key,result_checksum,NULL::bigint,'ai_response' FROM ai_calls WHERE result_key IS NOT NULL
UNION ALL SELECT $1,storage_key,checksum,byte_size,'ingest_original' FROM ingest_files WHERE storage_key IS NOT NULL
UNION ALL SELECT $1,p.value->>'key',p.value->>'sha256',(p.value->>'size')::bigint,'ingest_part'
  FROM ingest_files f CROSS JOIN LATERAL jsonb_each(f.parts) p
"""


async def save_references(connection, buckets, root):
    labels = {bucket: role for role, bucket in buckets.items()}
    count = 0
    query = REFERENCES
    if await connection.fetchval("SELECT to_regclass('public.tree_exports') IS NOT NULL"):
        query += " UNION ALL SELECT $1,storage_key,checksum,byte_size,'tree_export' FROM tree_exports"
    if await connection.fetchval("SELECT to_regclass('public.tree_imports') IS NOT NULL"):
        query += " UNION ALL SELECT $1,storage_key,checksum,byte_size,'tree_import' FROM tree_imports"
    with private_file(Path(root) / "references.jsonl") as stream:
        async for row in connection.cursor(query, buckets["private"], prefetch=128):
            value = dict(row)
            if value.pop("bucket") not in labels:
                raise RecoveryError("PostgreSQL referencia un bucket no incluido en esta copia.")
            value["role"] = labels[row["bucket"]]
            stream.write(canonical(value) + b"\n"); count += 1
    return count


async def require_empty_target(connection, settings, manifest):
    source = manifest["source"]
    target = await identity(connection)
    if (not re.fullmatch(r"gensuite_restore_[a-z0-9_]+", settings.postgres_db)
            or target["system_identifier"] == source["system_identifier"]
            or target["server_major"] != source["server_major"]):
        raise RecoveryError("Restaura en otro clúster PostgreSQL de la misma versión mayor y BD gensuite_restore_*.")
    if settings.postgres_user != manifest["owner_role"] or settings.app_db_user != manifest["app_role"]:
        raise RecoveryError("Conserva los nombres de los roles para restaurar las ACL sin alterarlas.")
    await require_quiet(connection)
    if await connection.fetchval("SELECT count(*) FROM pg_database WHERE NOT datistemplate AND datname NOT IN ('postgres',current_database())"):
        raise RecoveryError("El clúster de ensayo contiene otras bases de datos.")
    if await connection.fetchval("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%' AND c.relkind IN ('r','p','v','m','S','f')"):
        raise RecoveryError("La base de restauración ya contiene objetos; no se sobrescribe.")
    if await connection.fetchval("SELECT count(*) FROM pg_extension WHERE extname<>'plpgsql'"):
        raise RecoveryError("La base destino debe estar vacía, sin extensiones de aplicación.")
    if await connection.fetchval("SELECT count(*) FROM pg_namespace WHERE nspname NOT IN ('public','information_schema') AND nspname NOT LIKE 'pg_%'"):
        raise RecoveryError("El destino contiene esquemas adicionales.")
    if await connection.fetchval("SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public'"):
        raise RecoveryError("El destino contiene funciones de aplicación.")
    role = await connection.fetchrow("SELECT rolsuper,rolbypassrls,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=$1", settings.app_db_user)
    if role and any(role.values()):
        raise RecoveryError("El rol de aplicación del destino tiene privilegios elevados.")
    if await connection.fetchval("SELECT count(*) FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname=$1)", settings.app_db_user):
        raise RecoveryError("El rol de aplicación no debe heredar otros roles en el destino.")
    return target


async def prepare_role(connection, settings):
    role = identifier(settings.app_db_user)
    if not await connection.fetchval("SELECT 1 FROM pg_roles WHERE rolname=$1", settings.app_db_user):
        await connection.execute(f"CREATE ROLE {role} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS")
    else:
        await connection.execute(f"ALTER ROLE {role} NOLOGIN")
    # Keep the role disabled after restoration; enabling it is an operator decision.
    # Passwords are supplied by isolated deployment configuration, never from the archive.


async def quarantine(connection, buckets, manifest):
    result = {}
    async with connection.transaction():
        for role in ("private", "public"):
            result[f"bucket_{role}"] = await connection.execute("UPDATE documents SET storage_bucket=$1 WHERE storage_bucket=$2", buckets[role], manifest["buckets"][role])
        if manifest["database"]["revisions"] in (["0046"], ["0047"], ["0048"], ["0049"], ["0050"], ["0051"], ["0052"], ["0053"]):
            # Restoring current objects creates new destination version IDs. Preserve old
            # receipts but require a fresh inventory epoch before any cleanup can resume.
            result["cleanup_inventories"] = await connection.execute("UPDATE ingest_cleanups SET bucket=$1,inventory_epoch=inventory_epoch+1,phase='verify_originals',verified_after=NULL,inventory_cursor='{}',error='Restauración aislada: inventariar de nuevo antes de limpiar' WHERE phase<>'completed'", buckets["private"])
        if manifest["database"]["revisions"] in (["0047"], ["0048"], ["0049"], ["0050"], ["0051"], ["0052"], ["0053"]):
            # A restore copies current payloads, not old source version IDs or unfinished
            # multipart uploads. Keep reports as history, never as a destination baseline.
            result["storage_inventories"] = await connection.execute("UPDATE storage_scans SET invalidated_at=now(),error='Inventario histórico del origen: inicia otro en el destino restaurado'")
        if manifest["database"]["revisions"] in (["0048"], ["0049"], ["0050"], ["0051"], ["0052"], ["0053"]):
            result["storage_writes"] = await connection.execute("UPDATE storage_writes SET state='quarantined',error='Recibo del origen restaurado; no autoriza escribir en el destino' WHERE state IN ('reserved','sending','unknown')")
            result["storage_quotas"] = await connection.execute("UPDATE storage_quotas SET phase='uninitialized',location_hash=NULL,scan_id=NULL,baseline_at=NULL,reserved_bytes=0")
        for name, sql in (
            ("jobs", "UPDATE jobs SET status='paused',error='Restauración aislada: revisión necesaria antes de reanudar',finished_at=now() WHERE status IN ('running','queued')"),
            ("research_runs", "UPDATE research_runs SET status='paused',version=version+1 WHERE status='running'"),
            ("research_run_stages", "UPDATE research_run_stages SET status='paused' WHERE status='running'"),
            ("ai_calls", "UPDATE ai_calls SET state='unknown',error_code='restored_inflight' WHERE state='inflight'"),
            ("providers", "UPDATE provider_credentials SET is_active=false WHERE is_active"),
            ("budgets", "UPDATE tenants SET monthly_budget_cents=NULL WHERE monthly_budget_cents IS NOT NULL"),
        ):
            result[name] = await connection.execute(sql)
    return result


async def check_rls(connection, app_role):
    tables = await connection.fetch("SELECT c.table_name,EXISTS(SELECT 1 FROM information_schema.columns v WHERE v.table_schema='public' AND v.table_name=c.table_name AND v.column_name='visibility') AS has_visibility FROM information_schema.columns c JOIN pg_class r ON r.relname=c.table_name JOIN pg_namespace n ON n.oid=r.relnamespace AND n.nspname=c.table_schema WHERE c.table_schema='public' AND c.column_name='tenant_id' AND r.relrowsecurity ORDER BY c.table_name")
    tenants = await connection.fetch("SELECT id FROM tenants ORDER BY id LIMIT 2")
    # A random tenant additionally verifies empty-context isolation on single-tenant copies.
    import uuid
    checked = 0
    for tenant in [row["id"] for row in tenants] + [uuid.uuid4()]:
        positive_tables = {"persons", "names", "families", "documents", "pages", "transcriptions", "tree_exports", "tree_imports", "source_documents",
                           "research_sources", "citations", "research_runs", "embedding_items", "ai_calls", "ingest_sessions"}
        own_counts = {table["table_name"]: await connection.fetchval(
            f"SELECT count(*) FROM public.{identifier(table['table_name'])} WHERE tenant_id=$1", tenant)
            for table in tables if table["table_name"] in positive_tables}
        async with connection.transaction():
            await connection.execute(f"SET LOCAL ROLE {identifier(app_role)}")
            await connection.execute("SELECT set_config('app.tenant_id',$1,true),set_config('app.user_id','',true),set_config('app.is_server_admin','false',true),set_config('app.user_role','tenant_admin',true)", str(tenant))
            for table in tables:
                condition = " AND visibility IS DISTINCT FROM 'public'" if table["has_visibility"] else ""
                if await connection.fetchval(f"SELECT count(*) FROM public.{identifier(table['table_name'])} WHERE tenant_id<>$1{condition}", tenant):
                    raise RecoveryError("La comprobación RLS del destino encontró datos privados de otro espacio.")
                checked += 1
                if table["table_name"] in own_counts:
                    own = await connection.fetchval(f"SELECT count(*) FROM public.{identifier(table['table_name'])} WHERE tenant_id=$1", tenant)
                    if own != own_counts[table["table_name"]]:
                        raise RecoveryError("El rol de aplicación no puede leer toda su evidencia propia restaurada.")
                    checked += 1
    return checked
