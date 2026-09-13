"""Manual source citations: "this fact comes from this document/page of my library".

Complements the automatic citations created by the linkage pipeline. Targets supported in
the UI: a person or one of their events (person/family events alike)."""
from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.citation import Citation
from ...models.document import Document, Page
from ...models.event import Event
from ...models.family import Family
from ...models.person import Name, Person
from ...models.research import Assertion, Source

_TARGETS = {"person": Person, "name": Name, "event": Event, "family": Family, "assertion": Assertion}


async def _resolve_page(session: AsyncSession, document_id: uuid.UUID | None,
                        page_no: int | None) -> uuid.UUID | None:
    if not document_id:
        return None
    page = await session.scalar(select(Page).where(
        Page.document_id == document_id, Page.page_no == (page_no or 1)))
    if not page:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "esa página no existe en el documento")
    return page.id


async def _source_for_document(session: AsyncSession, document_id: uuid.UUID | None) -> uuid.UUID | None:
    if not document_id:
        return None
    existing = await session.scalar(select(Source.id).where(Source.primary_document_id == document_id))
    if existing:
        return existing
    # Lock the document while lazily materialising its bibliographic Source.  Two concurrent
    # citations to a newly-uploaded document must converge on one source record.
    document = await session.scalar(select(Document).where(Document.id == document_id).with_for_update())
    if not document:
        return None
    existing = await session.scalar(select(Source.id).where(Source.primary_document_id == document_id))
    if existing:
        return existing
    # A local document becomes a bibliographic source at first use, including documents uploaded
    # after the migration backfill.  The citation still retains its exact page/record links.
    source = Source(
        tenant_id=document.tenant_id, title=document.title, source_type="document",
        primary_document_id=document.id,
    )
    session.add(source)
    await session.flush()
    return source.id


async def create_citation(session: AsyncSession, tenant_id: uuid.UUID, *, target_type: str,
                          target_id: uuid.UUID, document_id: uuid.UUID | None = None,
                          page_no: int | None = None, source_id: uuid.UUID | None = None,
                          assertion_id: uuid.UUID | None = None, locator: dict | None = None,
                          quote: str | None = None, quality: str | None = None,
                          note: str | None = None) -> Citation:
    if target_type not in _TARGETS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "target_type no admite citas")
    target = await session.get(_TARGETS[target_type], target_id)
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "el destino de la cita no existe")
    if source_id and not await session.get(Source, source_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "la fuente no existe")
    if assertion_id and not await session.get(Assertion, assertion_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "la afirmación no existe")
    resolved_source_id = source_id or await _source_for_document(session, document_id)
    if not (document_id or resolved_source_id or (note and note.strip()) or (quote and quote.strip())):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "una cita necesita fuente, documento, cita textual o nota")
    cit = Citation(
        tenant_id=tenant_id, target_type=target_type, target_id=target_id,
        page_id=await _resolve_page(session, document_id, page_no),
        source_id=resolved_source_id, assertion_id=assertion_id, locator=locator,
        quote=(quote or "").strip() or None, quality=quality,
        note=(note or "").strip() or None,
    )
    session.add(cit)
    await session.flush()
    return cit


async def update_citation(session: AsyncSession, citation_id: uuid.UUID, *,
                          document_id: uuid.UUID | None = None, page_no: int | None = None,
                          source_id: uuid.UUID | None = None, clear_source: bool = False,
                          assertion_id: uuid.UUID | None = None, clear_assertion: bool = False,
                          locator: dict | None = None, quote: str | None = None,
                          quality: str | None = None, note: str | None = None) -> Citation:
    cit = await session.get(Citation, citation_id)
    if not cit:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "cita no encontrada")
    if document_id is not None:
        cit.page_id = await _resolve_page(session, document_id, page_no)
        if cit.source_id is None:
            cit.source_id = await _source_for_document(session, document_id)
    if source_id is not None:
        if not await session.get(Source, source_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "la fuente no existe")
        cit.source_id = source_id
    if clear_source:
        cit.source_id = None
    if assertion_id is not None:
        if not await session.get(Assertion, assertion_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "la afirmación no existe")
        cit.assertion_id = assertion_id
    if clear_assertion:
        cit.assertion_id = None
    if locator is not None:
        cit.locator = locator
    if quote is not None:
        cit.quote = quote.strip() or None
    if quality is not None:
        cit.quality = quality.strip() or None
    if note is not None:
        cit.note = note.strip() or None
    # keep the create-invariant: a citation must still point at something
    if cit.page_id is None and cit.source_id is None and not cit.note and not cit.quote and not (
            cit.record_id or cit.transcription_id or cit.person_mention_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "la cita quedaría vacía: necesita fuente, documento, cita textual o nota")
    await session.flush()
    return cit


async def delete_citation(session: AsyncSession, citation_id: uuid.UUID) -> None:
    cit = await session.get(Citation, citation_id)
    if not cit:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "cita no encontrada")
    await session.delete(cit)
    await session.flush()
