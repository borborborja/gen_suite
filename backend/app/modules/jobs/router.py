from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ...core import events
from ...core.deps import get_current_principal, get_tenant_db
from ...core.security import Principal
from ...db.rls import set_rls_context
from ...db.session import SessionLocal
from ...models.job import Job
from . import service
from .constants import TERMINAL_STATUSES, terminal_event
from .schemas import JobOut

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _doc_id(job: Job) -> uuid.UUID | None:
    raw = (job.params or {}).get("document_id") if isinstance(job.params, dict) else None
    try:
        return uuid.UUID(str(raw)) if raw else None
    except (ValueError, TypeError):
        return None


def _out(job: Job) -> JobOut:
    return JobOut(
        id=job.id, type=job.type, status=job.status, progress=job.progress,
        result=job.result, error=job.error, document_id=_doc_id(job), created_at=job.created_at,
        started_at=job.started_at, finished_at=job.finished_at,
    )


@router.get("", response_model=list[JobOut])
async def list_jobs(db: AsyncSession = Depends(get_tenant_db)) -> list[JobOut]:
    return [_out(j) for j in await service.list_jobs(db)]


@router.get("/{job_id}", response_model=JobOut)
async def get_job(job_id: uuid.UUID, db: AsyncSession = Depends(get_tenant_db)) -> JobOut:
    return _out(await service.get_job(db, job_id))


@router.post("/{job_id}/cancel", response_model=JobOut)
async def cancel_job(job_id: uuid.UUID, db: AsyncSession = Depends(get_tenant_db)) -> JobOut:
    return _out(await service.cancel_job(db, job_id))


@router.get("/{job_id}/events")
async def job_events(
    job_id: uuid.UUID, principal: Principal = Depends(get_current_principal)
) -> StreamingResponse:
    """SSE stream of a job's progress. Ownership is checked with a short-lived session so the
    long-lived stream doesn't hold a pooled DB connection."""
    if principal.tenant_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no active tenant")
    already_done: dict | None = None
    async with SessionLocal() as session:
        await set_rls_context(
            session, user_id=principal.user_id, tenant_id=principal.tenant_id, role=principal.role
        )
        job = await session.get(Job, job_id)
        if not job:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
        # If the job already finished, the live pub/sub has nothing to replay — hand the relay a
        # synthetic final event so the client gets closure instead of hanging on keepalives forever.
        if job.status in TERMINAL_STATUSES:
            already_done = terminal_event(job.status, job.progress, job.error)

    tid, uid, role = principal.tenant_id, principal.user_id, principal.role

    async def check_terminal() -> dict | None:
        """Short-lived read of the job's current status — lets the SSE relay close if the job died
        without publishing a final event (worker crash, reaper). No long-held DB connection."""
        async with SessionLocal() as s:
            await set_rls_context(s, user_id=uid, tenant_id=tid, role=role)
            j = await s.get(Job, job_id)
            if not j:
                return terminal_event("error", None, "job gone")
            if j.status in TERMINAL_STATUSES:
                return terminal_event(j.status, j.progress, j.error)
        return None

    return StreamingResponse(
        events.sse_stream(principal.tenant_id, job_id, already_done=already_done,
                          check_terminal=check_terminal),
        media_type="text/event-stream",
    )
