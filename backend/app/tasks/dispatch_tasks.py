"""Retry queue publication after a committed database transaction, including Redis outages."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from ..core.dispatch import DISPATCH_FUNCTIONS, queue_options
from ..core.queue import get_queue
from ..models.job import Job
from ..models.task_dispatch import TaskDispatch
from ..settings import settings


async def dispatch_pending_tasks(ctx) -> None:
    # Cross-tenant maintenance, like the existing reaper. Task payloads carry an explicit
    # tenant; actual work still uses the non-superuser connection and transaction-local RLS.
    engine = create_async_engine(settings.admin_database_url)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            for _ in range(32):
                now = datetime.now(timezone.utc)
                intent = await session.scalar(select(TaskDispatch).where(
                    TaskDispatch.status == "pending", TaskDispatch.available_at <= now,
                ).order_by(TaskDispatch.available_at, TaskDispatch.job_id)
                    .with_for_update(skip_locked=True).limit(1))
                if intent is None:
                    break
                job = await session.get(Job, intent.job_id)
                if job is None or job.status != "queued":
                    intent.status = "cancelled" if job is None or job.status == "cancelled" else "dispatched"
                elif intent.function not in DISPATCH_FUNCTIONS:
                    intent.status = "error"
                    job.status = "error"
                    job.error = "Tipo de tarea no registrado"
                    job.finished_at = now
                else:
                    intent.attempts += 1
                    try:
                        async with asyncio.timeout(10):
                            queue = await get_queue()
                            await queue.enqueue_job(intent.function,
                                _job_id=(f"durable:{intent.job_id}:retry:{intent.generation}" if intent.generation
                                         else f"durable:{intent.job_id}"),
                                **queue_options(intent.function), **intent.payload)
                        intent.status = "dispatched"
                        intent.dispatched_at = now
                        intent.error = None
                    except Exception:
                        # No raw exception text: Redis URLs/errors may contain credentials.
                        intent.error = "Cola no disponible; se reintentará sin perder la tarea"
                        intent.available_at = now + timedelta(seconds=min(300, 2 ** min(intent.attempts, 8)))
                await session.commit()
    finally:
        await engine.dispose()
