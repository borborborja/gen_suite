from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.deps import get_current_principal, get_tenant_db, require_roles
from ...core.security import Principal
from ...models.membership import MembershipRole
from ..jobs.schemas import JobOut
from . import service
from .schemas import ReconcileRequest, TranscribeRequest, TranscriptionOut, VersionPairOut

router = APIRouter(prefix="/transcription", tags=["transcription"])

_WRITE = (MembershipRole.tenant_admin.value, MembershipRole.researcher.value)


def _job_out(job) -> JobOut:
    return JobOut(
        id=job.id, type=job.type, status=job.status, progress=job.progress, result=job.result,
        error=job.error, created_at=job.created_at, started_at=job.started_at,
        finished_at=job.finished_at,
    )


@router.post(
    "/jobs", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_roles(*_WRITE))],
)
async def start_transcription(
    body: TranscribeRequest,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> JobOut:
    job = await service.create_job(db, principal.tenant_id, principal.user_id, body)
    return _job_out(job)


@router.post(
    "/jobs/{job_id}/cancel", response_model=JobOut, dependencies=[Depends(require_roles(*_WRITE))]
)
async def cancel_transcription(
    job_id: uuid.UUID, db: AsyncSession = Depends(get_tenant_db)
) -> JobOut:
    return _job_out(await service.cancel_job(db, job_id))


@router.patch(
    "/{transcription_id}", response_model=TranscriptionOut,
    dependencies=[Depends(require_roles(*_WRITE))],
)
async def correct_transcription(
    transcription_id: uuid.UUID,
    body: dict,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> TranscriptionOut:
    """Save a human-corrected transcription (eScriptorium-style). Body: {"text": "..."}.
    Re-indexes the page (FTS auto + re-embed)."""
    t = await service.correct_text(
        db, transcription_id, str(body.get("text", "")),
        tenant_id=principal.tenant_id, created_by=principal.user_id,
    )
    return TranscriptionOut(
        id=t.id, page_no=t.page_no, engine=t.engine, model=t.model, text=t.text, status=t.status
    )


@router.get("/documents/{document_id}", response_model=list[TranscriptionOut])
async def document_transcriptions(
    document_id: uuid.UUID, db: AsyncSession = Depends(get_tenant_db)
) -> list[TranscriptionOut]:
    return [
        TranscriptionOut(
            id=t.id, page_no=t.page_no, engine=t.engine, model=t.model, text=t.text, status=t.status
        )
        for t in await service.list_for_document(db, document_id)
    ]


def _tout(t) -> TranscriptionOut | None:
    if t is None:
        return None
    return TranscriptionOut(
        id=t.id, page_no=t.page_no, engine=t.engine, model=t.model, text=t.text, status=t.status)


@router.get("/documents/{document_id}/versions", response_model=list[VersionPairOut])
async def document_versions(
    document_id: uuid.UUID, db: AsyncSession = Depends(get_tenant_db)
) -> list[VersionPairOut]:
    """Per-page active transcription + the candidate from the last re-recognition (for reconciliation)."""
    return [
        VersionPairOut(page_no=p, active=_tout(a), candidate=_tout(c))
        for p, a, c in await service.list_versions(db, document_id)
    ]


@router.post(
    "/documents/{document_id}/reconcile", dependencies=[Depends(require_roles(*_WRITE))]
)
async def reconcile(
    document_id: uuid.UUID, body: ReconcileRequest,
    principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_tenant_db),
) -> dict:
    """Apply a reconciliation (substitute / mix[frequency|llm] / manual) to the re-recognized pages."""
    n = await service.reconcile_versions(
        db, principal.tenant_id, document_id, mode=body.mode, criterion=body.criterion,
        keep_history=body.keep_history, choices=body.choices, created_by=principal.user_id,
    )
    return {"pages": n}
