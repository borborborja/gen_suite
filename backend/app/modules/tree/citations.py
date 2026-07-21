"""Manual source citations: "this fact comes from this document/page of my library".

Complements the automatic citations created by the linkage pipeline. Targets supported in
the UI: a person or one of their events (person/family events alike)."""
from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.citation import Citation
from ...models.document import Page
from ...models.event import Event
from ...models.person import Person

_TARGETS = ("person", "event")


async def _resolve_page(session: AsyncSession, document_id: uuid.UUID | None,
                        page_no: int | None) -> uuid.UUID | None:
    if not document_id:
        return None
    page = await session.scalar(select(Page).where(
        Page.document_id == document_id, Page.page_no == (page_no or 1)))
    if not page:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "esa página no existe en el documento")
    return page.id


async def create_citation(session: AsyncSession, tenant_id: uuid.UUID, *, target_type: str,
                          target_id: uuid.UUID, document_id: uuid.UUID | None = None,
                          page_no: int | None = None, note: str | None = None) -> Citation:
    if target_type not in _TARGETS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "target_type debe ser person o event")
    target = await session.get(Person if target_type == "person" else Event, target_id)
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "el destino de la cita no existe")
    if not document_id and not (note and note.strip()):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "una cita necesita documento o nota")
    cit = Citation(
        tenant_id=tenant_id, target_type=target_type, target_id=target_id,
        page_id=await _resolve_page(session, document_id, page_no),
        note=(note or "").strip() or None,
    )
    session.add(cit)
    await session.flush()
    return cit


async def update_citation(session: AsyncSession, citation_id: uuid.UUID, *,
                          document_id: uuid.UUID | None = None, page_no: int | None = None,
                          note: str | None = None) -> Citation:
    cit = await session.get(Citation, citation_id)
    if not cit:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "cita no encontrada")
    if document_id is not None:
        cit.page_id = await _resolve_page(session, document_id, page_no)
    if note is not None:
        cit.note = note.strip() or None
    await session.flush()
    return cit


async def delete_citation(session: AsyncSession, citation_id: uuid.UUID) -> None:
    cit = await session.get(Citation, citation_id)
    if not cit:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "cita no encontrada")
    await session.delete(cit)
    await session.flush()
