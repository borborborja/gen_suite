"""Serialize a tenant's genealogy graph back to a valid GEDCOM 5.5.1 (UTF-8) file.

Fresh xrefs are assigned (@I1@, @F1@, @S1@, ...). INDI/FAM/NAME/SEX/events/FAMC/FAMS are emitted
from the schema; unmapped tags stored on ``raw`` are re-emitted. Provenance is exported too: one
top-level SOUR record per source document, with SOUR pointers (+ PAGE) under the person/event/
family each Citation supports. (OBJE/media records are still not exported.)
"""
from __future__ import annotations

import uuid
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.citation import Citation
from ...models.document import Document, Page
from ...models.event import Event
from ...models.family import Family, FamilyChild
from ...models.person import Name, Person
from ...models.place import Place
from ...models.record import Record
from ...models.transcription import Transcription
from . import gedcom
from .gedcom import Node
from .mapping import TYPE2TAG, VALUE_EVENT_TAGS


def _sour_ptr(sxref: str, page_no: int | None) -> Node:
    """A SOUR pointer (citation) under an INDI/FAM/event, with an optional PAGE."""
    node = Node(tag="SOUR", value=sxref)
    if page_no:
        node.children.append(Node(tag="PAGE", value=f"pág. {page_no}"))
    return node


def _head_node() -> Node:
    return Node(
        tag="HEAD",
        children=[
            Node(tag="SOUR", value="gen_suite"),
            Node(
                tag="GEDC",
                children=[Node(tag="VERS", value="5.5.1"), Node(tag="FORM", value="LINEAGE-LINKED")],
            ),
            Node(tag="CHAR", value="UTF-8"),
        ],
    )


def _name_node(name: Name) -> Node:
    node = Node(tag="NAME", value=f"{name.given or ''} /{name.surname or ''}/".strip())
    if name.given:
        node.children.append(Node(tag="GIVN", value=name.given))
    if name.surname:
        node.children.append(Node(tag="SURN", value=name.surname))
    if name.surname_prefix:
        node.children.append(Node(tag="SPFX", value=name.surname_prefix))
    if name.nickname:
        node.children.append(Node(tag="NICK", value=name.nickname))
    return node


def _event_node(ev: Event, place_name: dict[uuid.UUID, str],
                event_sources: dict[uuid.UUID, list[tuple[str, int | None]]] | None = None) -> Node:
    # GEDCOM-standard tag when known; otherwise the standard custom-event form EVEN + TYPE so the
    # file stays valid and interoperable (FamilySearch/Ancestry/MyHeritage import it as a fact).
    known = ev.type in TYPE2TAG
    tag = TYPE2TAG.get(ev.type, "EVEN")
    value = ev.value if (ev.value and tag in VALUE_EVENT_TAGS) else None
    node = Node(tag=tag, value=value)
    if not known:
        node.children.append(Node(tag="TYPE", value=ev.type))
    if ev.date_raw:
        node.children.append(Node(tag="DATE", value=ev.date_raw))
    if ev.place_id and ev.place_id in place_name:
        node.children.append(Node(tag="PLAC", value=place_name[ev.place_id]))
    for sxref, page_no in (event_sources or {}).get(ev.id, []):
        node.children.append(_sour_ptr(sxref, page_no))
    return node


async def _collect_sources(session: AsyncSession, tenant_id: uuid.UUID,
                           name_to_person: dict[uuid.UUID, uuid.UUID],
                           fc_to_family: dict[uuid.UUID, uuid.UUID]):
    """Turn the tenant's Citations into top-level GEDCOM SOUR records (one per source document) plus
    SOUR pointers grouped by the conclusion they support (person/event/family). Returns
    (sour_nodes, by_person, by_event, by_family)."""
    cits = (await session.scalars(
        select(Citation).where(Citation.tenant_id == tenant_id))).all()
    if not cits:
        return [], {}, {}, {}

    rec_ids = {c.record_id for c in cits if c.record_id}
    records = {r.id: r for r in (await session.scalars(
        select(Record).where(Record.id.in_(rec_ids)))).all()} if rec_ids else {}
    tr_ids = {c.transcription_id for c in cits if c.transcription_id}
    trans = {t.id: t for t in (await session.scalars(
        select(Transcription).where(Transcription.id.in_(tr_ids)))).all()} if tr_ids else {}
    page_ids = {c.page_id for c in cits if c.page_id}
    page_ids |= {r.page_id for r in records.values() if r.page_id}
    pages = {p.id: p for p in (await session.scalars(
        select(Page).where(Page.id.in_(page_ids)))).all()} if page_ids else {}
    doc_ids = {r.document_id for r in records.values()} | {t.document_id for t in trans.values()}
    doc_title = {d.id: d.title for d in (await session.scalars(
        select(Document).where(Document.id.in_(doc_ids)))).all()} if doc_ids else {}

    # assign a stable @S{n}@ xref per source document
    doc_order: dict[uuid.UUID, str] = {}
    for did in doc_title:
        doc_order[did] = f"@S{len(doc_order) + 1}@"

    by_person: dict[uuid.UUID, list[tuple[str, int | None]]] = defaultdict(list)
    by_event: dict[uuid.UUID, list[tuple[str, int | None]]] = defaultdict(list)
    by_family: dict[uuid.UUID, list[tuple[str, int | None]]] = defaultdict(list)

    for c in cits:
        r = records.get(c.record_id) if c.record_id else None
        t = trans.get(c.transcription_id) if c.transcription_id else None
        doc_id = (r.document_id if r else None) or (t.document_id if t else None)
        if not doc_id or doc_id not in doc_order:
            continue
        sxref = doc_order[doc_id]
        page = pages.get(c.page_id) or (pages.get(r.page_id) if r and r.page_id else None)
        page_no = page.page_no if page else (t.page_no if t else None)
        ref = (sxref, page_no)
        if c.target_type == "person":
            by_person[c.target_id].append(ref)
        elif c.target_type == "name":
            pid = name_to_person.get(c.target_id)
            if pid:
                by_person[pid].append(ref)
        elif c.target_type == "event":
            by_event[c.target_id].append(ref)
        elif c.target_type == "family":
            by_family[c.target_id].append(ref)
        elif c.target_type == "family_child":
            fid = fc_to_family.get(c.target_id)
            if fid:
                by_family[fid].append(ref)

    sour_nodes = [
        Node(tag="SOUR", xref=doc_order[did], children=[Node(tag="TITL", value=doc_title[did])])
        for did in doc_order
    ]
    return sour_nodes, by_person, by_event, by_family


async def export_gedcom(session: AsyncSession, tenant_id: uuid.UUID) -> str:
    persons = (
        await session.scalars(
            select(Person).where(Person.tenant_id == tenant_id).order_by(Person.created_at)
        )
    ).all()
    families = (
        await session.scalars(
            select(Family).where(Family.tenant_id == tenant_id).order_by(Family.created_at)
        )
    ).all()
    place_name = {
        pid: name
        for pid, name in (
            await session.execute(select(Place.id, Place.name).where(Place.tenant_id == tenant_id))
        ).all()
    }

    names_by_person: dict[uuid.UUID, list[Name]] = defaultdict(list)
    name_to_person: dict[uuid.UUID, uuid.UUID] = {}
    for n in (
        await session.scalars(
            select(Name).where(Name.tenant_id == tenant_id).order_by(Name.is_primary.desc())
        )
    ).all():
        names_by_person[n.person_id].append(n)
        name_to_person[n.id] = n.person_id

    person_events: dict[uuid.UUID, list[Event]] = defaultdict(list)
    family_events: dict[uuid.UUID, list[Event]] = defaultdict(list)
    for ev in (await session.scalars(select(Event).where(Event.tenant_id == tenant_id))).all():
        if ev.subject_person_id:
            person_events[ev.subject_person_id].append(ev)
        elif ev.subject_family_id:
            family_events[ev.subject_family_id].append(ev)

    children_of: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    famc_of: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    fc_to_family: dict[uuid.UUID, uuid.UUID] = {}
    for fc in (
        await session.scalars(
            select(FamilyChild).where(FamilyChild.tenant_id == tenant_id).order_by(FamilyChild.seq)
        )
    ).all():
        children_of[fc.family_id].append(fc.person_id)
        famc_of[fc.person_id].append(fc.family_id)
        fc_to_family[fc.id] = fc.family_id

    fams_of: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for f in families:
        if f.husband_id:
            fams_of[f.husband_id].append(f.id)
        if f.wife_id:
            fams_of[f.wife_id].append(f.id)

    pxref = {p.id: f"@I{i + 1}@" for i, p in enumerate(persons)}
    fxref = {f.id: f"@F{i + 1}@" for i, f in enumerate(families)}

    # Provenance: top-level SOUR records + per-conclusion SOUR pointers, from the Citation graph.
    sour_nodes, src_person, src_event, src_family = await _collect_sources(
        session, tenant_id, name_to_person, fc_to_family)

    records: list[Node] = [_head_node()]

    for p in persons:
        node = Node(tag="INDI", xref=pxref[p.id])
        for name in names_by_person.get(p.id, []):
            node.children.append(_name_node(name))
        if p.sex in ("M", "F"):
            node.children.append(Node(tag="SEX", value=p.sex))
        for ev in person_events.get(p.id, []):
            node.children.append(_event_node(ev, place_name, src_event))
        for fid in fams_of.get(p.id, []):
            node.children.append(Node(tag="FAMS", value=fxref[fid]))
        for fid in famc_of.get(p.id, []):
            node.children.append(Node(tag="FAMC", value=fxref[fid]))
        for sxref, page_no in src_person.get(p.id, []):
            node.children.append(_sour_ptr(sxref, page_no))
        for raw in p.raw or []:
            node.children.append(gedcom.from_dict(raw))
        records.append(node)

    for f in families:
        node = Node(tag="FAM", xref=fxref[f.id])
        if f.husband_id:
            node.children.append(Node(tag="HUSB", value=pxref[f.husband_id]))
        if f.wife_id:
            node.children.append(Node(tag="WIFE", value=pxref[f.wife_id]))
        for cid in children_of.get(f.id, []):
            node.children.append(Node(tag="CHIL", value=pxref[cid]))
        for ev in family_events.get(f.id, []):
            node.children.append(_event_node(ev, place_name, src_event))
        for sxref, page_no in src_family.get(f.id, []):
            node.children.append(_sour_ptr(sxref, page_no))
        for raw in f.raw or []:
            node.children.append(gedcom.from_dict(raw))
        records.append(node)

    records.extend(sour_nodes)
    records.append(Node(tag="TRLR"))
    return gedcom.serialize(records)
