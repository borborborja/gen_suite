"""Tree queries: import, stats, search, person detail, ego-centric subgraph.

All queries run on a tenant-scoped session, so RLS confines them to the active tenant.
"""
from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.citation import Citation
from ...models.document import Document, Page
from ...models.event import Event
from ...models.family import Family, FamilyChild
from ...models.person import Name, Person
from ...models.place import Place
from ...models.record import Record
from ...models.transcription import Transcription
from . import gedcom, importer
from .schemas import (
    CitationOut,
    DuplicatePair,
    EventOut,
    FamilyOut,
    ImportResult,
    NameOut,
    PersonDetail,
    PersonPage,
    PersonRow,
    RelatedPerson,
    SearchHit,
    TreeFamily,
    TreeGraph,
    TreePerson,
    TreeStats,
)


def _display(s: TreePerson | None) -> str | None:
    return " ".join(x for x in ((s.given, s.surname) if s else ()) if x) or None


async def import_gedcom_file(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    created_by: uuid.UUID,
    filename: str | None,
    data: bytes,
) -> ImportResult:
    roots, encoding = gedcom.parse(data)
    raw_text = data.decode("latin-1" if encoding == "ansel" else encoding, "replace")
    summary = await importer.import_gedcom(
        session, tenant_id, roots, filename=filename, raw_text=raw_text,
        encoding=encoding, created_by=created_by,
    )
    return ImportResult(**summary)


async def get_stats(session: AsyncSession) -> TreeStats:
    return TreeStats(
        persons=await session.scalar(select(func.count()).select_from(Person)) or 0,
        families=await session.scalar(select(func.count()).select_from(Family)) or 0,
        events=await session.scalar(select(func.count()).select_from(Event)) or 0,
        places=await session.scalar(select(func.count()).select_from(Place)) or 0,
    )


async def _summaries(session: AsyncSession, ids: set[uuid.UUID]) -> dict[uuid.UUID, TreePerson]:
    if not ids:
        return {}
    persons = (await session.scalars(select(Person).where(Person.id.in_(ids)))).all()
    names = {
        pid: (given, surname)
        for pid, given, surname in (
            await session.execute(
                select(Name.person_id, Name.given, Name.surname).where(
                    Name.person_id.in_(ids), Name.is_primary.is_(True)
                )
            )
        ).all()
    }
    birth: dict[uuid.UUID, int] = {}
    death: dict[uuid.UUID, int] = {}
    for pid, etype, year in (
        await session.execute(
            select(Event.subject_person_id, Event.type, Event.date_year).where(
                Event.subject_person_id.in_(ids),
                Event.type.in_(("birth", "death")),
                Event.date_year.is_not(None),
            ).order_by(Event.date_year)  # con eventos duplicados gana siempre el año menor
        )
    ).all():
        (birth if etype == "birth" else death).setdefault(pid, year)

    out: dict[uuid.UUID, TreePerson] = {}
    for p in persons:
        given, surname = names.get(p.id, (None, None))
        out[p.id] = TreePerson(
            id=p.id, given=given, surname=surname, sex=p.sex,
            birth_year=birth.get(p.id), death_year=death.get(p.id),
        )
    return out


async def get_subgraph(session: AsyncSession, focus_id: uuid.UUID, depth: int) -> TreeGraph:
    if not await session.get(Person, focus_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "person not found")

    person_ids: set[uuid.UUID] = {focus_id}
    families: dict[uuid.UUID, dict] = {}

    # Ancestors: from each person, find the family where they are a child, add its parents.
    frontier = {focus_id}
    for _ in range(depth):
        if not frontier:
            break
        rows = (
            await session.execute(
                select(FamilyChild.person_id, Family.id, Family.husband_id, Family.wife_id)
                .join(Family, Family.id == FamilyChild.family_id)
                .where(FamilyChild.person_id.in_(frontier))
            )
        ).all()
        nxt: set[uuid.UUID] = set()
        for child, fam_id, husb, wife in rows:
            fam = families.setdefault(
                fam_id, {"id": fam_id, "husband_id": husb, "wife_id": wife, "children": set()}
            )
            fam["children"].add(child)
            for parent in (husb, wife):
                if parent:
                    person_ids.add(parent)
                    nxt.add(parent)
        frontier = nxt

    # Descendants: from each person, find families they parent, add spouses + children.
    frontier = {focus_id}
    for _ in range(depth):
        if not frontier:
            break
        fam_rows = (
            await session.execute(
                select(Family.id, Family.husband_id, Family.wife_id).where(
                    or_(Family.husband_id.in_(frontier), Family.wife_id.in_(frontier))
                )
            )
        ).all()
        fam_ids: list[uuid.UUID] = []
        for fam_id, husb, wife in fam_rows:
            families.setdefault(
                fam_id, {"id": fam_id, "husband_id": husb, "wife_id": wife, "children": set()}
            )
            fam_ids.append(fam_id)
            for parent in (husb, wife):
                if parent:
                    person_ids.add(parent)
        nxt = set()
        if fam_ids:
            for fam_id, child in (
                await session.execute(
                    select(FamilyChild.family_id, FamilyChild.person_id).where(
                        FamilyChild.family_id.in_(fam_ids)
                    )
                )
            ).all():
                families[fam_id]["children"].add(child)
                person_ids.add(child)
                nxt.add(child)
        frontier = nxt

    summaries = await _summaries(session, person_ids)
    persons_out = [
        summaries.get(pid)
        or TreePerson(id=pid, given=None, surname=None, sex="U", birth_year=None, death_year=None)
        for pid in person_ids
    ]
    families_out = [
        TreeFamily(
            id=f["id"],
            husband_id=f["husband_id"],
            wife_id=f["wife_id"],
            child_ids=sorted(f["children"], key=str),
        )
        for f in families.values()
    ]
    return TreeGraph(focus=focus_id, persons=persons_out, families=families_out)


async def get_person_detail(session: AsyncSession, person_id: uuid.UUID) -> PersonDetail:
    person = await session.get(Person, person_id)
    if not person:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "person not found")

    names = (
        await session.scalars(
            select(Name).where(Name.person_id == person_id).order_by(Name.is_primary.desc())
        )
    ).all()
    events = [
        EventOut(id=eid, type=t, date_raw=dr, date_year=dy, place=pl,
                 place_lat=plat, place_lng=plng, value=v, is_inferred=inf)
        for eid, t, dr, dy, v, inf, pl, plat, plng in (
            await session.execute(
                select(
                    Event.id, Event.type, Event.date_raw, Event.date_year, Event.value,
                    Event.is_inferred, Place.name, Place.lat, Place.lng,
                )
                .select_from(Event)
                .outerjoin(Place, Place.id == Event.place_id)
                .where(Event.subject_person_id == person_id)
                .order_by(Event.date_year)
            )
        ).all()
    ]

    parent_ids: set[uuid.UUID] = set()
    child_fam_ids: list[uuid.UUID] = []
    for fid, husb, wife in (
        await session.execute(
            select(Family.id, Family.husband_id, Family.wife_id)
            .join(FamilyChild, FamilyChild.family_id == Family.id)
            .where(FamilyChild.person_id == person_id)
        )
    ).all():
        child_fam_ids.append(fid)
        parent_ids.update(p for p in (husb, wife) if p)

    # siblings = the OTHER children of the families where this person is a child
    sibling_ids: set[uuid.UUID] = set()
    if child_fam_ids:
        sibling_ids = {
            c for (c,) in (
                await session.execute(
                    select(FamilyChild.person_id).where(
                        FamilyChild.family_id.in_(child_fam_ids),
                        FamilyChild.person_id != person_id,
                    )
                )
            ).all()
        }

    spouse_ids: set[uuid.UUID] = set()
    child_ids: list[uuid.UUID] = []
    fams = (
        await session.execute(
            select(Family.id, Family.husband_id, Family.wife_id).where(
                or_(Family.husband_id == person_id, Family.wife_id == person_id)
            )
        )
    ).all()
    fam_ids = [f[0] for f in fams]
    for _, husb, wife in fams:
        other = wife if husb == person_id else husb
        if other:
            spouse_ids.add(other)
    if fam_ids:
        child_ids = [
            c
            for (c,) in (
                await session.execute(
                    select(FamilyChild.person_id)
                    .where(FamilyChild.family_id.in_(fam_ids))
                    .order_by(FamilyChild.seq)
                )
            ).all()
        ]

    summaries = await _summaries(session, parent_ids | spouse_ids | set(child_ids) | sibling_ids)

    # Couple events (marriage, divorce…) of the families where this person is a spouse — shown
    # in the life line of both spouses, tagged with family_id so the UI can badge/edit them.
    if fam_ids:
        spouse_by_fam = {fid: (wife if husb == person_id else husb) for fid, husb, wife in fams}
        for eid, t, dr, dy, v, inf, fid, pl, plat, plng in (
            await session.execute(
                select(Event.id, Event.type, Event.date_raw, Event.date_year, Event.value,
                       Event.is_inferred, Event.subject_family_id, Place.name, Place.lat, Place.lng)
                .select_from(Event)
                .outerjoin(Place, Place.id == Event.place_id)
                .where(Event.subject_family_id.in_(fam_ids))
            )
        ).all():
            sp = spouse_by_fam.get(fid)
            events.append(EventOut(
                id=eid, type=t, date_raw=dr, date_year=dy, place=pl, place_lat=plat,
                place_lng=plng, value=v, is_inferred=inf, family_id=fid, spouse_id=sp,
                spouse_name=_display(summaries.get(sp)) if sp else None,
            ))
        events.sort(key=lambda e: (e.date_year is None, e.date_year or 0))

    def related(pid: uuid.UUID, relation: str) -> RelatedPerson:
        s = summaries.get(pid)
        return RelatedPerson(
            id=pid,
            given=s.given if s else None,
            surname=s.surname if s else None,
            sex=s.sex if s else "U",
            birth_year=s.birth_year if s else None,
            death_year=s.death_year if s else None,
            relation=relation,
        )

    return PersonDetail(
        id=person.id,
        sex=person.sex,
        notes=person.notes,
        names=[
            NameOut(
                type=n.type, given=n.given, surname=n.surname, surname_prefix=n.surname_prefix,
                nickname=n.nickname, is_primary=n.is_primary, is_inferred=n.is_inferred,
            )
            for n in names
        ],
        events=events,
        parents=[related(p, "parent") for p in parent_ids],
        spouses=[related(p, "spouse") for p in spouse_ids],
        children=[related(c, "child") for c in child_ids],
        siblings=[related(s, "sibling") for s in sibling_ids],
    )


async def get_person_families(session: AsyncSession, person_id: uuid.UUID) -> list[FamilyOut]:
    """Families where the person is a spouse: the other spouse, children count and couple events."""
    if not await session.get(Person, person_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "person not found")
    fams = (
        await session.execute(
            select(Family.id, Family.husband_id, Family.wife_id).where(
                or_(Family.husband_id == person_id, Family.wife_id == person_id)
            )
        )
    ).all()
    if not fams:
        return []
    fam_ids = [f[0] for f in fams]
    spouse_by_fam = {fid: (wife if husb == person_id else husb) for fid, husb, wife in fams}
    child_counts: dict[uuid.UUID, int] = dict(
        (await session.execute(
            select(FamilyChild.family_id, func.count())
            .where(FamilyChild.family_id.in_(fam_ids))
            .group_by(FamilyChild.family_id)
        )).all()
    )
    summaries = await _summaries(session, {s for s in spouse_by_fam.values() if s})

    events_by_fam: dict[uuid.UUID, list[EventOut]] = {}
    for eid, t, dr, dy, v, inf, fid, pl, plat, plng in (
        await session.execute(
            select(Event.id, Event.type, Event.date_raw, Event.date_year, Event.value,
                   Event.is_inferred, Event.subject_family_id, Place.name, Place.lat, Place.lng)
            .select_from(Event)
            .outerjoin(Place, Place.id == Event.place_id)
            .where(Event.subject_family_id.in_(fam_ids))
            .order_by(Event.date_year)
        )
    ).all():
        events_by_fam.setdefault(fid, []).append(EventOut(
            id=eid, type=t, date_raw=dr, date_year=dy, place=pl, place_lat=plat,
            place_lng=plng, value=v, is_inferred=inf, family_id=fid,
        ))

    out: list[FamilyOut] = []
    for fid, _, _ in fams:
        sp = spouse_by_fam.get(fid)
        s = summaries.get(sp) if sp else None
        out.append(FamilyOut(
            id=fid,
            spouse=RelatedPerson(
                id=sp, given=s.given if s else None, surname=s.surname if s else None,
                sex=s.sex if s else "U", birth_year=s.birth_year if s else None,
                death_year=s.death_year if s else None, relation="spouse",
            ) if sp else None,
            children_count=child_counts.get(fid, 0),
            events=events_by_fam.get(fid, []),
        ))
    return out


async def find_duplicates(session: AsyncSession, limit: int = 50) -> list[DuplicatePair]:
    """Suggest likely duplicate persons in the tree: block by Spanish phonetic key of given+surname,
    then score candidate pairs by name similarity (Jaro-Winkler) + birth-year proximity. Reuses the
    same phonetic blocking the linkage pipeline uses on mentions."""
    import jellyfish

    from ..extraction.normalize import block_key_given, block_key_surname

    rows = (await session.execute(
        select(Person.id, Name.given, Name.surname)
        .outerjoin(Name, (Name.person_id == Person.id) & (Name.is_primary.is_(True)))
    )).all()
    ids = {r[0] for r in rows}
    summaries = await _summaries(session, ids)

    blocks: dict[tuple[str, str], list] = {}
    for pid, given, surname in rows:
        if not (given or surname):
            continue
        key = (block_key_given(given), block_key_surname(surname))
        if not key[0] and not key[1]:
            continue
        blocks.setdefault(key, []).append((pid, given or "", surname or ""))

    pairs: list[DuplicatePair] = []
    for members in blocks.values():
        if len(members) < 2:
            continue
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                (ai, ag, asu), (bi, bg, bsu) = members[i], members[j]
                g = jellyfish.jaro_winkler_similarity(ag.lower(), bg.lower()) if ag and bg else 0.0
                s = jellyfish.jaro_winkler_similarity(asu.lower(), bsu.lower()) if asu and bsu else 0.0
                name_score = 0.5 * g + 0.5 * s
                if name_score < 0.85:
                    continue
                sa, sb = summaries.get(ai), summaries.get(bi)
                year_ok = True
                if sa and sb and sa.birth_year and sb.birth_year:
                    year_ok = abs(sa.birth_year - sb.birth_year) <= 3
                if not year_ok:
                    continue
                score = round(min(1.0, name_score + (0.05 if year_ok else 0)), 3)
                reason = "nombre y apellidos muy similares" + (
                    " · año de nacimiento compatible" if (sa and sb and sa.birth_year and sb.birth_year) else "")
                pairs.append(DuplicatePair(
                    a=_hit(ai, summaries), b=_hit(bi, summaries), score=score, reason=reason))
    pairs.sort(key=lambda p: p.score, reverse=True)
    return pairs[:limit]


def _hit(pid: uuid.UUID, summaries: dict) -> SearchHit:
    s = summaries.get(pid)
    return SearchHit(id=pid, given=s.given if s else None, surname=s.surname if s else None,
                     birth_year=s.birth_year if s else None, death_year=s.death_year if s else None)


async def get_home(session: AsyncSession, tenant_id: uuid.UUID) -> uuid.UUID | None:
    from ...models.tenant import Tenant
    t = await session.get(Tenant, tenant_id)
    return t.home_person_id if t else None


async def set_home(session: AsyncSession, tenant_id: uuid.UUID, person_id: uuid.UUID | None) -> None:
    """Set the tree viewer's default person. Validates the person belongs to the tenant (RLS-scoped
    session, so a foreign id simply won't be found)."""
    from ...models.tenant import Tenant
    if person_id is not None and not await session.get(Person, person_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "person not found")
    t = await session.get(Tenant, tenant_id)
    if t:
        t.home_person_id = person_id


async def geocode_places(session: AsyncSession, limit: int = 40) -> dict:
    """Fill lat/lng on tree Places that still lack coordinates (e.g. after a GEDCOM import), using
    the Nominatim helper. Gentle: caps how many we resolve per call (the UI can re-run)."""
    import asyncio

    from ..geo.router import geocode_one

    places = (await session.scalars(
        select(Place).where(Place.lat.is_(None)).limit(limit)
    )).all()
    done = 0
    for pl in places:
        coords = await geocode_one(pl.name)
        if coords:
            pl.lat, pl.lng = coords
            done += 1
        await asyncio.sleep(1.0)  # respect Nominatim's 1 req/s policy
    await session.flush()
    remaining = await session.scalar(
        select(func.count()).select_from(Place).where(Place.lat.is_(None))
    ) or 0
    return {"geocoded": done, "remaining": remaining}


async def get_person_citations(session: AsyncSession, person_id: uuid.UUID) -> list[CitationOut]:
    """Evidence behind a person: the citations targeting the person directly plus those targeting
    their events/names, resolved to the source document + page so the UI can open the Visor."""
    if not await session.get(Person, person_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "person not found")

    event_ids = set((await session.scalars(
        select(Event.id).where(Event.subject_person_id == person_id))).all())
    name_ids = set((await session.scalars(
        select(Name.id).where(Name.person_id == person_id))).all())

    targets = [("person", person_id)]
    targets += [("event", e) for e in event_ids]
    targets += [("name", n) for n in name_ids]

    conds = [(Citation.target_type == t) & (Citation.target_id == i) for t, i in targets]
    cits = (await session.scalars(
        select(Citation).where(or_(*conds)).order_by(Citation.created_at)
    )).all() if conds else []

    # Batch-resolve the records / transcriptions / pages the citations point at.
    rec_ids = {c.record_id for c in cits if c.record_id}
    records = {r.id: r for r in (await session.scalars(
        select(Record).where(Record.id.in_(rec_ids)))).all()} if rec_ids else {}
    tr_ids = {c.transcription_id for c in cits if c.transcription_id}
    trans = {t.id: t for t in (await session.scalars(
        select(Transcription).where(Transcription.id.in_(tr_ids)))).all()} if tr_ids else {}

    page_ids: set[uuid.UUID] = set()
    for c in cits:
        if c.page_id:
            page_ids.add(c.page_id)
        r = records.get(c.record_id) if c.record_id else None
        if r and r.page_id:
            page_ids.add(r.page_id)
    pages = {p.id: p for p in (await session.scalars(
        select(Page).where(Page.id.in_(page_ids)))).all()} if page_ids else {}

    doc_ids: set[uuid.UUID] = set()
    for c in cits:
        r = records.get(c.record_id) if c.record_id else None
        if r:
            doc_ids.add(r.document_id)
        t = trans.get(c.transcription_id) if c.transcription_id else None
        if t:
            doc_ids.add(t.document_id)
    doc_ids |= {p.document_id for p in pages.values()}  # manual citas: only a page attached
    docs = {d.id: d for d in (await session.scalars(
        select(Document).where(Document.id.in_(doc_ids)))).all()} if doc_ids else {}

    out: list[CitationOut] = []
    for c in cits:
        r = records.get(c.record_id) if c.record_id else None
        t = trans.get(c.transcription_id) if c.transcription_id else None
        page = pages.get(c.page_id) or (pages.get(r.page_id) if r and r.page_id else None)
        document_id = ((r.document_id if r else None) or (t.document_id if t else None)
                       or (page.document_id if page else None))
        page_no = page.page_no if page else (t.page_no if t else None)
        doc = docs.get(document_id) if document_id else None
        out.append(CitationOut(
            id=c.id, note=c.note, target_type=c.target_type,
            document_id=document_id, document_title=doc.title if doc else None,
            page_no=page_no, record_type=r.record_type if r else None,
            date_raw=r.date_raw if r else None, summary=r.summary if r else None,
        ))
    return out


async def search_persons(
    session: AsyncSession, q: str | None = None, limit: int = 50, *,
    given: str | None = None, surname: str | None = None,
    year_from: int | None = None, year_to: int | None = None,
) -> list[SearchHit]:
    stmt = (
        # outerjoin: una persona sin Name primario (GEDCOM sin tag NAME) debe seguir
        # siendo visible cuando no se filtra por nombre
        select(Person.id, Name.given, Name.surname)
        .outerjoin(Name, (Name.person_id == Person.id) & Name.is_primary.is_(True))
    )
    if q and q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Name.given.ilike(like), Name.surname.ilike(like)))
    if given and given.strip():
        stmt = stmt.where(Name.given.ilike(f"%{given.strip()}%"))
    if surname and surname.strip():
        stmt = stmt.where(Name.surname.ilike(f"%{surname.strip()}%"))
    if year_from is not None or year_to is not None:
        by = select(Event.subject_person_id).where(
            Event.type == "birth", Event.date_year.is_not(None))
        if year_from is not None:
            by = by.where(Event.date_year >= year_from)
        if year_to is not None:
            by = by.where(Event.date_year <= year_to)
        stmt = stmt.where(Person.id.in_(by))
    rows = (await session.execute(stmt.limit(limit))).all()
    summaries = await _summaries(session, {r[0] for r in rows})
    return [
        SearchHit(
            id=pid,
            given=given,
            surname=surname,
            birth_year=summaries[pid].birth_year if pid in summaries else None,
            death_year=summaries[pid].death_year if pid in summaries else None,
        )
        for pid, given, surname in rows
    ]


def _person_filters(
    *, q: str | None = None, surname: str | None = None, sex: str | None = None,
    year_from: int | None = None, year_to: int | None = None,
    place_id: uuid.UUID | None = None, missing: list[str] | None = None,
) -> list:
    """WHERE conditions for the person directory (shared by list, CSV export and count)."""
    conds: list = []
    if q and q.strip():
        like = f"%{q.strip()}%"
        conds.append(or_(Name.given.ilike(like), Name.surname.ilike(like)))
    if surname and surname.strip():
        conds.append(Name.surname.ilike(f"%{surname.strip()}%"))
    if sex in ("M", "F", "U"):
        conds.append(Person.sex == sex)
    if year_from is not None or year_to is not None:
        by = select(Event.subject_person_id).where(
            Event.type == "birth", Event.date_year.is_not(None))
        if year_from is not None:
            by = by.where(Event.date_year >= year_from)
        if year_to is not None:
            by = by.where(Event.date_year <= year_to)
        conds.append(Person.id.in_(by))
    if place_id is not None:
        conds.append(Person.id.in_(
            select(Event.subject_person_id).where(
                Event.place_id == place_id, Event.subject_person_id.is_not(None))))
    for miss in missing or []:
        if miss == "birth":
            conds.append(Person.id.not_in(select(Event.subject_person_id).where(
                Event.type == "birth", Event.subject_person_id.is_not(None))))
        elif miss == "parents":
            conds.append(Person.id.not_in(select(FamilyChild.person_id)))
        elif miss == "sources":
            cit_person = select(Citation.id).where(
                Citation.target_type == "person", Citation.target_id == Person.id)
            cit_event = (
                select(Citation.id)
                .join(Event, Event.id == Citation.target_id)
                .where(Citation.target_type == "event", Event.subject_person_id == Person.id)
            )
            conds.append(~cit_person.exists() & ~cit_event.exists())
    return conds


async def list_persons(
    session: AsyncSession, *, q: str | None = None, surname: str | None = None,
    sort: str = "name", order: str = "asc", page: int = 1, page_size: int = 50,
    sex: str | None = None, year_from: int | None = None, year_to: int | None = None,
    place_id: uuid.UUID | None = None, missing: list[str] | None = None,
) -> PersonPage:
    """Paginated, sortable, filterable person directory for the tree's list view."""
    birth_sq = (
        select(func.min(Event.date_year))
        .where(Event.subject_person_id == Person.id, Event.type == "birth")
        .correlate(Person).scalar_subquery()
    )
    death_sq = (
        select(func.min(Event.date_year))
        .where(Event.subject_person_id == Person.id, Event.type == "death")
        .correlate(Person).scalar_subquery()
    )
    stmt = (
        select(Person.id, Name.given, Name.surname, Person.sex,
               birth_sq.label("birth_year"), death_sq.label("death_year"))
        .outerjoin(Name, (Name.person_id == Person.id) & Name.is_primary.is_(True))
    )
    count_stmt = (
        select(func.count()).select_from(Person)
        .outerjoin(Name, (Name.person_id == Person.id) & Name.is_primary.is_(True))
    )
    for cond in _person_filters(q=q, surname=surname, sex=sex, year_from=year_from,
                                year_to=year_to, place_id=place_id, missing=missing):
        stmt, count_stmt = stmt.where(cond), count_stmt.where(cond)

    sub = stmt.subquery()
    sort_cols = {
        "name": (sub.c.surname, sub.c.given),
        "birth": (sub.c.birth_year,),
        "death": (sub.c.death_year,),
    }.get(sort, (sub.c.surname, sub.c.given))
    ordered = [c.desc().nulls_last() if order == "desc" else c.asc().nulls_last()
               for c in sort_cols] + [sub.c.id]  # desempate estable entre páginas
    rows = (
        await session.execute(
            select(sub).order_by(*ordered).limit(page_size).offset((page - 1) * page_size)
        )
    ).all()
    total = await session.scalar(count_stmt) or 0
    return PersonPage(
        total=total,
        items=[PersonRow(id=pid, given=g, surname=s, sex=sex, birth_year=by, death_year=dy)
               for pid, g, s, sex, by, dy in rows],
    )


async def export_persons_csv(
    session: AsyncSession, *, q: str | None = None, surname: str | None = None,
    sex: str | None = None, year_from: int | None = None, year_to: int | None = None,
    place_id: uuid.UUID | None = None, missing: list[str] | None = None,
) -> str:
    """CSV of the filtered person directory. Batched queries so a whole-tree export of tens
    of thousands of rows stays a handful of MB and a few dozen queries."""
    import csv
    import io

    stmt = (
        select(Person.id, Name.given, Name.surname, Person.sex)
        .outerjoin(Name, (Name.person_id == Person.id) & Name.is_primary.is_(True))
        .order_by(Name.surname.nulls_last(), Name.given.nulls_last(), Person.id)
    )
    for cond in _person_filters(q=q, surname=surname, sex=sex, year_from=year_from,
                                year_to=year_to, place_id=place_id, missing=missing):
        stmt = stmt.where(cond)

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "nombre", "apellidos", "sexo", "año_nacimiento", "lugar_nacimiento",
                "año_defuncion", "lugar_defuncion", "num_hijos", "num_fuentes"])

    offset, batch_size = 0, 1000
    while True:
        batch = (await session.execute(stmt.limit(batch_size).offset(offset))).all()
        if not batch:
            break
        offset += batch_size
        ids = [r[0] for r in batch]

        vitals: dict[tuple[uuid.UUID, str], tuple[int | None, str | None]] = {}
        for pid, etype, year, place in (await session.execute(
                select(Event.subject_person_id, Event.type, Event.date_year, Place.name)
                .select_from(Event).outerjoin(Place, Place.id == Event.place_id)
                .where(Event.subject_person_id.in_(ids), Event.type.in_(("birth", "death"))))).all():
            vitals.setdefault((pid, etype), (year, place))

        children: dict[uuid.UUID, int] = {}
        fam_rows = (await session.execute(
            select(Family.id, Family.husband_id, Family.wife_id).where(
                or_(Family.husband_id.in_(ids), Family.wife_id.in_(ids))))).all()
        fam_ids = [f[0] for f in fam_rows]
        counts = dict((await session.execute(
            select(FamilyChild.family_id, func.count()).where(FamilyChild.family_id.in_(fam_ids))
            .group_by(FamilyChild.family_id))).all()) if fam_ids else {}
        for fid, husb, wife in fam_rows:
            for parent in (husb, wife):
                if parent in ids:
                    children[parent] = children.get(parent, 0) + counts.get(fid, 0)

        sources: dict[uuid.UUID, int] = {}
        for pid, n in (await session.execute(
                select(Citation.target_id, func.count()).where(
                    Citation.target_type == "person", Citation.target_id.in_(ids))
                .group_by(Citation.target_id))).all():
            sources[pid] = sources.get(pid, 0) + n
        for pid, n in (await session.execute(
                select(Event.subject_person_id, func.count())
                .select_from(Citation).join(Event, Event.id == Citation.target_id)
                .where(Citation.target_type == "event", Event.subject_person_id.in_(ids))
                .group_by(Event.subject_person_id))).all():
            sources[pid] = sources.get(pid, 0) + n

        for pid, given, surname_, sex_ in batch:
            by, bp = vitals.get((pid, "birth"), (None, None))
            dy, dp = vitals.get((pid, "death"), (None, None))
            w.writerow([str(pid), given or "", surname_ or "", sex_, by or "", bp or "",
                        dy or "", dp or "", children.get(pid, 0), sources.get(pid, 0)])
    return buf.getvalue()


async def get_person_report(session: AsyncSession, person_id: uuid.UUID):
    """Everything the printable report needs, in one payload."""
    from .schemas import PersonReport
    return PersonReport(
        person=await get_person_detail(session, person_id),
        families=await get_person_families(session, person_id),
        citations=await get_person_citations(session, person_id),
    )


async def get_statistics(session: AsyncSession):
    """Aggregates for the statistics view: surnames, decades, lifespan, places, sex, children."""
    from sqlalchemy.orm import aliased

    from .schemas import CountItem, LifespanItem, TreeStatistics

    surnames = [
        CountItem(label=s, count=n)
        for s, n in (await session.execute(
            select(Name.surname, func.count()).where(
                Name.is_primary.is_(True), Name.surname.is_not(None))
            .group_by(Name.surname).order_by(func.count().desc()).limit(15))).all()
    ]
    decades = [
        CountItem(label=str(int(d)), count=n)
        for d, n in (await session.execute(
            select((Event.date_year // 10 * 10).label("dec"), func.count())
            .where(Event.type == "birth", Event.date_year.is_not(None))
            .group_by("dec").order_by("dec"))).all()
    ]
    b, d = aliased(Event), aliased(Event)
    spans: dict[int, list[int]] = {}
    for by, dy in (await session.execute(
            select(b.date_year, d.date_year)
            .join(d, d.subject_person_id == b.subject_person_id)
            .where(b.type == "birth", d.type == "death",
                   b.date_year.is_not(None), d.date_year.is_not(None)))).all():
        span = dy - by
        if 0 <= span <= 120:
            spans.setdefault(by // 100 * 100, []).append(span)
    lifespan = [
        LifespanItem(century=c, avg_years=round(sum(v) / len(v), 1), count=len(v))
        for c, v in sorted(spans.items())
    ]
    places = [
        CountItem(label=n, count=c)
        for n, c in (await session.execute(
            select(Place.name, func.count(Event.id))
            .join(Event, Event.place_id == Place.id)
            .group_by(Place.id, Place.name).order_by(func.count(Event.id).desc()).limit(15))).all()
    ]
    sex = {s: n for s, n in (await session.execute(
        select(Person.sex, func.count()).group_by(Person.sex))).all()}
    total_children = await session.scalar(select(func.count()).select_from(FamilyChild)) or 0
    fams_with_children = await session.scalar(
        select(func.count(func.distinct(FamilyChild.family_id)))) or 0
    return TreeStatistics(
        totals=await get_stats(session),
        surnames=surnames, birth_decades=decades, lifespan_by_century=lifespan, places=places,
        sex=sex,
        avg_children_per_family=round(total_children / fams_with_children, 2) if fams_with_children else 0.0,
    )


async def get_roots(session: AsyncSession, limit: int = 100) -> list[SearchHit]:
    """Persons who are not a child in any family — natural entry points into the tree."""
    rows = (
        await session.execute(
            select(Person.id, Name.given, Name.surname)
            .outerjoin(Name, (Name.person_id == Person.id) & (Name.is_primary.is_(True)))
            .where(Person.id.not_in(select(FamilyChild.person_id)))
            .limit(limit)
        )
    ).all()
    summaries = await _summaries(session, {r[0] for r in rows})
    return [
        SearchHit(
            id=pid,
            given=given,
            surname=surname,
            birth_year=summaries[pid].birth_year if pid in summaries else None,
            death_year=summaries[pid].death_year if pid in summaries else None,
        )
        for pid, given, surname in rows
    ]
