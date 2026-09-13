"""Worker transitions must not resurrect a cancelled or otherwise terminal job."""
from sqlalchemy import select, update

from ..models.job import Job
from ..modules.jobs.constants import ACTIVE_STATUSES


async def job_is_active(session, job_id) -> bool:
    return await session.scalar(select(Job.status).where(Job.id == job_id)) in ACTIVE_STATUSES


async def update_active_job(session, job_id, **values) -> bool:
    changed = await session.execute(update(Job).where(
        Job.id == job_id, Job.status.in_(ACTIVE_STATUSES),
    ).values(**values))
    return bool(changed.rowcount)
