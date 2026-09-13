from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select, update, or_
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.dispatch import schedule
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
    if not await session.scalar(select(Document).where(
        Document.id == body.document_id, Document.tenant_id == tenant_id,
    ).with_for_update()):
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
    await schedule(session, job, "transcribe_document",
        document_id=str(body.document_id),
        override=override,
        options=options,
    )
    return job


async def cancel_job(session: AsyncSession, job_id: uuid.UUID) -> Job:
    from ..jobs.service import cancel_job as cancel
    return await cancel(session, job_id)


async def correct_text(
    session: AsyncSession, transcription_id: uuid.UUID, text: str,
    *, tenant_id: uuid.UUID | None = None, created_by: uuid.UUID | None = None,
) -> Transcription:
    """Create an immutable human revision; citations keep their original evidence."""
    t = await session.get(Transcription, transcription_id)
    if not t or (tenant_id is not None and t.tenant_id != tenant_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "transcription not found")
    await session.scalar(select(Document.id).where(Document.id == t.document_id).with_for_update())
    await session.refresh(t)
    if not t.is_active:
        raise HTTPException(409, "La transcripción ha cambiado. Recarga la página antes de corregirla.")
    if t.text == text and t.status == "corrected":
        return t
    t.is_active = False
    await session.flush()
    revision = Transcription(
        tenant_id=t.tenant_id, document_id=t.document_id, page_id=t.page_id,
        page_no=t.page_no, visibility=t.visibility, engine="human", model=None,
        text=text, status="corrected", confidence=None, is_active=True,
        supersedes_id=t.id, reviewed_by=created_by,
    )
    session.add(revision)
    await invalidate_evidence(session, t.id)
    await session.flush()
    return revision


async def invalidate_evidence(session, transcription_id):
    """Invalidate derivations, never accepted tree facts or their citations."""
    from ...models.record import Record
    from ...models.match_candidate import MatchCandidate
    records = select(Record.id).where(or_(
        Record.transcription_id == transcription_id,
        Record.transcription_end_id == transcription_id,
    ))
    await session.execute(update(MatchCandidate).where(
        MatchCandidate.record_id.in_(records), MatchCandidate.status == "pending",
    ).values(status="superseded"))
    await session.execute(update(Record).where(Record.id.in_(records)).values(status="superseded"))


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
        if t.is_active:
            active[t.page_no] = t
        elif t.status == "ok":
            cand[t.page_no] = t
    pages = sorted(set(active) | set(cand))
    return [(p, active.get(p), cand.get(p) if p not in active or
             (p in cand and cand[p].created_at > active[p].created_at) else None) for p in pages]


async def reconcile_versions(
    session: AsyncSession, tenant_id: uuid.UUID, document_id: uuid.UUID, *,
    mode: str, criterion: str | None = None, keep_history: bool = False,
    choices: dict[str, str] | None = None, created_by: uuid.UUID | None = None,
    expected_versions: dict | None = None,
) -> int:
    """Reconcile into new revisions. Legacy keep_history=false can never delete evidence."""
    from .reconcile import book_frequency, merge_by_frequency

    if mode not in {"substitute", "manual", "mix"}:
        raise HTTPException(422, "Modo de reconciliación no válido")
    if mode == "mix" and criterion != "frequency":
        raise HTTPException(422, "La mezcla con IA requiere una ejecución presupuestada; utiliza revisión manual o frecuencia.")
    doc = await session.scalar(select(Document).where(
        Document.id == document_id, Document.tenant_id == tenant_id,
    ).with_for_update())
    if doc is None:
        raise HTTPException(404, "Documento no encontrado")
    pairs = await list_versions(session, document_id)
    if expected_versions is not None:
        actual = {str(page): {"active_id": str(a.id) if a else None, "candidate_id": str(c.id) if c else None}
                  for page, a, c in pairs}
        if any(actual.get(page) != expected for page, expected in expected_versions.items()):
            raise HTTPException(409, "Las versiones han cambiado. Recarga y revisa las diferencias antes de aplicar.")
        pairs = [pair for pair in pairs if str(pair[0]) in expected_versions]
    freq = book_frequency([a.text for _, a, _ in pairs if a and a.text]) if mode == "mix" else None
    applied = 0
    for page_no, a, c in pairs:
        if c is None:
            continue
        choice = (choices or {}).get(str(page_no))
        if mode == "manual" and choice is None:
            continue
        if mode == "manual" and choice == "old":
            c.status = "dismissed"
            applied += 1
            continue
        final = c.text
        if mode == "manual" and choice != "new":
            final = choice
        elif mode == "mix":
            final = merge_by_frequency(a.text if a else "", c.text or "", freq)
        if a:
            a.is_active = False
            await invalidate_evidence(session, a.id)
        await session.flush()  # partial unique index: deactivate before inserting
        session.add(Transcription(
            tenant_id=tenant_id, document_id=document_id, page_id=c.page_id,
            page_no=page_no, visibility=doc.visibility, text=final,
            engine="human" if mode == "manual" else "reconcile", model=None,
            confidence=None, status="corrected" if mode == "manual" else "ok",
            is_active=True, supersedes_id=a.id if a else c.id, reviewed_by=created_by,
            provenance={"candidate_id": str(c.id), "mode": mode, "criterion": criterion},
        ))
        applied += 1
    await session.flush()
    return applied
