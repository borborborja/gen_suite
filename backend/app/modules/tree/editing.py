"""Manual tree editing (add a discovery you made yourself), kept inside the GEDCOM model: persons,
names (NAME), facts/events (BIRT/CHR/DEAT/RESI/OCCU/…), and parent/spouse/child relationships
(FAM). Everything written here exports cleanly to GEDCOM via the exporter."""
from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.event import Event
from ...models.family import Family, FamilyChild
from ...models.person import Name, Person
from ...models.place import Place
from .mapping import TYPE2TAG, normalize_place

# GEDCOM-standard fact types offered in the edit UI (key = internal/GEDCOM type, label = Spanish).
FACT_TYPES: list[dict] = [
    {"key": "birth", "label": "Nacimiento"}, {"key": "baptism", "label": "Bautismo"},
    {"key": "christening", "label": "Cristianización"}, {"key": "confirmation", "label": "Confirmación"},
    {"key": "marriage", "label": "Matrimonio"}, {"key": "death", "label": "Defunción"},
    {"key": "burial", "label": "Entierro"}, {"key": "residence", "label": "Residencia"},
    {"key": "occupation", "label": "Oficio"}, {"key": "education", "label": "Educación"},
    {"key": "religion", "label": "Religión"}, {"key": "census", "label": "Censo / Padrón"},
    {"key": "immigration", "label": "Inmigración"}, {"key": "emigration", "label": "Emigración"},
    {"key": "naturalization", "label": "Naturalización"}, {"key": "will", "label": "Testamento"},
    {"key": "probate", "label": "Sucesión"}, {"key": "title", "label": "Título"},
    {"key": "event", "label": "Otro hecho"},
]


async def _resolve_place(session, tenant_id, name: str | None, lat=None, lng=None):
    if not name or not name.strip():
        return None
    key = normalize_place(name)[:512]
    place = await session.scalar(select(Place).where(Place.tenant_id == tenant_id, Place.normalized_key == key))
    if place:
        if lat is not None and place.lat is None:
            place.lat, place.lng = lat, lng
        return place.id
    place = Place(tenant_id=tenant_id, name=name.strip()[:512], normalized_key=key, lat=lat, lng=lng)
    session.add(place)
    await session.flush()
    return place.id


async def create_person(session, tenant_id, *, given, surname, sex="U", inferred=False) -> Person:
    p = Person(tenant_id=tenant_id, sex=(sex or "U")[:1].upper() if sex in ("M", "F") else "U")
    session.add(p)
    await session.flush()
    session.add(Name(tenant_id=tenant_id, person_id=p.id, type="birth", given=given,
                     surname=surname, is_primary=True, is_inferred=inferred))
    await session.flush()
    return p


async def update_person(session, tenant_id, person_id, *, sex=None, given=None, surname=None,
                        surname_prefix=None, nickname=None, notes=None) -> Person:
    p = await session.get(Person, person_id)
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "person not found")
    if sex in ("M", "F", "U"):
        p.sex = sex
    if notes is not None:
        p.notes = notes or None
    name = await session.scalar(
        select(Name).where(Name.person_id == person_id).order_by(Name.is_primary.desc())
    )
    if name and (given is not None or surname is not None or surname_prefix is not None or nickname is not None):
        if given is not None:
            name.given = given or None
        if surname is not None:
            name.surname = surname or None
        if surname_prefix is not None:
            name.surname_prefix = surname_prefix or None
        if nickname is not None:
            name.nickname = nickname or None
    await session.flush()
    return p


async def add_event(session, tenant_id, person_id, *, type, date_raw=None, place=None,
                    place_lat=None, place_lng=None, value=None) -> Event:
    if not await session.get(Person, person_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "person not found")
    if type not in TYPE2TAG:
        type = "event"  # custom fact → exported as EVEN+TYPE
    from .mapping import extract_year
    ev = Event(
        tenant_id=tenant_id, type=type, date_raw=date_raw, date_year=extract_year(date_raw),
        value=value or None, is_inferred=False, subject_person_id=person_id,
        place_id=await _resolve_place(session, tenant_id, place, place_lat, place_lng),
    )
    session.add(ev)
    await session.flush()
    return ev


async def edit_event(session, tenant_id, event_id, *, type=None, date_raw=None, place=None,
                     place_lat=None, place_lng=None, value=None) -> Event:
    """Update an existing fact/event in place. Only non-None fields are written; ``place`` is
    re-resolved (a new/looked-up Place) whenever provided."""
    from .mapping import extract_year
    ev = await session.get(Event, event_id)
    if not ev:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "event not found")
    if type is not None:
        ev.type = type if type in TYPE2TAG else "event"
    if date_raw is not None:
        ev.date_raw = date_raw or None
        ev.date_year = extract_year(date_raw)
    if value is not None:
        ev.value = value or None
    if place is not None:
        ev.place_id = await _resolve_place(session, tenant_id, place, place_lat, place_lng)
    await session.flush()
    return ev


async def delete_event(session, event_id) -> None:
    ev = await session.get(Event, event_id)
    if ev:
        await session.delete(ev)


async def _parent_family(session, tenant_id, child_id) -> Family:
    fam_id = await session.scalar(select(FamilyChild.family_id).where(FamilyChild.person_id == child_id).limit(1))
    if fam_id:
        return await session.get(Family, fam_id)
    fam = Family(tenant_id=tenant_id)
    session.add(fam)
    await session.flush()
    session.add(FamilyChild(tenant_id=tenant_id, family_id=fam.id, person_id=child_id))
    return fam


async def _spouse_family(session, tenant_id, person_id) -> Family:
    fam = await session.scalar(select(Family).where(or_(Family.husband_id == person_id, Family.wife_id == person_id)).limit(1))
    if fam:
        return fam
    fam = Family(tenant_id=tenant_id)
    session.add(fam)
    await session.flush()
    return fam


async def link_relative(session, tenant_id, person_id, relative_id, relation: str) -> None:
    """relation ∈ father|mother|parent|spouse|child — build the right FAM links (GEDCOM)."""
    rel = await session.get(Person, relative_id)
    if not rel:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "relative not found")
    if relation in ("father", "mother", "parent"):
        fam = await _parent_family(session, tenant_id, person_id)
        if relative_id in (fam.husband_id, fam.wife_id):
            return
        if relation == "mother" or (relation == "parent" and rel.sex == "F"):
            fam.wife_id = relative_id if fam.wife_id is None else fam.wife_id
        elif fam.husband_id is None:
            fam.husband_id = relative_id
        elif fam.wife_id is None:
            fam.wife_id = relative_id
    elif relation == "spouse":
        fam = await _spouse_family(session, tenant_id, person_id)
        if relative_id in (fam.husband_id, fam.wife_id):
            return
        if fam.husband_id in (None, person_id) and rel.sex != "M":
            fam.husband_id = fam.husband_id or person_id
            fam.wife_id = relative_id if fam.wife_id is None else fam.wife_id
        else:
            fam.wife_id = fam.wife_id or person_id
            fam.husband_id = relative_id if fam.husband_id is None else fam.husband_id
    elif relation == "child":
        fam = await _spouse_family(session, tenant_id, person_id)
        if fam.husband_id is None and fam.wife_id is None:
            fam.husband_id = person_id
        exists = await session.scalar(select(FamilyChild.person_id).where(
            FamilyChild.family_id == fam.id, FamilyChild.person_id == relative_id))
        if not exists:
            session.add(FamilyChild(tenant_id=tenant_id, family_id=fam.id, person_id=relative_id))
    else:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid relation")
    await session.flush()


async def add_relative(session, tenant_id, person_id, *, relation, given, surname, sex="U") -> Person:
    rel = await create_person(session, tenant_id, given=given, surname=surname, sex=sex)
    await link_relative(session, tenant_id, person_id, rel.id, relation)
    return rel


async def unlink_relative(session, tenant_id, person_id, relative_id, relation: str) -> None:
    """Inverse of link_relative: remove the FAM link between person and relative.
    - father/mother/parent: relative is a parent in person's child-family → clear that slot.
    - spouse: relative is the other spouse in a shared family → clear the relative's slot.
    - child: relative is a child of person's spouse-family → drop the FamilyChild row.
    Empty Family rows are left in place (harmless, no orphan parents)."""
    if relation in ("father", "mother", "parent"):
        fam_id = await session.scalar(
            select(FamilyChild.family_id).where(FamilyChild.person_id == person_id))
        if not fam_id:
            return
        fam = await session.get(Family, fam_id)
        if fam and fam.husband_id == relative_id:
            fam.husband_id = None
        elif fam and fam.wife_id == relative_id:
            fam.wife_id = None
    elif relation == "spouse":
        fam = await session.scalar(select(Family).where(or_(
            (Family.husband_id == person_id) & (Family.wife_id == relative_id),
            (Family.wife_id == person_id) & (Family.husband_id == relative_id),
        )).limit(1))
        if fam:
            if fam.husband_id == relative_id:
                fam.husband_id = None
            elif fam.wife_id == relative_id:
                fam.wife_id = None
    elif relation == "child":
        fam_ids = (await session.scalars(select(Family.id).where(
            or_(Family.husband_id == person_id, Family.wife_id == person_id)))).all()
        if fam_ids:
            fc = await session.scalar(select(FamilyChild).where(
                FamilyChild.family_id.in_(fam_ids), FamilyChild.person_id == relative_id))
            if fc:
                await session.delete(fc)
    else:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid relation")
    await session.flush()


async def merge_persons(session, tenant_id, keep_id, dup_id) -> Person:
    """Fold ``dup_id`` into ``keep_id``: move names/events/child-rows, repoint family parent slots
    and citations, merge notes, then delete the duplicate. Identical primary-name duplicates are
    dropped; only one primary name survives."""
    from ...models.citation import Citation
    if keep_id == dup_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "cannot merge a person with itself")
    keep = await session.get(Person, keep_id)
    dup = await session.get(Person, dup_id)
    if not keep or not dup:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "person not found")

    keep_names = (await session.scalars(select(Name).where(Name.person_id == keep_id))).all()
    seen = {(n.given or "", n.surname or "", n.type) for n in keep_names}
    for n in (await session.scalars(select(Name).where(Name.person_id == dup_id))).all():
        sig = (n.given or "", n.surname or "", n.type)
        if sig in seen:
            await session.delete(n)
        else:
            n.person_id = keep_id
            n.is_primary = False  # keep's primary stays primary
            seen.add(sig)

    for ev in (await session.scalars(select(Event).where(Event.subject_person_id == dup_id))).all():
        ev.subject_person_id = keep_id
    for fc in (await session.scalars(select(FamilyChild).where(FamilyChild.person_id == dup_id))).all():
        exists = await session.scalar(select(FamilyChild.id).where(
            FamilyChild.family_id == fc.family_id, FamilyChild.person_id == keep_id))
        if exists:
            await session.delete(fc)
        else:
            fc.person_id = keep_id
    for fam in (await session.scalars(select(Family).where(
            or_(Family.husband_id == dup_id, Family.wife_id == dup_id)))).all():
        if fam.husband_id == dup_id:
            fam.husband_id = keep_id
        if fam.wife_id == dup_id:
            fam.wife_id = keep_id
    await session.execute(
        update(Citation).where(
            Citation.target_type == "person", Citation.target_id == dup_id
        ).values(target_id=keep_id)
    )
    if dup.notes:
        keep.notes = ((keep.notes + "\n\n") if keep.notes else "") + dup.notes
    if keep.sex == "U" and dup.sex in ("M", "F"):
        keep.sex = dup.sex
    await session.flush()
    await session.delete(dup)
    await session.flush()
    return keep


async def delete_person(session, tenant_id, person_id) -> None:
    """Remove a person and everything anchored to them: names + events (subject), their FamilyChild
    rows, their slot in any spouse Family (set NULL), and citations targeting them. Families left
    empty are kept (harmless)."""
    from ...models.citation import Citation
    p = await session.get(Person, person_id)
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "person not found")
    for name in (await session.scalars(select(Name).where(Name.person_id == person_id))).all():
        await session.delete(name)
    for ev in (await session.scalars(
            select(Event).where(Event.subject_person_id == person_id))).all():
        await session.delete(ev)
    for fc in (await session.scalars(
            select(FamilyChild).where(FamilyChild.person_id == person_id))).all():
        await session.delete(fc)
    for fam in (await session.scalars(select(Family).where(
            or_(Family.husband_id == person_id, Family.wife_id == person_id)))).all():
        if fam.husband_id == person_id:
            fam.husband_id = None
        if fam.wife_id == person_id:
            fam.wife_id = None
    for cit in (await session.scalars(select(Citation).where(
            Citation.target_type == "person", Citation.target_id == person_id))).all():
        await session.delete(cit)
    await session.delete(p)
    await session.flush()
