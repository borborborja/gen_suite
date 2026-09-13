"""Periodic maintenance: reap stalled/orphaned jobs so no section's UI ever hangs on a dead job.

A job is "stalled" if it's been ``running`` with no progress (``updated_at``) for a while — the worker
died mid-task, an LLM/network call hung past its retries, or it was orphaned. We fail such jobs (and
publish a terminal event) so the SSE stream closes and the frontend clears the spinner. Also fails
``queued`` jobs that were never picked up. Runs cross-tenant via the owner role (bypasses RLS)."""
from __future__ import annotations

import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from ..settings import settings

RUNNING_STALE_MIN = 30   # a 'running' job with no progress for this long is considered dead
QUEUED_STALE_MIN = 90    # a 'queued' job never picked up for this long is considered dead


async def reap_stale_jobs(ctx) -> None:
    engine = create_async_engine(settings.admin_database_url)
    try:
        async with engine.begin() as conn:
            reaped = (await conn.execute(text(
                f"""
                UPDATE jobs SET status='error', finished_at=now(),
                    error=COALESCE(error, 'Tarea bloqueada o sin actividad — cancelada automáticamente')
                WHERE (status='running' AND updated_at < now() - interval '{RUNNING_STALE_MIN} minutes')
                   OR (status='queued' AND updated_at < now() - interval '{QUEUED_STALE_MIN} minutes'
                       AND NOT EXISTS (SELECT 1 FROM task_dispatches d
                           WHERE d.job_id = jobs.id AND d.status = 'pending'))
                RETURNING id, tenant_id
                """
            ))).all()
    finally:
        await engine.dispose()

    if not reaped:
        return
    # tell any connected client to stop waiting (best-effort; the SSE also closes on terminal DB state)
    try:
        from ..core import events
        for jid, tid in reaped:
            await events.publish(tid, jid, {"kind": "book_fail", "error": "tarea cancelada (sin actividad)"})
    except Exception:
        pass
    print(f"reaper: failed {len(reaped)} stalled job(s)")


def _pg_dump_to_s3() -> str | None:
    """Blocking: upload a custom PostgreSQL dump to the isolated backup bucket.

    Retention is deliberately enforced by the bucket lifecycle/Object Lock policy, not
    by the application credentials.  This leaves a recoverable copy after compromise.
    """
    import subprocess
    import uuid
    from pathlib import Path
    from tempfile import TemporaryDirectory
    from datetime import datetime, timezone
    from ..core import storage

    cmd = [
        "pg_dump", "-Fc", "--no-owner", "--no-privileges", "--no-password",
        "-h", settings.postgres_host, "-p", str(settings.postgres_port),
        "-U", settings.postgres_user, "-d", settings.postgres_db,
    ]
    env = {"PGPASSWORD": settings.postgres_password, "PATH": "/usr/bin:/usr/local/bin:/bin"}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    bucket = settings.backup_s3_bucket
    key = f"_backups/database-only/gensuite-{stamp}-{uuid.uuid4()}.dump"
    s3 = storage.backup_s3()
    from ..recovery.archive import descriptor, private_file
    with TemporaryDirectory(prefix="gensuite-db-backup-") as tmp:
        path = Path(tmp) / "database.dump"
        with private_file(path):
            pass
        proc = subprocess.run(cmd + ["--file", str(path)], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, env=env, timeout=3600)
        if proc.returncode != 0:
            raise RuntimeError("pg_dump no se completó; no se ha publicado una copia válida")
        s3.upload_file(str(path), bucket, key, ExtraArgs={"ContentType": "application/octet-stream"})
        # This explicitly remains database-only. It is not a document recovery set.
        info = {"format": "gen-suite-database-only/v1", "scope": "database-only", "objects_included": False,
                "key": key, **descriptor(path)}
        s3.put_object(Bucket=bucket, Key=key + ".json", Body=json.dumps(info).encode(), ContentType="application/json")
    return key


async def backup_db_to_s3(ctx) -> None:
    """Daily PostgreSQL → isolated S3 backup (gated by ``BACKUP_TO_S3``)."""
    if not settings.backup_to_s3:
        return
    import asyncio
    try:
        key = await asyncio.to_thread(_pg_dump_to_s3)
        print(f"backup: wrote {key}")
    except Exception:
        print("backup: FAILED — comprueba configuración, espacio y disponibilidad; no se muestran errores que puedan contener secretos")
        raise RuntimeError("No se ha completado la copia programada de PostgreSQL") from None
