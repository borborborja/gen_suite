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
                   OR (status='queued'  AND created_at < now() - interval '{QUEUED_STALE_MIN} minutes')
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
    """Blocking: pg_dump the whole DB (custom format) and upload it to the private bucket under
    ``_backups/``, then prune old dumps beyond the retention. Returns the key written, or None if off.
    Reuses the configured S3 client (so backups land in whatever S3 the app is pointed at)."""
    import subprocess
    from datetime import datetime, timezone
    from ..core import storage

    cmd = [
        "pg_dump", "-Fc", "--no-owner", "--no-privileges",
        "-h", settings.postgres_host, "-p", str(settings.postgres_port),
        "-U", settings.postgres_user, "-d", settings.postgres_db,
    ]
    env = {"PGPASSWORD": settings.postgres_password, "PATH": "/usr/bin:/usr/local/bin:/bin"}
    proc = subprocess.run(cmd, capture_output=True, env=env, timeout=3600)
    if proc.returncode != 0:
        raise RuntimeError(f"pg_dump failed: {proc.stderr.decode(errors='replace')[:300]}")
    dump = proc.stdout
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    bucket = settings.minio_bucket_private
    key = f"_backups/gensuite-{stamp}.dump"
    s3 = storage._s3()
    s3.put_object(Bucket=bucket, Key=key, Body=dump, ContentType="application/octet-stream")
    # prune: keep the N most recent (names sort chronologically by the YYYYMMDD-HHMMSS stamp)
    try:
        objs = s3.list_objects_v2(Bucket=bucket, Prefix="_backups/").get("Contents", [])
        old = sorted((o["Key"] for o in objs), reverse=True)[settings.backup_retention:]
        if old:
            s3.delete_objects(Bucket=bucket, Delete={"Objects": [{"Key": k} for k in old]})
    except Exception:
        pass
    return key


async def backup_db_to_s3(ctx) -> None:
    """Daily Postgres → S3 backup (gated by ``BACKUP_TO_S3``). The DB itself can't live on S3, but a
    dump there is durable, off-host and cheap. Restore with ``pg_restore`` (see DEPLOY.md)."""
    if not settings.backup_to_s3:
        return
    import asyncio
    try:
        key = await asyncio.to_thread(_pg_dump_to_s3)
        print(f"backup: wrote {key}")
    except Exception as exc:
        print(f"backup: FAILED — {exc}")
