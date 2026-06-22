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
