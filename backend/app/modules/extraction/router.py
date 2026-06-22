from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.deps import get_current_principal, get_tenant_db, require_roles
from ...core.security import Principal
from ...models.job import Job
from ...models.membership import MembershipRole
from ...models.mention import PersonMention
from ...models.place import Place
from ..jobs.schemas import JobOut
from . import service

from .record_types import RECORD_TYPES

router = APIRouter(prefix="/extraction", tags=["extraction"])


@router.get("/record-types")
async def record_types() -> list[dict]:
    """The document/record types the uploader can pick (drives the upload form's type dropdown)."""
    return [{"key": k, "label": v["label"], "family": v["family"]} for k, v in RECORD_TYPES.items()]

_WRITE = (MembershipRole.tenant_admin.value, MembershipRole.researcher.value)


class ExtractRequest(BaseModel):
    document_id: uuid.UUID
    engine: str | None = None
    model: str | None = None
    credential_id: uuid.UUID | None = None
    api_key: str | None = None
    base_url: str | None = None
    modality: str = "sync"  # sync | batch (Batch API: async, ~50% cheaper)


class MentionLite(BaseModel):
    role: str
    given: str | None = None
    surname: str | None = None
    name_raw: str | None = None
    sex: str | None = None


class RecordOut(BaseModel):
    id: uuid.UUID
    record_type: str
    date_raw: str | None
    date_year: int | None
    date_month: int | None = None
    date_day: int | None = None
    summary: str | None
    place: str | None = None
    confidence: float | None
    status: str
    page_id: uuid.UUID | None
    page_end_id: uuid.UUID | None = None
    is_continued: bool = False
    record_no: str | None = None
    sequence_warning: str | None = None
    mentions: list[MentionLite] = []


# order people in an act for display: the focal person first, then parents, spouse, godparents, rest
_ROLE_ORDER = {"principal": 0, "head": 0, "testator": 0, "spouse": 1, "father": 2, "mother": 3,
               "godfather": 4, "godmother": 5, "son": 6, "daughter": 6, "child": 6, "sibling": 7,
               "witness": 8, "declarant": 9, "officiant": 9}


def _job_out(job: Job) -> JobOut:
    return JobOut(
        id=job.id, type=job.type, status=job.status, progress=job.progress, result=job.result,
        error=job.error, created_at=job.created_at, started_at=job.started_at,
        finished_at=job.finished_at,
    )


def _override(body: ExtractRequest) -> dict:
    o: dict = {}
    if body.engine:
        o["engine"] = body.engine
    if body.model:
        o["model"] = body.model
    if body.credential_id:
        o["credential_id"] = str(body.credential_id)
    if body.api_key:
        o["api_key"] = body.api_key
    if body.base_url:
        o["base_url"] = body.base_url
    return o


@router.post(
    "/jobs", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_roles(*_WRITE))],
)
async def start_extraction(
    body: ExtractRequest,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> JobOut:
    job = await service.create_job(
        db, principal.tenant_id, principal.user_id,
        document_id=body.document_id, override=_override(body),
        modality=body.modality if body.modality in ("sync", "batch") else "sync",
    )
    return _job_out(job)


@router.post(
    "/reextract/{transcription_id}", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_roles(*_WRITE))],
)
async def reextract(
    transcription_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> JobOut:
    """Re-extract a corrected transcription (supersedes old records, re-runs extraction)."""
    job = await service.reextract_transcription(
        db, principal.tenant_id, principal.user_id, transcription_id=transcription_id
    )
    return _job_out(job)


@router.post(
    "/embed-mentions", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_roles(*_WRITE))],
)
async def embed_mentions(
    body: ExtractRequest,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> JobOut:
    """Embed a document's extracted person-mentions for hybrid retrieval (plan §2/M2)."""
    job = await service.create_embed_mentions_job(
        db, principal.tenant_id, principal.user_id, document_id=body.document_id
    )
    return _job_out(job)


@router.post(
    "/jobs/{job_id}/cancel", response_model=JobOut, dependencies=[Depends(require_roles(*_WRITE))]
)
async def cancel_extraction(
    job_id: uuid.UUID, db: AsyncSession = Depends(get_tenant_db)
) -> JobOut:
    return _job_out(await service.cancel_job(db, job_id))


def _record_out(r, mentions: list[PersonMention], places: dict) -> RecordOut:
    ms = sorted(mentions, key=lambda m: _ROLE_ORDER.get(m.role, 99))
    return RecordOut(
        id=r.id, record_type=r.record_type, date_raw=r.date_raw, date_year=r.date_year,
        date_month=r.date_month, date_day=r.date_day,
        summary=r.summary, place=(places.get(r.place_id) if r.place_id else None) or r.parish_raw,
        confidence=r.confidence, status=r.status, page_id=r.page_id,
        page_end_id=r.page_end_id, is_continued=r.is_continued, record_no=r.record_no,
        sequence_warning=(r.attributes or {}).get("sequence_warning"),
        mentions=[MentionLite(role=m.role, given=m.given, surname=m.surname,
                              name_raw=m.name_raw, sex=m.sex) for m in ms],
    )


@router.get("/documents/{document_id}", response_model=list[RecordOut])
async def document_records(
    document_id: uuid.UUID, db: AsyncSession = Depends(get_tenant_db)
) -> list[RecordOut]:
    records = await service.list_for_document(db, document_id)
    if not records:
        return []
    rids = [r.id for r in records]
    ments = (await db.scalars(select(PersonMention).where(PersonMention.record_id.in_(rids)))).all()
    by_rec: dict = {}
    for m in ments:
        by_rec.setdefault(m.record_id, []).append(m)
    place_ids = {r.place_id for r in records if r.place_id}
    places: dict = {}
    if place_ids:
        places = {pid: name for pid, name in (
            await db.execute(select(Place.id, Place.name).where(Place.id.in_(place_ids)))).all()}
    return [_record_out(r, by_rec.get(r.id, []), places) for r in records]


@router.post("/records/{record_id}/merge-next", response_model=RecordOut,
             dependencies=[Depends(require_roles(*_WRITE))])
async def merge_next(
    record_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> RecordOut:
    """Manually join this record with the first record on the following page into one spanning record
    (when auto-detection missed a split entry)."""
    return _record_out(await service.merge_with_next(db, principal.tenant_id, record_id))


@router.post("/records/{record_id}/split", response_model=RecordOut,
             dependencies=[Depends(require_roles(*_WRITE))])
async def split_record(
    record_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> RecordOut:
    """Unlink a spanning record from its second page (keeps it on its start page; auto-stitch will
    leave it alone afterwards)."""
    return _record_out(await service.split_record(db, principal.tenant_id, record_id))
