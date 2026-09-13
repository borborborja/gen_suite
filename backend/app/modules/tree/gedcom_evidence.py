"""Bibliography and exact citation targets for GEDCOM 5.5.1 interchange.

Private _GS_* extensions preserve locator/quality semantics; QUAY is deliberately
not inferred from 'original': originality does not establish reliability.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
import json
import re
import uuid

from fastapi import HTTPException
from sqlalchemy import select

from ...models.citation import Citation
from ...models.document import Document, Page
from ...models.record import Record
from ...models.research import Repository, Source
from ...models.transcription import Transcription
from ...models.tree_import import SourceDocument
from .gedcom import Node

MISSING_SOURCE = "Referencia sin fuente bibliográfica identificada"
CHILD_NOTE = "Cita sobre un vínculo de filiación; la familia concreta no está identificada en esta referencia."


def extension(tag: str, **values) -> Node:
    return Node(tag=tag, value=json.dumps({"version": 1, **values}, ensure_ascii=False, sort_keys=True))


def metadata(node: Node, tag: str) -> dict:
    raw = node.value_of(tag)
    if not raw or len(raw) > 512_000:
        return {}
    try:
        value = json.loads(raw)
    except (ValueError, RecursionError):
        return {}
    return value if isinstance(value, dict) and type(value.get("version")) is int and value["version"] == 1 else {}


def locator_text(locator: dict | None, page_no: int | None) -> str | None:
    if locator:
        if isinstance(locator.get("gedcom_page"), str):
            return locator["gedcom_page"]
        return "; ".join(f"{key}: {value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)}"
                         for key, value in sorted(locator.items()))
    return f"pág. {page_no}" if page_no else None


def citation_node(citation, source_xref: str | None, page_no: int | None = None) -> Node:
    node = Node(tag="SOUR", value=source_xref or MISSING_SOURCE)
    locator = locator_text(citation.locator, page_no)
    # PAGE belongs to pointer citations. Inline TEXT is quoted evidence, not a locator.
    if locator:
        node.children.append(Node(tag="PAGE" if source_xref else "NOTE",
                                  value=locator if source_xref else f"Localizador: {locator}", is_pointer=False))
    if citation.quote:
        quote = Node(tag="TEXT", value=citation.quote)
        node.children.append(Node(tag="DATA", children=[quote]) if source_xref else quote)
    if citation.note:
        node.children.append(Node(tag="NOTE", value=citation.note, is_pointer=False))
    node.children.append(extension("_GS_CITATION", locator=citation.locator,
        quality=citation.quality, source_missing=source_xref is None,
        target_type=citation.target_type))
    return node


@dataclass
class Evidence:
    records: list[Node] = field(default_factory=list)
    targets: dict = field(default_factory=lambda: defaultdict(list))
    citations: list = field(default_factory=list)
    source_count: int = 0
    repository_count: int = 0
    relocated: int = 0
    source_documents: dict = field(default_factory=lambda: defaultdict(set))


async def collect(session, tenant_id) -> Evidence:
    result = Evidence()
    sources = list((await session.scalars(select(Source).where(Source.tenant_id == tenant_id)
                                         .order_by(Source.id))).all())
    repositories = list((await session.scalars(select(Repository).where(Repository.tenant_id == tenant_id)
                                              .order_by(Repository.id))).all())
    result.citations = list((await session.scalars(select(Citation).where(Citation.tenant_id == tenant_id)
                                                  .order_by(Citation.id))).all())
    result.source_count, result.repository_count = len(sources), len(repositories)
    sxrefs = {row.id: f"@S{n + 1}@" for n, row in enumerate(sources)}
    for row in sources:
        if row.primary_document_id:
            result.source_documents[sxrefs[row.id]].add(row.primary_document_id)
    for link in (await session.scalars(select(SourceDocument).where(SourceDocument.tenant_id == tenant_id))).all():
        if link.source_id in sxrefs:
            result.source_documents[sxrefs[link.source_id]].add(link.document_id)
    rxrefs = {row.id: f"@R{n + 1}@" for n, row in enumerate(repositories)}
    for row in repositories:
        children = [Node(tag="NAME", value=row.name)]
        for tag, value in (("ADDR", row.address), ("WWW", row.url), ("NOTE", row.notes)):
            if value: children.append(Node(tag=tag, value=value, is_pointer=False))
        result.records.append(Node(tag="REPO", xref=rxrefs[row.id], children=children))
    for row in sources:
        children = [Node(tag="TITL", value=row.title)]
        for tag, value in (("AUTH", row.author), ("PUBL", row.publication_info)):
            if value: children.append(Node(tag=tag, value=value))
        if row.repository_id in rxrefs:
            repository = Node(tag="REPO", value=rxrefs[row.repository_id])
            if row.call_number: repository.children.append(Node(tag="CALN", value=row.call_number))
            children.append(repository)
        elif row.call_number:
            children.append(Node(tag="REPO", children=[Node(tag="CALN", value=row.call_number)]))
        if row.url: children.append(Node(tag="NOTE", value=f"URL de la fuente: {row.url}"))
        children.append(extension("_GS_SOURCE", source_type=row.source_type, url=row.url,
                                  call_number=row.call_number, raw=row.raw))
        # Original standard notes remain useful in applications ignoring our extensions.
        original = (row.raw or {}).get("gedcom_record", {})
        for child in original.get("children", []) if isinstance(original, dict) else []:
            if isinstance(child, dict) and child.get("tag") == "NOTE" and isinstance(child.get("value"), str) and child["value"]:
                value = child["value"]
                if value not in {c.value for c in children if c.tag == "NOTE"}:
                    children.append(Node(tag="NOTE", value=value, is_pointer=False))
        result.records.append(Node(tag="SOUR", xref=sxrefs[row.id], children=children))

    async def rows(model, ids):
        return {r.id: r for r in (await session.scalars(select(model).where(model.id.in_(ids)))).all()} if ids else {}
    # These secondary reads retain RLS: referenced public evidence may be readable,
    # but unreferenced documents from other spaces are never enumerated.
    records = await rows(Record, {c.record_id for c in result.citations if c.record_id})
    transcriptions = await rows(Transcription, {c.transcription_id for c in result.citations if c.transcription_id})
    pages = await rows(Page, {c.page_id for c in result.citations if c.page_id}
                       | {r.page_id for r in records.values() if r.page_id})
    documents = await rows(Document, {r.document_id for r in records.values()}
                           | {t.document_id for t in transcriptions.values()} | {p.document_id for p in pages.values()})
    legacy_xrefs = {}
    for citation in result.citations:
        record, transcript = records.get(citation.record_id), transcriptions.get(citation.transcription_id)
        page = pages.get(citation.page_id) or (pages.get(record.page_id) if record else None)
        doc_id = (record.document_id if record else None) or (transcript.document_id if transcript else None) or (page.document_id if page else None)
        source_xref = sxrefs.get(citation.source_id)
        if not source_xref and doc_id in documents:
            if doc_id not in legacy_xrefs:
                legacy_xrefs[doc_id] = f"@S{len(sxrefs) + len(legacy_xrefs) + 1}@"
                result.records.append(Node(tag="SOUR", xref=legacy_xrefs[doc_id],
                    children=[Node(tag="TITL", value=documents[doc_id].title)]))
            source_xref = legacy_xrefs[doc_id]
        node = citation_node(citation, source_xref, page.page_no if page else (transcript.page_no if transcript else None))
        if source_xref and doc_id:
            result.source_documents[source_xref].add(doc_id)
        target_type = citation.target_type
        if target_type == "family_child":
            # The legacy polymorphic ID names the child, not a particular family.
            # Never choose the first family or imply that all its members are supported.
            target_type = "person"
            result.relocated += 1
            node.children.append(Node(tag="NOTE", value=CHILD_NOTE))
        result.targets[(target_type, citation.target_id)].append(node)
    return result


def validate_import(roots, *, version="5.5.1"):
    for node in roots:
        if node.tag == "HEAD" and node.first("GEDC"):
            actual_version = node.first("GEDC").value_of("VERS")
            if actual_version and actual_version not in ({"7.0"} if version == "7.0" else {"5.5", "5.5.1"}):
                raise HTTPException(422, "La versión no corresponde a este importador. Usa la importación revisada para GEDCOM 7/GEDZIP; no se ha importado.")
    seen = set()
    for node in roots:
        if node.xref:
            if node.xref in seen: raise HTTPException(422, "GEDCOM contiene identificadores duplicados; no se ha importado.")
            seen.add(node.xref)
        if node.tag in {"INDI", "FAM", "SOUR", "REPO"} and not node.xref:
            raise HTTPException(422, "Un registro GEDCOM no tiene identificador; no se ha importado.")
    sources = {n.xref for n in roots if n.tag == "SOUR"}
    repositories = {n.xref for n in roots if n.tag == "REPO"}
    pending = [(n, True) for n in roots]
    while pending:
        node, root = pending.pop()
        if not root and node.tag in {"SOUR", "REPO"} and node.is_pointer is not False and re.fullmatch(r"@[^@\s]+@", node.value or ""):
            allowed = sources if node.tag == "SOUR" else repositories
            if node.value not in allowed:
                raise HTTPException(422, "GEDCOM contiene una referencia bibliográfica sin destino; no se ha importado.")
        pending.extend((child, False) for child in node.children)


async def import_sources(session, tenant_id, roots):
    from .gedcom import to_dict
    repositories = {}
    for node in roots:
        if node.tag != "REPO": continue
        row = Repository(id=uuid.uuid4(), tenant_id=tenant_id, name=(node.value_of("NAME") or "Repositorio sin nombre")[:255],
            url=(node.value_of("WWW") or "")[:1024] or None, address=node.value_of("ADDR"),
            notes="\n\n".join(n.value for n in node.all("NOTE") if n.value) or None)
        session.add(row); repositories[node.xref] = row
    await session.flush()
    sources = {}
    for node in roots:
        if node.tag != "SOUR": continue
        meta = metadata(node, "_GS_SOURCE")
        ref = node.first("REPO")
        repository = repositories.get(ref.value) if ref else None
        def string(value, length):
            return value[:length] if isinstance(value, str) and value else None
        row = Source(id=uuid.uuid4(), tenant_id=tenant_id, title=(node.value_of("TITL") or "Fuente sin título")[:512],
            source_type=string(meta.get("source_type"), 32) or "document",
            author=string(node.value_of("AUTH"), 255), publication_info=node.value_of("PUBL"),
            call_number=string(meta.get("call_number"), 255) or string(ref.value_of("CALN") if ref else node.value_of("CALN"), 255),
            url=string(meta.get("url"), 1024), repository_id=repository.id if repository else None,
            raw=meta.get("raw") if "raw" in meta and (meta["raw"] is None or isinstance(meta["raw"], dict))
                else {"gedcom_record": to_dict(node)})
        session.add(row); sources[node.xref] = row
    await session.flush()
    return sources, len(repositories)


async def import_citations(session, tenant_id, parent: Node, target_type, target_id, sources) -> tuple[int, int]:
    count = created_sources = 0
    for node in parent.all("SOUR"):
        meta = metadata(node, "_GS_CITATION")
        source = sources.get(node.value) if node.is_pointer is not False else None
        missing_source = source is None and meta.get("source_missing") is True and node.value == MISSING_SOURCE
        if source is None and not missing_source:
            # 5.5.1 also admits an inline source description, not just pointers.
            source = Source(id=uuid.uuid4(), tenant_id=tenant_id, title=(node.value or "Fuente sin descripción")[:512],
                            source_type="other", raw={"gedcom_record": {"inline_description": node.value}})
            session.add(source)
            # No ORM relationship is configured here to order INSERTs automatically.
            # Materialise the inline source before queuing the citation's foreign key.
            await session.flush()
            created_sources += 1
        locator = dict(meta["locator"]) if isinstance(meta.get("locator"), dict) else None
        if node.value_of("PAGE") and node.value_of("PAGE") != locator_text(locator, None):
            # Keep an externally edited standard locator rather than silently replacing
            # it with the older extension; retain structured context alongside it.
            locator = {**(locator or {}), "gedcom_page": node.value_of("PAGE")}
        quality = meta.get("quality")
        if not isinstance(quality, str) or len(quality) > 16: quality = None
        data = node.first("DATA")
        quote = "\n\n".join(n.value for n in (data.all("TEXT") if data else node.all("TEXT")) if n.value) or None
        notes = [n.value for n in node.all("NOTE") if n.value]
        generated_locator = f"Localizador: {locator_text(locator, None)}"
        if missing_source and notes and notes[0] == generated_locator:
            notes.pop(0)
        if target_type == "person" and meta.get("target_type") == "family_child" and notes and notes[-1] == CHILD_NOTE:
            notes.pop()
        note = "\n\n".join(notes) or None
        original_target = "family_child" if target_type == "person" and meta.get("target_type") == "family_child" else target_type
        session.add(Citation(tenant_id=tenant_id, target_type=original_target, target_id=target_id,
            source_id=source.id if source else None, locator=locator, quote=quote, quality=quality, note=note))
        count += 1
    return count, created_sources
