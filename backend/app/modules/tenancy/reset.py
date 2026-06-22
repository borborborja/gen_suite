"""Destructive per-tenant data reset (Ajustes → zona peligrosa). Scopes let the user wipe the
tree, the library, the discoveries, or everything — without touching the tenant, its members or
the configured AI providers. All deletes are tenant-scoped (RLS also applies)."""
from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...core import storage
from ...models.citation import Citation
from ...models.document import Document, Page
from ...models.event import Event
from ...models.family import Family, FamilyChild
from ...models.gedcom_import import GedcomImport
from ...models.match_candidate import MatchCandidate
from ...models.mention import PersonMention
from ...models.person import Name, Person
from ...models.place import Place
from ...models.record import Record
from ...models.transcription import Transcription

SCOPES = ("discoveries", "library", "tree", "all")


async def _wipe_discoveries(session: AsyncSession, tid: uuid.UUID) -> None:
    await session.execute(delete(MatchCandidate).where(MatchCandidate.tenant_id == tid))
    await session.execute(
        update(PersonMention).where(PersonMention.tenant_id == tid)
        .values(resolved_person_id=None, match_status="unlinked")
    )


async def _wipe_library(session: AsyncSession, tid: uuid.UUID) -> None:
    # remove stored objects first (gather prefixes before deleting the rows)
    docs = (await session.execute(
        select(Document.storage_bucket, Document.storage_prefix).where(Document.tenant_id == tid)
    )).all()
    await session.execute(delete(MatchCandidate).where(MatchCandidate.tenant_id == tid))
    await session.execute(delete(Citation).where(Citation.tenant_id == tid, Citation.record_id.is_not(None)))
    await session.execute(delete(PersonMention).where(PersonMention.tenant_id == tid))
    await session.execute(delete(Record).where(Record.tenant_id == tid))
    await session.execute(delete(Transcription).where(Transcription.tenant_id == tid))
    await session.execute(delete(Page).where(Page.tenant_id == tid))
    await session.execute(delete(Document).where(Document.tenant_id == tid))
    for bucket, prefix in docs:
        try:
            await storage.delete_prefix(bucket, prefix)
        except Exception:
            pass


async def _wipe_tree(session: AsyncSession, tid: uuid.UUID) -> None:
    await session.execute(delete(MatchCandidate).where(MatchCandidate.tenant_id == tid))
    await session.execute(delete(Citation).where(Citation.tenant_id == tid))
    await session.execute(delete(Event).where(Event.tenant_id == tid))
    await session.execute(delete(FamilyChild).where(FamilyChild.tenant_id == tid))
    await session.execute(delete(Family).where(Family.tenant_id == tid))
    await session.execute(delete(Name).where(Name.tenant_id == tid))
    await session.execute(delete(Person).where(Person.tenant_id == tid))
    await session.execute(delete(GedcomImport).where(GedcomImport.tenant_id == tid))


async def reset_tenant_data(session: AsyncSession, tenant_id: uuid.UUID, scope: str) -> dict:
    if scope not in SCOPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"scope must be one of {SCOPES}")
    if scope in ("discoveries", "all"):
        await _wipe_discoveries(session, tenant_id)
    if scope in ("library", "all"):
        await _wipe_library(session, tenant_id)
    if scope in ("tree", "all"):
        await _wipe_tree(session, tenant_id)
    if scope == "all":
        await session.execute(delete(Place).where(Place.tenant_id == tenant_id))
    await session.commit()
    return {"reset": scope}
