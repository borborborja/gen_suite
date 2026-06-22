from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.queue import get_queue
from ...models.document import Document
from ...models.job import Job
from ...models.transcription import Transcription
from .schemas import TranscribeRequest


def _build_override(body: TranscribeRequest) -> dict:
    override: dict = {}
    if body.engine:
        override["engine"] = body.engine
    if body.model:
        override["model"] = body.model
    if body.credential_id:
        override["credential_id"] = str(body.credential_id)
    if body.api_key:
        override["api_key"] = body.api_key
    if body.base_url:
        override["base_url"] = body.base_url
    return override


async def create_job(
    session: AsyncSession, tenant_id: uuid.UUID, created_by: uuid.UUID, body: TranscribeRequest
) -> Job:
    if not await session.get(Document, body.document_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    from ..jobs.service import active_job_for
    existing = await active_job_for(session, tenant_id, "transcription", body.document_id)
    if existing:
        return existing
    from ..providers.service import assert_within_budget
    await assert_within_budget(session, tenant_id)

    override = _build_override(body)
    options = {"lang": body.lang, "psm": body.psm, "prompt": body.prompt, "replace": body.replace}
    job = Job(
        tenant_id=tenant_id,
        type="transcription",
        status="queued",
        params={"document_id": str(body.document_id), "override": override, "options": options},
        created_by=created_by,
    )
    session.add(job)
    await session.commit()  # durable before enqueue so the worker can't race ahead of the row

    queue = await get_queue()
    await queue.enqueue_job(
        "transcribe_document",
        job_id=str(job.id),
        tenant_id=str(tenant_id),
        document_id=str(body.document_id),
        override=override,
        options=options,
    )
    return job


async def cancel_job(session: AsyncSession, job_id: uuid.UUID) -> Job:
    job = await session.get(Job, job_id)
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
    if job.status in ("queued", "running"):
        job.status = "cancelled"
    return job


async def correct_text(
    session: AsyncSession, transcription_id: uuid.UUID, text: str,
    *, tenant_id: uuid.UUID | None = None, created_by: uuid.UUID | None = None,
) -> Transcription:
    """Human correction of an HTR transcription (eScriptorium-style loop). Marks it ``corrected``
    so it can be exported as training ground truth and re-extracted. Keeps the original engine/model
    for provenance — we're recording that a human verified/fixed this page.

    Re-indexes the page: the FTS ``tsv`` column is generated from ``text`` so keyword search updates
    automatically; the semantic ``embedding`` is now stale, so we NULL it and enqueue ``embed_document``
    (which re-embeds only the NULL rows — i.e. just this corrected page)."""
    t = await session.get(Transcription, transcription_id)
    if not t:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "transcription not found")
    t.text = text
    t.status = "corrected"
    t.confidence = 1.0
    t.embedding = None  # invalidate the now-stale vector; re-embed below recomputes it
    await session.flush()

    # Re-embed just this page so semantic search reflects the correction (best-effort).
    try:
        job = Job(
            tenant_id=tenant_id or t.tenant_id, type="embedding", status="queued",
            params={"document_id": str(t.document_id)}, created_by=created_by,
        )
        session.add(job)
        await session.commit()  # durable before enqueue
        jid = job.id
        queue = await get_queue()
        await queue.enqueue_job(
            "embed_document", job_id=str(jid), tenant_id=str(tenant_id or t.tenant_id),
            document_id=str(t.document_id),
        )
    except Exception:  # re-embed is best-effort; the text + FTS index are already corrected
        pass
    return t


async def list_for_document(
    session: AsyncSession, document_id: uuid.UUID, *, active_only: bool = True
) -> list[Transcription]:
    stmt = select(Transcription).where(Transcription.document_id == document_id)
    if active_only:
        stmt = stmt.where(Transcription.is_active.is_(True))
    return list((await session.scalars(stmt.order_by(Transcription.page_no))).all())


async def list_versions(
    session: AsyncSession, document_id: uuid.UUID
) -> list[tuple[int, Transcription | None, Transcription | None]]:
    """Per page: (page_no, active, candidate) — candidate = newest inactive re-recognition row."""
    rows = list((await session.scalars(
        select(Transcription).where(Transcription.document_id == document_id)
        .order_by(Transcription.page_no, Transcription.created_at)
    )).all())
    active: dict[int, Transcription] = {}
    cand: dict[int, Transcription] = {}
    for t in rows:
        (active if t.is_active else cand)[t.page_no] = t  # later row wins (newest)
    pages = sorted(set(active) | set(cand))
    return [(p, active.get(p), cand.get(p)) for p in pages]


async def reconcile_versions(
    session: AsyncSession, tenant_id: uuid.UUID, document_id: uuid.UUID, *,
    mode: str, criterion: str | None = None, keep_history: bool = False,
    choices: dict[str, str] | None = None, created_by: uuid.UUID | None = None,
) -> int:
    """Collapse each page's candidate + old active into one active transcription per the chosen mode.
    The candidate row becomes the new active (carrying the final text); the old active is archived
    (kept inactive) when ``keep_history`` else deleted. Re-indexes (FTS auto + re-embed)."""
    from .reconcile import book_frequency, llm_reconcile, merge_by_frequency

    rows = list((await session.scalars(
        select(Transcription).where(Transcription.document_id == document_id)
        .order_by(Transcription.created_at)
    )).all())
    active = {t.page_no: t for t in rows if t.is_active}
    cands: dict[int, list[Transcription]] = {}
    for t in rows:
        if not t.is_active:
            cands.setdefault(t.page_no, []).append(t)
    if not cands:
        return 0

    freq = None
    if mode == "mix" and criterion == "frequency":
        freq = book_frequency([t.text for t in active.values() if t.text])
    rc = None
    if mode == "mix" and criterion == "llm":
        from ..providers.service import ProviderService
        try:
            rc = await ProviderService(session).resolve(tenant_id=tenant_id, task_type="inference")
        except Exception:
            rc = None

    applied = 0
    for page_no, cand_list in cands.items():
        cand_list.sort(key=lambda t: t.created_at)
        c = cand_list[-1]
        for stale in cand_list[:-1]:
            await session.delete(stale)
        a = active.get(page_no)

        if mode == "substitute":
            final = c.text
        elif mode == "manual":
            ch = (choices or {}).get(str(page_no))
            if ch is None:
                continue  # page left undecided → keep candidate inactive
            final = (a.text if a else c.text) if ch == "old" else (c.text if ch == "new" else ch)
        else:  # mix
            if rc is not None:
                final = await llm_reconcile(rc, a.text if a else "", c.text or "")
            elif freq is not None:
                final = merge_by_frequency(a.text if a else "", c.text or "", freq)
            else:
                final = c.text  # graceful fallback

        c.text = final
        c.is_active = True
        c.status = "ok"
        c.embedding = None  # re-embed below
        if a is not None:
            if keep_history:
                a.is_active = False
                a.status = "archived"
            else:
                await session.delete(a)
        applied += 1

    await session.flush()

    # re-index: FTS auto-updates (generated tsv); enqueue embed for the now-active corrected pages.
    try:
        job = Job(tenant_id=tenant_id, type="embedding", status="queued",
                  params={"document_id": str(document_id)}, created_by=created_by)
        session.add(job)
        await session.commit()  # durable before enqueue
        jid = job.id
        queue = await get_queue()
        await queue.enqueue_job(
            "embed_document", job_id=str(jid), tenant_id=str(tenant_id), document_id=str(document_id))
    except Exception:
        pass
    return applied
