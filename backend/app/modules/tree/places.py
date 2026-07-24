"""Place manager: rename, merge duplicates, geocode and arrange places in a hierarchy
(pueblo → provincia → país). The hierarchy is internal to the app — the GEDCOM exporter
keeps writing the flat place name so imports/exports round-trip unchanged."""
from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.errors import AppError
from ...models.event import Event
from ...models.family import Family
from ...models.place import Place
from .mapping import normalize_place
from .schemas import PlaceDetail, PlaceEventRow, PlaceEventsPage, PlacePage, PlaceRef, PlaceRow
from .service import _summaries

PLACE_TYPES = ("country", "region", "province", "municipality", "parish", "other")

_MAX_DEPTH = 12  # hierarchy walk guard (also breaks accidental cycles)


async def list_places(
    session: AsyncSession, *, q: str | None = None, sort: str = "name", order: str = "asc",
    page: int = 1, page_size: int = 50,
) -> PlacePage:
    ev_count = (
        select(func.count()).where(Event.place_id == Place.id).correlate(Place).scalar_subquery()
    )
    parent = Place.__table__.alias("parent")
    child_count = (
        select(func.count()).select_from(parent)
        .where(parent.c.parent_id == Place.id).correlate(Place).scalar_subquery()
    )
    parent2 = Place.__table__.alias("parent2")
    stmt = (
        select(Place.id, Place.name, Place.place_type, Place.parent_id, parent2.c.name,
               Place.lat, Place.lng, ev_count.label("event_count"),
               child_count.label("children_count"))
        .outerjoin(parent2, parent2.c.id == Place.parent_id)
    )
    count_stmt = select(func.count()).select_from(Place)
    if q and q.strip():
        cond = Place.name.ilike(f"%{q.strip()}%")
        stmt, count_stmt = stmt.where(cond), count_stmt.where(cond)

    sub = stmt.subquery()
    col = sub.c.event_count if sort == "events" else sub.c.name
    ordered = col.desc().nulls_last() if order == "desc" else col.asc().nulls_last()
    rows = (
        await session.execute(
            select(sub).order_by(ordered, sub.c.name, sub.c.id)  # id = desempate estable
            .limit(page_size).offset((page - 1) * page_size)
        )
    ).all()
    total = await session.scalar(count_stmt) or 0
    return PlacePage(total=total, items=[
        PlaceRow(id=pid, name=n, place_type=pt, parent_id=par, parent_name=pn, lat=lat, lng=lng,
                 event_count=ec, children_count=cc)
        for pid, n, pt, par, pn, lat, lng, ec, cc in rows
    ])


async def _breadcrumb(session: AsyncSession, place: Place) -> list[PlaceRef]:
    """Ancestors of ``place`` from the root down to its direct parent."""
    chain: list[PlaceRef] = []
    seen = {place.id}
    cur = place
    for _ in range(_MAX_DEPTH):
        if not cur.parent_id or cur.parent_id in seen:
            break
        cur = await session.get(Place, cur.parent_id)
        if not cur:
            break
        seen.add(cur.id)
        chain.append(PlaceRef(id=cur.id, name=cur.name, place_type=cur.place_type))
    chain.reverse()
    return chain


async def get_place(session: AsyncSession, place_id: uuid.UUID) -> PlaceDetail:
    place = await session.get(Place, place_id)
    if not place:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "lugar no encontrado")
    event_count = await session.scalar(
        select(func.count()).where(Event.place_id == place_id)) or 0
    children = (await session.scalars(
        select(Place).where(Place.parent_id == place_id).order_by(Place.name))).all()
    parent = await session.get(Place, place.parent_id) if place.parent_id else None
    return PlaceDetail(
        id=place.id, name=place.name, place_type=place.place_type, parent_id=place.parent_id,
        parent_name=parent.name if parent else None, lat=place.lat, lng=place.lng,
        event_count=event_count, children_count=len(children),
        breadcrumb=await _breadcrumb(session, place),
        children=[PlaceRef(id=c.id, name=c.name, place_type=c.place_type) for c in children],
    )


async def list_place_events(
    session: AsyncSession, place_id: uuid.UUID, *, page: int = 1, page_size: int = 50,
) -> PlaceEventsPage:
    if not await session.get(Place, place_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "lugar no encontrado")
    total = await session.scalar(select(func.count()).where(Event.place_id == place_id)) or 0
    events = (await session.scalars(
        select(Event).where(Event.place_id == place_id)
        .order_by(Event.date_year.nulls_last(), Event.id)
        .limit(page_size).offset((page - 1) * page_size)
    )).all()

    # Resolve a navigable person per event: the subject, or a spouse of the subject family.
    fam_ids = {e.subject_family_id for e in events if e.subject_family_id}
    fam_spouse: dict[uuid.UUID, uuid.UUID] = {}
    if fam_ids:
        for fid, husb, wife in (await session.execute(
                select(Family.id, Family.husband_id, Family.wife_id).where(Family.id.in_(fam_ids)))).all():
            sp = husb or wife
            if sp:
                fam_spouse[fid] = sp
    person_ids = {e.subject_person_id for e in events if e.subject_person_id} | set(fam_spouse.values())
    summaries = await _summaries(session, person_ids)

    items: list[PlaceEventRow] = []
    for e in events:
        pid = e.subject_person_id or (fam_spouse.get(e.subject_family_id) if e.subject_family_id else None)
        s = summaries.get(pid) if pid else None
        items.append(PlaceEventRow(
            id=e.id, type=e.type, date_raw=e.date_raw, date_year=e.date_year,
            person_id=pid, person_name=" ".join(x for x in ((s.given, s.surname) if s else ()) if x) or None,
        ))
    return PlaceEventsPage(total=total, items=items)


async def _is_descendant(session: AsyncSession, candidate: uuid.UUID, of: uuid.UUID) -> bool:
    """True if ``candidate`` is ``of`` itself or sits below it in the hierarchy.
    Walks the full chain (seen-set, not a depth cap): a truncated walk would green-light
    cycles in hierarchies deeper than the cap."""
    cur: uuid.UUID | None = candidate
    seen: set[uuid.UUID] = set()
    while cur is not None and cur not in seen:
        if cur == of:
            return True
        seen.add(cur)
        place = await session.get(Place, cur)
        cur = place.parent_id if place else None
    return False


async def update_place(
    session: AsyncSession, place_id: uuid.UUID, *, name: str | None = None,
    place_type: str | None = None, parent_id: uuid.UUID | None = None,
    clear_parent: bool = False, lat: float | None = None, lng: float | None = None,
) -> Place:
    place = await session.get(Place, place_id)
    if not place:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "lugar no encontrado")
    if name is not None and name.strip():
        key = normalize_place(name)[:512]
        clash = await session.scalar(select(Place).where(
            Place.normalized_key == key, Place.id != place_id))
        if clash:
            raise AppError(409, f"Ya existe el lugar «{clash.name}»; usa fusionar en su lugar",
                           code="place_exists")
        place.name = name.strip()[:512]
        place.normalized_key = key
    if place_type is not None:
        if place_type and place_type not in PLACE_TYPES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "tipo de lugar no válido")
        place.place_type = place_type or None
    if clear_parent:
        place.parent_id = None
    elif parent_id is not None:
        if not await session.get(Place, parent_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "lugar padre no encontrado")
        # walking up from the new parent must never reach this place (no cycles)
        if await _is_descendant(session, parent_id, place_id):
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "el padre elegido está por debajo de este lugar (crearía un ciclo)")
        place.parent_id = parent_id
    if lat is not None:
        place.lat = lat
    if lng is not None:
        place.lng = lng
    await session.flush()
    return place


async def merge_place(session: AsyncSession, place_id: uuid.UUID, into_id: uuid.UUID) -> Place:
    """Fold ``place_id`` into ``into_id``: repoint events and children, keep coords, delete it."""
    if place_id == into_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no se puede fusionar un lugar consigo mismo")
    src = await session.get(Place, place_id)
    dst = await session.get(Place, into_id)
    if not src or not dst:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "lugar no encontrado")
    # ORM loops on purpose: the change-log capture (audit) only sees ORM mutations
    for ev in (await session.scalars(select(Event).where(Event.place_id == place_id))).all():
        ev.place_id = into_id
    # If dst sits anywhere below src, repointing src's children to dst would close a cycle
    # (src → X → dst with X.parent = dst). Lift dst to src's spot in the hierarchy first.
    if await _is_descendant(session, into_id, place_id):
        dst.parent_id = src.parent_id
    for child in (await session.scalars(select(Place).where(Place.parent_id == place_id))).all():
        if child.id != into_id:
            child.parent_id = into_id
    if dst.lat is None and src.lat is not None:
        dst.lat, dst.lng = src.lat, src.lng
    await session.delete(src)
    await session.flush()
    return dst


async def geocode_place(session: AsyncSession, place_id: uuid.UUID) -> Place:
    place = await session.get(Place, place_id)
    if not place:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "lugar no encontrado")
    from ..geo.router import geocode_one

    breadcrumb = await _breadcrumb(session, place)
    query = ", ".join([place.name] + [b.name for b in reversed(breadcrumb)])
    coords = await geocode_one(query)
    if not coords:
        raise AppError(422, f"No se encontraron coordenadas para «{query}»", code="geocode_miss")
    place.lat, place.lng = coords
    await session.flush()
    return place
