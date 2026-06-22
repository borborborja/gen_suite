"""Within-corpus deduplication / entity resolution (plan M4): the same person mentioned across
several acts, and the same act extracted more than once. Distinct from linkage (tree↔corpus) — here
it's corpus↔corpus. Powers Estela's "hemos encontrado N actas más con X" affordance.
"""
from __future__ import annotations

import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.mention import PersonMention
from ...models.record import Record
from ..extraction.normalize import block_key_surname, norm_surname
from .scoring import MentionView, coref_score


async def _co_names(session: AsyncSession, record_ids: set[uuid.UUID]) -> dict[uuid.UUID, set[str]]:
    """Map each record → the set of normalized names mentioned in it (for relative overlap)."""
    if not record_ids:
        return {}
    rows = (await session.scalars(select(PersonMention).where(PersonMention.record_id.in_(record_ids)))).all()
    out: dict[uuid.UUID, set[str]] = {}
    for m in rows:
        s = out.setdefault(m.record_id, set())
        for v in (norm_surname(m.surname), norm_surname(m.given)):
            if v:
                s.add(v)
    return out


def _view(m: PersonMention, year: int | None, co: set[str]) -> MentionView:
    own = {norm_surname(m.surname), norm_surname(m.given)}
    return MentionView(
        given=m.given, surname=m.surname, year=year, role=m.role,
        origin=m.stated_origin, co_names={c for c in co - own if c},
    )


async def find_coreferents(
    session: AsyncSession, tenant_id: uuid.UUID, mention_id: uuid.UUID, limit: int = 20
) -> list[dict]:
    """Other corpus mentions that likely refer to the SAME person as ``mention_id`` (M4).
    Blocking by phonetic surname, then coref scoring; returns scored matches above the floor."""
    base = await session.get(PersonMention, mention_id)
    if not base:
        return []
    bk = block_key_surname(base.surname)
    ns = norm_surname(base.surname)
    conds = []
    if bk:
        conds.append(PersonMention.block_key_surname == bk)
    if ns:
        conds.append(PersonMention.norm_surname.ilike(f"%{ns}%"))
    if not conds:
        return []
    cands = (
        await session.scalars(
            select(PersonMention).where(
                PersonMention.tenant_id == tenant_id,
                PersonMention.id != mention_id,
                or_(*conds),
            ).limit(limit * 6)
        )
    ).all()
    if not cands:
        return []

    rec_ids = {base.record_id} | {m.record_id for m in cands}
    records = {r.id: r for r in (await session.scalars(select(Record).where(Record.id.in_(rec_ids)))).all()}
    co = await _co_names(session, rec_ids)

    base_view = _view(base, records[base.record_id].date_year if base.record_id in records else None,
                      co.get(base.record_id, set()))
    out: list[dict] = []
    for m in cands:
        rec = records.get(m.record_id)
        res = coref_score(base_view, _view(m, rec.date_year if rec else None, co.get(m.record_id, set())))
        if res["same"]:
            out.append({
                "mention_id": m.id, "record_id": m.record_id,
                "name_raw": m.name_raw, "role": m.role,
                "record_type": rec.record_type if rec else None,
                "date_year": rec.date_year if rec else None,
                "score": res["score"], "signals": res["signals"],
            })
    out.sort(key=lambda h: h["score"], reverse=True)
    return out[:limit]


async def find_duplicate_records(
    session: AsyncSession, tenant_id: uuid.UUID, document_id: uuid.UUID
) -> list[dict]:
    """Groups of records in a document that look like the SAME act extracted twice (same type +
    year + place + principal name). A light heuristic for re-transcribed/overlapping pages (M4)."""
    records = (
        await session.scalars(select(Record).where(Record.document_id == document_id))
    ).all()
    principals: dict[uuid.UUID, PersonMention] = {}
    if records:
        rows = (
            await session.scalars(
                select(PersonMention).where(
                    PersonMention.record_id.in_([r.id for r in records]),
                    PersonMention.role == "principal",
                )
            )
        ).all()
        for m in rows:
            principals.setdefault(m.record_id, m)

    groups: dict[tuple, list[Record]] = {}
    for r in records:
        p = principals.get(r.id)
        key = (
            r.record_type, r.date_year, r.place_id,
            norm_surname(p.surname) if p else None, norm_surname(p.given) if p else None,
        )
        groups.setdefault(key, []).append(r)

    dups: list[dict] = []
    for key, recs in groups.items():
        if len(recs) > 1 and (key[1] is not None or key[3]):  # need a year or a name to be meaningful
            dups.append({
                "record_type": key[0], "date_year": key[1],
                "principal_surname": key[3],
                "record_ids": [str(r.id) for r in recs],
                "count": len(recs),
            })
    return dups
