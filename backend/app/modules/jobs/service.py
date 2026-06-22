from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.job import Job


async def list_jobs(session: AsyncSession) -> list[Job]:
    return list((await session.scalars(select(Job).order_by(Job.created_at.desc()))).all())


async def get_job(session: AsyncSession, job_id: uuid.UUID) -> Job:
    job = await session.get(Job, job_id)
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
    return job


async def active_job_for(
    session: AsyncSession, tenant_id: uuid.UUID, job_type: str, document_id: uuid.UUID | str
) -> Job | None:
    """The existing queued/running job of ``job_type`` for this document, if any — so callers can
    return it instead of enqueuing a duplicate (a user double-clicking 'Extraer' shouldn't pile up
    three jobs on the same book). Matched on params->>'document_id'."""
    return await session.scalar(
        select(Job).where(
            Job.tenant_id == tenant_id, Job.type == job_type,
            Job.status.in_(("queued", "running")),
            Job.params["document_id"].astext == str(document_id),
        ).order_by(Job.created_at.desc()).limit(1)
    )
