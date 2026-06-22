from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.queue import get_queue
from ...models.document import Document
from ...models.job import Job
from ...models.record import Record


async def create_job(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    created_by: uuid.UUID,
    *,
    document_id: uuid.UUID,
    override: dict | None = None,
    modality: str = "sync",
) -> Job:
    if not await session.get(Document, document_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    # don't pile up duplicate jobs on the same book (double-click / impatient retry)
    from ..jobs.service import active_job_for
    existing = await active_job_for(session, tenant_id, "extraction", document_id)
    if existing:
        return existing
    from ..providers.service import assert_within_budget
    await assert_within_budget(session, tenant_id)
    override = override or {}
    job = Job(
        tenant_id=tenant_id, type="extraction", status="queued",
        params={"document_id": str(document_id), "override": override, "modality": modality},
        created_by=created_by,
    )
    session.add(job)
    await session.commit()  # durable before enqueue so the worker can't race ahead of the row

    queue = await get_queue()
    await queue.enqueue_job(
        "extract_records", job_id=str(job.id), tenant_id=str(tenant_id),
        document_id=str(document_id), override=override, options={"modality": modality},
    )
    return job


async def create_embed_mentions_job(
    session: AsyncSession, tenant_id: uuid.UUID, created_by: uuid.UUID, *, document_id: uuid.UUID
) -> Job:
    if not await session.get(Document, document_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    job = Job(
        tenant_id=tenant_id, type="embed_mentions", status="queued",
        params={"document_id": str(document_id)}, created_by=created_by,
    )
    session.add(job)
    await session.commit()  # durable before enqueue
    queue = await get_queue()
    await queue.enqueue_job(
        "embed_mentions", job_id=str(job.id), tenant_id=str(tenant_id), document_id=str(document_id)
    )
    return job


async def reextract_transcription(
    session: AsyncSession, tenant_id: uuid.UUID, created_by: uuid.UUID, *, transcription_id: uuid.UUID
) -> Job:
    """Re-extract a single corrected transcription: mark its existing records superseded (kept for
    history) and enqueue extraction for the document — the anti-join now reprocesses that page."""
    from ...models.record import Record
    from ...models.transcription import Transcription
    from sqlalchemy import or_, update

    t = await session.get(Transcription, transcription_id)
    if not t:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "transcription not found")
    # Supersede records starting OR ending on this page — a cross-page entry whose second sheet was
    # corrected must be re-stitched, not left stale.
    await session.execute(
        update(Record).where(
            or_(
                Record.transcription_id == transcription_id,
                Record.transcription_end_id == transcription_id,
            )
        ).values(status="superseded")
    )
    job = Job(
        tenant_id=tenant_id, type="extraction", status="queued",
        params={"document_id": str(t.document_id), "override": {}}, created_by=created_by,
    )
    session.add(job)
    await session.commit()  # durable before enqueue
    queue = await get_queue()
    await queue.enqueue_job(
        "extract_records", job_id=str(job.id), tenant_id=str(tenant_id),
        document_id=str(t.document_id), override={},
    )
    return job


async def cancel_job(session: AsyncSession, job_id: uuid.UUID) -> Job:
    job = await session.get(Job, job_id)
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
    if job.status in ("queued", "running"):
        job.status = "cancelled"
    return job


async def list_for_document(session: AsyncSession, document_id: uuid.UUID) -> list[Record]:
    return list(
        (
            await session.scalars(
                select(Record)
                .where(Record.document_id == document_id)
                .order_by(Record.date_year)
            )
        ).all()
    )


_ACTIVE = ("extracted", "needs_review", "reviewed")


async def merge_with_next(session: AsyncSession, tenant_id: uuid.UUID, record_id: uuid.UUID) -> Record:
    """Manually join a record with the first active record on the following page into one spanning
    record (safety net when auto-stitch missed a split entry). Reuses the extraction boundary merge."""
    from ...models.document import Page
    from ...models.transcription import Transcription
    from ...modules.providers.service import ProviderService
    from ...modules.extraction.schemas import ExtractedPage
    from ...tasks.extraction_tasks import merge_boundary_records

    rec = await session.get(Record, record_id)
    if not rec or rec.status not in _ACTIVE:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "record not found")
    if rec.is_continued:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "el registro ya abarca dos hojas")
    start_no = await session.scalar(select(Page.page_no).where(Page.id == rec.page_id))
    if start_no is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "el registro no está ligado a una página")
    next_pid = await session.scalar(
        select(Page.id).where(Page.document_id == rec.document_id, Page.page_no == start_no + 1)
    )
    if not next_pid:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no hay página siguiente")
    nexts = (await session.scalars(
        select(Record).where(
            Record.document_id == rec.document_id, Record.page_id == next_pid,
            Record.status.in_(_ACTIVE),
        )
    )).all()
    if not nexts:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no hay registro en la página siguiente")
    end_rec = sorted(nexts, key=lambda r: (r.raw_json or {}).get("_pos", 0))[0]

    rc = await ProviderService(session).resolve(tenant_id=tenant_id, task_type="inference")
    texts = dict(
        (await session.execute(
            select(Transcription.page_no, Transcription.text).where(
                Transcription.document_id == rec.document_id,
                Transcription.page_no.in_([start_no, start_no + 1]),
                Transcription.is_active.is_(True),
            )
        )).all()
    )
    schema = ExtractedPage.model_json_schema()
    return await merge_boundary_records(
        session, tenant_id=tenant_id, document_id=rec.document_id, rc=rc, schema=schema,
        start_rec=rec, end_rec=end_rec,
        start_text=texts.get(start_no, ""), end_text=texts.get(start_no + 1, ""),
    )


async def split_record(session: AsyncSession, tenant_id: uuid.UUID, record_id: uuid.UUID) -> Record:
    """Unlink a spanning record from its second page: keep it on its start page and mark it so the
    auto-stitch pass won't re-merge this boundary."""
    rec = await session.get(Record, record_id)
    if not rec:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "record not found")
    rec.is_continued = False
    rec.page_end_id = None
    rec.transcription_end_id = None
    attrs = dict(rec.attributes or {})
    attrs["manual_split"] = True
    rec.attributes = attrs
    await session.flush()
    return rec
