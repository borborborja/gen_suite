"""GEDCOM 7 output for the modeled graph, not a general 5.x file converter.

Legacy structures are opaque documented extensions. Unsupported date grammars
remain PHRASEs: no calendar/date interpretation is guessed. See the interchange
runbook for this intentionally conservative profile and extension identifiers.
"""
from __future__ import annotations

import calendar
import copy
import re
import uuid

from .gedcom import Node
from .gedcom_evidence import extension
from .mapping import PERSON_EVENT_TAGS, FAMILY_EVENT_TAGS

# These UUID URNs identify our semantics, not FamilySearch standard structures.
EXTENSIONS = {tag: f"urn:uuid:{uuid.uuid5(uuid.UUID('e473d574-1de1-4dcf-909f-d45d044fbcf9'), tag)}"
              for tag in ("_GS_SOURCE", "_GS_CITATION", "_GS_LEGACY", "_GS_EMPTY")}
_POINTER = re.compile(r"@[A-Z0-9_]+@\Z")
_MONTHS = "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split()


def _date(value: str) -> bool:
    match = re.fullmatch(r"(?:(GREGORIAN|JULIAN) )?(?:(\d{1,2}) )?(?:([A-Z]{3}) )?(\d+)( BCE)?", value)
    if not match:
        return False
    cal, day, month, year, bce = match.groups()
    if len(year) > 9 or int(year) == 0 or (day and not month) or (month and month not in _MONTHS):
        return False
    if not day:
        return True
    year_number = 1 - int(year) if bce else int(year)
    leap = year_number % 4 == 0 if cal == "JULIAN" else calendar.isleap(year_number)
    days = [31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    return 1 <= int(day) <= days[_MONTHS.index(month)]


def date_value(value: str) -> bool:
    if value.startswith("BET ") and " AND " in value:
        return all(_date(part) for part in value[4:].split(" AND ")) and value.count(" AND ") == 1
    if value.startswith("FROM ") and " TO " in value:
        return all(_date(part) for part in value[5:].split(" TO ")) and value.count(" TO ") == 1
    for prefix in ("ABT ", "CAL ", "EST ", "BEF ", "AFT ", "FROM ", "TO "):
        if value.startswith(prefix):
            return _date(value[len(prefix):])
    return _date(value)


def convert(records: list[Node]) -> tuple[list[Node], dict]:
    """Input must come from exporter.build_graph(version='7.0'), never raw imports."""
    records = copy.deepcopy(records)
    changes = {"date_phrases": 0, "legacy_structures": 0, "name_type_phrases": 0}

    def walk(node, parent=None):
        if node.tag == "HEAD":
            node.children = [Node(tag="GEDC", children=[Node(tag="VERS", value="7.0")]),
                Node(tag="SOUR", value="gen_suite", children=[Node(tag="NAME", value="gen_suite")]),
                Node(tag="SUBM", value="@U1@", is_pointer=True)]
        if node.tag == "DATE" and node.value and not date_value(node.value):
            node.children.insert(0, Node(tag="PHRASE", value=node.value, is_pointer=False))
            node.value = None
            changes["date_phrases"] += 1
        if node.tag == "TYPE" and parent == "NAME":
            value = (node.value or "").upper()
            if value not in {"AKA", "BIRTH", "IMMIGRANT", "MAIDEN", "MARRIED", "PROFESSIONAL", "OTHER"}:
                node.children = [Node(tag="PHRASE", value=node.value or "(sin tipo)")]
                value = "OTHER"
                changes["name_type_phrases"] += 1
            node.value = value
        if node.tag == "PEDI":
            node.value = (node.value or "OTHER").upper()
        if node.tag == "SOUR" and not node.xref and parent != "HEAD" and not _POINTER.fullmatch(node.value or ""):
            description = node.value
            node.value, node.is_pointer = "@VOID@", True
            if description:
                node.children.insert(0, Node(tag="NOTE", value=description, is_pointer=False))
            node.children = [Node(tag="DATA", children=[c]) if c.tag == "TEXT" else c for c in node.children]
        if node.tag == "REPO" and not node.xref and not node.value:
            node.value, node.is_pointer = "@VOID@", True
        if parent in {"INDI", "FAM"} and node.tag in PERSON_EVENT_TAGS | FAMILY_EVENT_TAGS:
            allowed = PERSON_EVENT_TAGS if parent == "INDI" else FAMILY_EVENT_TAGS | {"FACT"}
            if node.tag not in allowed:
                original_tag = node.tag
                node.tag = "EVEN"
                node.children.insert(0, Node(tag="TYPE", value=original_tag))
            if node.tag in {"EVEN", "FACT"} and not node.first("TYPE"):
                node.children.insert(0, Node(tag="TYPE", value="Hecho sin clasificación en gen_suite"))
        if node.tag == "_GS_LEGACY":
            changes["legacy_structures"] += 1
        for child in node.children:
            walk(child, node.tag)
        if not node.value and not node.children and node.tag not in {"HEAD", "TRLR"}:
            # Preserve an empty modeled record without asserting an event occurred (Y).
            node.children.append(extension("_GS_EMPTY", empty=True))

    for record in records:
        walk(record)
    used = set()
    pending = list(records)
    while pending:
        node = pending.pop()
        if node.tag.startswith("_"):
            if node.tag not in EXTENSIONS:
                raise ValueError("Extensión sin definición GEDCOM 7")
            used.add(node.tag)
        pending.extend(node.children)
    if used:
        records[0].children.insert(1, Node(tag="SCHMA", children=[
            Node(tag="TAG", value=f"{tag} {EXTENSIONS[tag]}") for tag in sorted(used)]))
    return records, {**changes, "extensions": sorted(used)}


def serialize(records: list[Node]) -> str:
    lines = []
    refs = {n.xref: n.tag for n in records if n.xref}
    if len(refs) != sum(bool(n.xref) for n in records) or "@VOID@" in refs:
        raise ValueError("Identificadores GEDCOM 7 duplicados o reservados")

    def emit(node, level):
        if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", node.tag) or node.tag in {"CONC", "CHAR"}:
            raise ValueError("Etiqueta no admitida en GEDCOM 7")
        if node.xref and (level or not _POINTER.fullmatch(node.xref)):
            raise ValueError("Identificador GEDCOM 7 no válido")
        prefix = f"{level} " + (f"{node.xref} " if node.xref else "") + node.tag
        value = node.value or ""
        if any((ord(c) < 32 and c not in "\t\r\n") or 0x7F <= ord(c) <= 0x9F or
               0xD800 <= ord(c) <= 0xDFFF or ord(c) in {0xFFFE, 0xFFFF} for c in value):
            raise ValueError("Texto con caracteres no representables en GEDCOM 7")
        pointer = node.is_pointer is True or (node.is_pointer is None and node.tag in {
            "SOUR", "REPO", "SUBM", "FAMC", "FAMS", "HUSB", "WIFE", "CHIL", "OBJE"
        } and bool(_POINTER.fullmatch(value)))
        if pointer:
            expected = {"SOUR": "SOUR", "REPO": "REPO", "SUBM": "SUBM", "OBJE": "OBJE",
                        "FAMC": "FAM", "FAMS": "FAM", "HUSB": "INDI", "WIFE": "INDI", "CHIL": "INDI"}.get(node.tag)
            if not _POINTER.fullmatch(value) or (value != "@VOID@" and (value not in refs or (expected and refs[value] != expected))):
                raise ValueError("Referencia GEDCOM 7 sin destino compatible")
            lines.append(prefix + " " + value)
        else:
            parts = value.replace("\r\n", "\n").replace("\r", "\n").split("\n")
            for index, part in enumerate(parts):
                escaped = "@" + part if part.startswith("@") else part
                start = f"{level + 1} CONT" if index else prefix
                lines.append(start + (" " + escaped if escaped else ""))
        for child in node.children:
            emit(child, level + 1)

    if not records or records[0].tag != "HEAD" or records[-1].tag != "TRLR":
        raise ValueError("Falta cabecera o final GEDCOM 7")
    for record in records:
        emit(record, 0)
    return "\n".join(lines) + "\n"
