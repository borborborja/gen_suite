from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from datetime import datetime, timezone

from ...core import events
from ...models.job import Job
from .constants import ACTIVE_STATUSES, JobStatus


async def list_jobs(session: AsyncSession) -> list[Job]:
    return list((await session.scalars(select(Job).order_by(Job.created_at.desc()))).all())


async def get_job(session: AsyncSession, job_id: uuid.UUID) -> Job:
    job = await session.get(Job, job_id)
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
    return job


async def cancel_job(session: AsyncSession, job_id: uuid.UUID) -> Job:
    """Cancel/dismiss a job, uniformly: mark it cancelled with a finished_at + default error, and
    publish the terminal SSE event so any open stream closes instead of waiting for the poll.
    A still-active worker stops at its next checkpoint; a stuck/orphaned job is simply marked done
    so it leaves the activity list. Idempotent on already-terminal jobs."""
    job = await get_job(session, job_id)
    if job.status in ACTIVE_STATUSES:
        job.status = JobStatus.CANCELLED
        job.finished_at = datetime.now(timezone.utc)
        if not job.error:
            job.error = "Cancelada por el usuario"
        await session.flush()
        await events.publish(job.tenant_id, job.id, {"kind": "cancelled", "error": job.error})
    return job


async def active_job_for(
    session: AsyncSession, tenant_id: uuid.UUID, job_type: str, entity_id: uuid.UUID | str,
    *, param_key: str = "document_id",
) -> Job | None:
    """The existing queued/running job of ``job_type`` for this entity, if any — so callers can
    return it instead of enqueuing a duplicate (a user double-clicking 'Extraer' shouldn't pile up
    three jobs on the same book). Matched on ``params->>param_key`` (document_id, person_id…)."""
    return await session.scalar(
        select(Job).where(
            Job.tenant_id == tenant_id, Job.type == job_type,
            Job.status.in_(ACTIVE_STATUSES),
            Job.params[param_key].astext == str(entity_id),
        ).order_by(Job.created_at.desc()).limit(1)
    )


async def active_job_of_type(
    session: AsyncSession, tenant_id: uuid.UUID, job_type: str
) -> Job | None:
    """The existing queued/running job of ``job_type`` for the tenant, regardless of params —
    dedup for tenant-wide jobs (reconstruction, reembed_corpus…)."""
    return await session.scalar(
        select(Job).where(
            Job.tenant_id == tenant_id, Job.type == job_type,
            Job.status.in_(ACTIVE_STATUSES),
        ).order_by(Job.created_at.desc()).limit(1)
    )
