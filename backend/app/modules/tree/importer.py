"""Map a parsed GEDCOM (Node tree) into the genealogy schema for one tenant.

Two passes: persons first (so xref->id is known), then families linking spouses/children.
Places are deduped per tenant by normalized name. Tags we don't model are preserved on
``Person.raw`` / ``Family.raw`` for round-trip export.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.event import Event
from ...models.family import Family, FamilyChild
from ...models.gedcom_import import GedcomImport
from ...models.person import Name, Person
from ...models.place import Place
from . import gedcom
from .mapping import (
    ALL_EVENT_TAGS,
    FAMILY_EVENT_TAGS,
    FAMILY_MAPPED,
    PERSON_EVENT_TAGS,
    PERSON_MAPPED,
    TAG2TYPE,
    VALUE_EVENT_TAGS,
    extract_year,
    normalize_place,
)
from .gedcom import Node


def _name_parts(name_node: Node) -> tuple[str | None, str | None, str | None, str | None]:
    given = surname = prefix = nick = None
    if name_node.value:
        from .mapping import _NAME_RE

        m = _NAME_RE.match(name_node.value)
        if m:
            given = (m.group("given") or "").strip() or None
            surname = (m.group("surname") or "").strip() or None
        else:
            given = name_node.value.strip() or None
    given = (name_node.value_of("GIVN") or given) or None
    surname = (name_node.value_of("SURN") or surname) or None
    prefix = name_node.value_of("SPFX") or prefix
    nick = name_node.value_of("NICK") or nick
    return (
        (given or None) and given[:255],
        (surname or None) and surname[:255],
        (prefix or None) and prefix[:64],
        (nick or None) and nick[:128],
    )


def _make_event(
    tenant_id: uuid.UUID,
    ev: Node,
    place_ids: dict[str, uuid.UUID],
    *,
    subject_person_id: uuid.UUID | None = None,
    subject_family_id: uuid.UUID | None = None,
) -> Event:
    date_raw = ev.value_of("DATE")
    plac = ev.value_of("PLAC")
    etype = TAG2TYPE.get(ev.tag, ev.tag.lower())[:24]
    value = ev.value if (ev.value and ev.tag in VALUE_EVENT_TAGS) else None
    return Event(
        tenant_id=tenant_id,
        type=etype,
        date_raw=date_raw[:128] if date_raw else None,
        date_year=extract_year(date_raw),
        value=value,
        place_id=place_ids.get(normalize_place(plac)) if plac else None,
        subject_person_id=subject_person_id,
        subject_family_id=subject_family_id,
    )


async def import_gedcom(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    roots: list[Node],
    *,
    filename: str | None,
    raw_text: str | None,
    encoding: str,
    created_by: uuid.UUID | None,
) -> dict:
    indi_nodes = [r for r in roots if r.tag == "INDI"]
    fam_nodes = [r for r in roots if r.tag == "FAM"]

    # ── 1) Places (dedup per tenant) ──
    place_names: dict[str, str] = {}
    for record in indi_nodes + fam_nodes:
        for ev in record.children:
            if ev.tag in ALL_EVENT_TAGS:
                plac = ev.value_of("PLAC")
                if plac and plac.strip():
                    place_names.setdefault(normalize_place(plac), plac.strip()[:512])
    existing = (
        {
            key: pid
            for key, pid in (
                await session.execute(
                    select(Place.normalized_key, Place.id).where(
                        Place.normalized_key.in_(list(place_names.keys()))
                    )
                )
            ).all()
        }
        if place_names
        else {}
    )
    new_places = {
        key: Place(tenant_id=tenant_id, name=name, normalized_key=key[:512])
        for key, name in place_names.items()
        if key not in existing
    }
    session.add_all(list(new_places.values()))
    await session.flush()
    place_ids = dict(existing)
    place_ids.update({key: place.id for key, place in new_places.items()})

    # ── 2) Persons ──
    persons: dict[str, Person] = {}
    for node in indi_nodes:
        sex = (node.value_of("SEX") or "U").strip().upper()[:1] or "U"
        if sex not in ("M", "F"):
            sex = "U"
        raw = [gedcom.to_dict(c) for c in node.children if c.tag not in PERSON_MAPPED]
        persons[node.xref] = Person(
            tenant_id=tenant_id, gedcom_xref=node.xref, sex=sex, raw=raw or None
        )
    session.add_all(list(persons.values()))
    await session.flush()

    names: list[Name] = []
    events: list[Event] = []
    for node in indi_nodes:
        person = persons[node.xref]
        for i, name_node in enumerate(node.all("NAME")):
            given, surname, prefix, nick = _name_parts(name_node)
            names.append(
                Name(
                    tenant_id=tenant_id,
                    person_id=person.id,
                    type="birth",
                    given=given,
                    surname=surname,
                    surname_prefix=prefix,
                    nickname=nick,
                    is_primary=(i == 0),
                )
            )
        for ev in node.children:
            if ev.tag in PERSON_EVENT_TAGS:
                events.append(_make_event(tenant_id, ev, place_ids, subject_person_id=person.id))
    session.add_all(names)

    # ── 3) Families ──
    families: dict[str, Family] = {}
    for node in fam_nodes:
        husb, wife = node.value_of("HUSB"), node.value_of("WIFE")
        raw = [gedcom.to_dict(c) for c in node.children if c.tag not in FAMILY_MAPPED]
        families[node.xref] = Family(
            tenant_id=tenant_id,
            gedcom_xref=node.xref,
            husband_id=persons[husb].id if husb in persons else None,
            wife_id=persons[wife].id if wife in persons else None,
            raw=raw or None,
        )
    session.add_all(list(families.values()))
    await session.flush()

    children: list[FamilyChild] = []
    for node in fam_nodes:
        family = families[node.xref]
        seq = 0
        for chil in node.all("CHIL"):
            if chil.value in persons:
                seq += 1
                children.append(
                    FamilyChild(
                        tenant_id=tenant_id,
                        family_id=family.id,
                        person_id=persons[chil.value].id,
                        relation="birth",
                        seq=seq,
                    )
                )
        for ev in node.children:
            if ev.tag in FAMILY_EVENT_TAGS:
                events.append(_make_event(tenant_id, ev, place_ids, subject_family_id=family.id))
    session.add_all(children)
    session.add_all(events)

    record = GedcomImport(
        tenant_id=tenant_id,
        filename=filename,
        char_encoding=encoding,
        individuals_count=len(indi_nodes),
        families_count=len(fam_nodes),
        raw_gedcom=raw_text,
        created_by=created_by,
    )
    session.add(record)
    await session.flush()

    return {
        "import_id": record.id,
        "individuals": len(indi_nodes),
        "families": len(fam_nodes),
        "places": len(place_names),
        "events": len(events),
        "encoding": encoding,
    }
