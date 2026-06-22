"""Research gaps: given a tree person, point at the source that would most likely fill a hole
(e.g. "the father of X should be in the baptism book of <place>, <years>"). Cross-checks the
user's own library (document metadata) so it can say "you already have that book" vs "look here".
"""
from __future__ import annotations

import uuid

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.document import Document
from ...models.place import Place
from .mapping import normalize_place
from .service import get_person_detail


async def _matching_book(session, tenant_id, place: str | None, record_type: str, yf: int | None, yt: int | None):
    """A document already in the library that likely covers this gap (same place/type/year range)."""
    stmt = select(Document.id, Document.title).where(
        Document.tenant_id == tenant_id, Document.default_record_type == record_type
    )
    if place:
        key = normalize_place(place)
        sub = select(Place.id).where(Place.tenant_id == tenant_id, Place.normalized_key.ilike(f"%{key}%"))
        stmt = stmt.where(Document.place_id.in_(sub))
    if yf and yt:
        stmt = stmt.where(or_(Document.year_from.is_(None),
                              and_(Document.year_from <= yt, Document.year_to >= yf)))
    row = (await session.execute(stmt.limit(1))).first()
    return (str(row[0]), row[1]) if row else (None, None)


async def get_gaps(session: AsyncSession, tenant_id: uuid.UUID, person_id: uuid.UUID) -> list[dict]:
    d = await get_person_detail(session, person_id)
    primary = next((n for n in d.names if n.is_primary), d.names[0] if d.names else None)
    given = primary.given if primary else None
    surname = primary.surname if primary else None
    name = " ".join(x for x in [given, surname] if x) or "esta persona"
    birth = next((e for e in d.events if e.type in ("birth", "baptism", "christening")), None)
    by = birth.date_year if birth else None
    place = (birth.place if birth else None) or next((e.place for e in d.events if e.place), None)
    has_father = any(p.sex == "M" for p in d.parents) or len(d.parents) >= 1
    has_mother = any(p.sex == "F" for p in d.parents)

    gaps: list[dict] = []

    async def add(kind: str, text: str, rtype: str, yf: int | None, yt: int | None):
        book_id, book_title = await _matching_book(session, tenant_id, place, rtype, yf, yt)
        gaps.append({
            "kind": kind, "text": text, "record_type": rtype, "place": place,
            "year_from": yf, "year_to": yt, "surname": surname, "given": given,
            "have_book": book_id is not None, "book_id": book_id, "book_title": book_title,
            "search": {"given": given or "", "surname": surname or "", "place": place or "",
                       "year_from": str(yf) if yf else "", "year_to": str(yt) if yt else ""},
        })

    if not d.parents:
        yf, yt = (by - 2, by + 1) if by else (None, None)
        await add("parents", f"Los padres de {name} estarían en el acta de bautismo de "
                  f"{place or 'su parroquia'}{f', {yf}–{yt}' if yf else ''}.", "baptism", yf, yt)
    elif not has_mother:
        yf, yt = (by - 2, by + 1) if by else (None, None)
        await add("mother", f"La madre de {name} debería constar en su bautismo "
                  f"({place or 'su parroquia'}{f', {yf}–{yt}' if yf else ''}).", "baptism", yf, yt)
    if not birth:
        await add("birth", f"Falta el nacimiento/bautismo de {name}.", "baptism", None, None)
    # marriage gap if has spouse but no marriage event recorded
    if d.spouses and not any(e.type == "marriage" for e in d.events):
        await add("marriage", f"Falta el matrimonio de {name} (busca en libros de matrimonios).",
                  "marriage", by + 18 if by else None, by + 45 if by else None)

    return gaps
