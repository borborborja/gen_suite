"""Serialize a tenant's genealogy graph to GEDCOM 5.5.1 with a loss report.

Fresh xrefs are assigned to the graph, bibliography and repositories. Citations
stay on their modeled targets. Unresolvable legacy raw pointers become explicit
notes instead of being rebound accidentally. No media or full native backup.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.base import Base
from ...models.event import Event
from ...models.family import Family, FamilyChild
from ...models.person import Name, Person
from ...models.place import Place
from . import gedcom, gedcom_evidence
from .gedcom import Node
from .mapping import TYPE2TAG, VALUE_EVENT_TAGS


def _head_node() -> Node:
    return Node(
        tag="HEAD",
        children=[
            Node(tag="SOUR", value="gen_suite"),
            Node(tag="SUBM", value="@U1@"),
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
    if name.type:
        node.children.append(Node(tag="TYPE", value=name.type))
    return node


def _event_node(ev: Event, place_name: dict[uuid.UUID, str],
                event_sources: dict | None = None) -> Node:
    # Known event tags or EVEN + TYPE. Consumer-level compatibility remains to be tested.
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
    node.children.extend((event_sources or {}).get(("event", ev.id), []))
    return node


async def build_graph(session: AsyncSession, tenant_id: uuid.UUID, *, version="5.5.1"):
    persons = (
        await session.scalars(
            select(Person).where(Person.tenant_id == tenant_id).order_by(Person.created_at, Person.id)
        )
    ).all()
    families = (
        await session.scalars(
            select(Family).where(Family.tenant_id == tenant_id).order_by(Family.created_at, Family.id)
        )
    ).all()
    place_name = {
        pid: name
        for pid, name in (
            await session.execute(select(Place.id, Place.name).where(Place.tenant_id == tenant_id))
        ).all()
    }

    names_by_person: dict[uuid.UUID, list[Name]] = defaultdict(list)
    for n in (
        await session.scalars(
            select(Name).where(Name.tenant_id == tenant_id).order_by(Name.is_primary.desc(), Name.id)
        )
    ).all():
        names_by_person[n.person_id].append(n)

    person_events: dict[uuid.UUID, list[Event]] = defaultdict(list)
    family_events: dict[uuid.UUID, list[Event]] = defaultdict(list)
    for ev in (await session.scalars(select(Event).where(Event.tenant_id == tenant_id).order_by(Event.id))).all():
        if ev.subject_person_id:
            person_events[ev.subject_person_id].append(ev)
        elif ev.subject_family_id:
            family_events[ev.subject_family_id].append(ev)

    children_of: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    famc_of: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    relations: dict[tuple[uuid.UUID, uuid.UUID], str] = {}
    for fc in (
        await session.scalars(
            select(FamilyChild).where(FamilyChild.tenant_id == tenant_id).order_by(FamilyChild.seq, FamilyChild.family_id, FamilyChild.person_id)
        )
    ).all():
        children_of[fc.family_id].append(fc.person_id)
        famc_of[fc.person_id].append(fc.family_id)
        relations[(fc.person_id, fc.family_id)] = fc.relation

    fams_of: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for f in families:
        if f.husband_id:
            fams_of[f.husband_id].append(f.id)
        if f.wife_id:
            fams_of[f.wife_id].append(f.id)

    pxref = {p.id: f"@I{i + 1}@" for i, p in enumerate(persons)}
    fxref = {f.id: f"@F{i + 1}@" for i, f in enumerate(families)}

    evidence = await gedcom_evidence.collect(session, tenant_id)
    emitted_targets = set()
    raw_count = unresolved = 0
    old_refs = defaultdict(list)
    for row, xrefs, kind in [(p, pxref, "INDI") for p in persons] + [(f, fxref, "FAM") for f in families]:
        if row.gedcom_xref: old_refs[row.gedcom_xref].append((kind, xrefs[row.id]))
    remap = {key: values[0] for key, values in old_refs.items() if len(values) == 1}

    def append_raw(parent, raw):
        nonlocal raw_count, unresolved
        raw_count += 1
        if version == "7.0":
            parent.children.append(gedcom_evidence.extension("_GS_LEGACY", structure=raw))
            return
        node = gedcom.from_dict(raw)
        pending, missing = [node], False
        while pending:
            child = pending.pop()
            if child.is_pointer is not False and re.fullmatch(r"@[^@]+@", child.value or ""):
                target = remap.get(child.value)
                expected = {"SOUR": "SOUR", "REPO": "REPO", "NOTE": "NOTE", "OBJE": "OBJE", "SUBM": "SUBM",
                            "HUSB": "INDI", "WIFE": "INDI", "CHIL": "INDI", "ASSO": "INDI", "ALIA": "INDI",
                            "FAMC": "FAM", "FAMS": "FAM"}.get(child.tag)
                if target and (expected is None or target[0] == expected): child.value = target[1]
                else: missing = True
            pending.extend(child.children)
        if missing:
            unresolved += 1
            # Do not emit dangling pointers or accidentally bind them to a new @S1@.
            node = Node(tag="NOTE", value="Estructura GEDCOM original con referencias no resueltas:\n" +
                        json.dumps(raw, ensure_ascii=False, sort_keys=True))
        parent.children.append(node)

    def cite(node, kind, identifier):
        emitted_targets.add((kind, identifier))
        node.children.extend(evidence.targets.get((kind, identifier), []))

    records: list[Node] = [_head_node(), Node(tag="SUBM", xref="@U1@", children=[
        Node(tag="NAME", value="gen_suite"),
        Node(tag="NOTE", value="Exportación automática; identidad del investigador no incluida.")])]

    for p in persons:
        node = Node(tag="INDI", xref=pxref[p.id])
        for name in names_by_person.get(p.id, []):
            name_node = _name_node(name)
            cite(name_node, "name", name.id)
            node.children.append(name_node)
        if p.sex in ("M", "F"):
            node.children.append(Node(tag="SEX", value=p.sex))
        for ev in person_events.get(p.id, []):
            event_node = _event_node(ev, place_name)
            cite(event_node, "event", ev.id)
            node.children.append(event_node)
        for fid in fams_of.get(p.id, []):
            node.children.append(Node(tag="FAMS", value=fxref[fid]))
        for fid in famc_of.get(p.id, []):
            link = Node(tag="FAMC", value=fxref[fid])
            relation = relations.get((p.id, fid))
            if relation in {"birth", "adopted", "foster", "sealing"}:
                link.children.append(Node(tag="PEDI", value=relation))
            elif relation:
                link.children.append(Node(tag="NOTE", value=f"Relación de filiación en gen_suite: {relation}"))
            node.children.append(link)
        cite(node, "person", p.id)
        if p.notes: node.children.append(Node(tag="NOTE", value=p.notes, is_pointer=False))
        for raw in p.raw or []:
            append_raw(node, raw)
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
            event_node = _event_node(ev, place_name)
            cite(event_node, "event", ev.id)
            node.children.append(event_node)
        cite(node, "family", f.id)
        for raw in f.raw or []:
            append_raw(node, raw)
        records.append(node)

    records.extend(evidence.records)
    records.append(Node(tag="TRLR"))
    omitted = sum(len(nodes) for target, nodes in evidence.targets.items() if target not in emitted_targets)
    exclusions = {}
    for table_name in ("documents", "transcriptions", "records", "person_mentions", "research_assertions",
                       "research_notes", "research_tags", "research_projects", "research_tasks", "research_searches"):
        table = Base.metadata.tables[table_name]
        exclusions[table_name] = await session.scalar(select(func.count()).select_from(table).where(table.c.tenant_id == tenant_id)) or 0
    report = {"format": "GEDCOM 5.5.1", "scope": "genealogy_interchange", "is_backup": False,
        "counts": {"persons": len(persons), "families": len(families), "sources": evidence.source_count,
                   "repositories": evidence.repository_count, "citations": len(evidence.citations),
                   "citations_exported": len(evidence.citations) - omitted},
        "extensions": ["_GS_SOURCE", "_GS_CITATION"], "excluded_records": exclusions,
        "unattached_citations": omitted, "relocated_child_citations": evidence.relocated,
        "raw_structures": raw_count, "unresolved_raw_references": unresolved,
        "warnings": ["No incluye imágenes, PDF, revisiones, inferencias, historial, permisos ni configuración. No es una copia de seguridad.",
                     "Los localizadores estructurados y la calidad usan extensiones privadas; otros programas pueden ignorarlas.",
                     "Los identificadores locales y enlaces a actas/páginas/revisiones no se restauran mediante GEDCOM.",
                     "Incluye personas privadas o vivas: la descarga es privada, no una publicación anonimizada."]}
    return records, report, evidence


def seal_report(content: str, report: dict) -> dict:
    report = {key: value for key, value in report.items() if key not in {"sha256", "review_token"}}
    report["sha256"] = hashlib.sha256(content.encode("utf-8")).hexdigest()
    report["review_token"] = hashlib.sha256(json.dumps(report, ensure_ascii=False, sort_keys=True,
                                                       separators=(",", ":")).encode("utf-8")).hexdigest()
    return report


async def build_export(session: AsyncSession, tenant_id: uuid.UUID) -> tuple[str, dict]:
    records, report, _ = await build_graph(session, tenant_id)
    content = gedcom.serialize(records)
    return content, seal_report(content, report)


async def export_gedcom(session: AsyncSession, tenant_id: uuid.UUID) -> str:
    content, _ = await build_export(session, tenant_id)
    return content
