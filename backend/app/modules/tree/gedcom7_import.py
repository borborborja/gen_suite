"""Strict GEDCOM 7 syntax, explicit projection into the current genealogy model.

This is not a complete validator for every standard/extension structure. Syntax,
record references and dangerous ambiguities are checked before any DB write.
Projection differences are reported; the original input must be retained separately.
"""
from __future__ import annotations

import copy
from collections import Counter
import re

from .gedcom import Node
from .gedcom7 import EXTENSIONS
from .gedcom_evidence import MISSING_SOURCE, extension, metadata
from .mapping import ALL_EVENT_TAGS, PERSON_EVENT_TAGS, FAMILY_EVENT_TAGS

_LINE = re.compile(r"^(0|[1-9][0-9]{0,2}) (?:(@[A-Z0-9_]+@) )?([A-Z][A-Z0-9_]*|_[A-Z0-9_]+)(?: (.*))?$")
_PTR = re.compile(r"@[A-Z0-9_]+@\Z")


class ImportError(ValueError):
    """Safe Spanish text, no storage paths, credentials or raw genealogy content."""


def parse(data: bytes, *, max_bytes=32 * 1024**2, max_nodes=100_000, max_depth=64) -> list[Node]:
    if not data or len(data) > max_bytes:
        raise ImportError("El GEDCOM está vacío o supera el límite de lectura.")
    try:
        value = data.decode("utf-8-sig", errors="strict")
    except UnicodeError:
        raise ImportError("GEDCOM 7 requiere UTF-8 válido; no se ha sustituido texto ilegible.") from None
    if any((ord(c) < 32 and c not in "\t\r\n") or 0x7F <= ord(c) <= 0x9F or ord(c) in {0xFFFE, 0xFFFF} for c in value):
        raise ImportError("El GEDCOM contiene caracteres de control no admitidos.")
    if not value.endswith(("\r", "\n")):
        raise ImportError("Falta el final de línea obligatorio del GEDCOM.")
    roots, stack, chunks = [], [], {}
    count = 0
    for number, line in enumerate(re.split(r"\r\n|\r|\n", value)[:-1], 1):
        count += 1
        if count > max_nodes:
            raise ImportError("El GEDCOM supera el límite de estructuras de importación.")
        match = _LINE.fullmatch(line)
        if not match:
            raise ImportError(f"Sintaxis GEDCOM 7 no válida en la línea {number}.")
        level, xref, tag, payload = int(match[1]), match[2], match[3], match[4]
        if len(line) > 1024**2 or len(tag) > 128 or len(xref or "") > 4096:
            raise ImportError("Una línea o identificador supera el límite de este perfil de importación.")
        if level > max_depth or level > len(stack):
            raise ImportError(f"Jerarquía GEDCOM no válida en la línea {number}.")
        if (xref and (level or xref == "@VOID@")) or tag == "CONC":
            raise ImportError(f"Identificador o continuación no admitidos en GEDCOM 7, línea {number}.")
        pointer = bool(_PTR.fullmatch(payload or ""))
        if payload and payload.startswith("@"):
            if payload.startswith("@@"):
                payload, pointer = payload[1:], False
            elif not pointer:
                raise ImportError(f"Arroba inicial sin escape en la línea {number}.")
        if tag == "CONT":
            if not level or xref or pointer or not stack or level != len(stack):
                raise ImportError(f"Continuación GEDCOM no válida en la línea {number}.")
            parent = stack[-1]
            if parent.is_pointer or parent.children:
                raise ImportError("Las continuaciones deben preceder a las subestructuras y no continuar punteros.")
            chunks.setdefault(id(parent), [parent.value or ""]).append(payload or "")
            continue
        stack = stack[:level]
        node = Node(tag, payload, xref, is_pointer=pointer)
        if stack:
            stack[-1].children.append(node)
        else:
            roots.append(node)
        stack.append(node)
    pending = list(roots)
    while pending:
        node = pending.pop()
        if id(node) in chunks:
            node.value = "\n".join(chunks[id(node)])
        if not node.value and not node.children and node.tag not in {"HEAD", "TRLR"}:
            raise ImportError("Hay una estructura vacía sin información ni subestructuras.")
        pending.extend(node.children)
    if not roots or roots[0].tag != "HEAD" or roots[-1].tag != "TRLR" or any(n.tag in {"HEAD", "TRLR"} for n in roots[1:-1]):
        raise ImportError("El GEDCOM debe tener una única cabecera y un único final, en orden.")
    if roots[0].xref or roots[0].value or roots[-1].xref or roots[-1].value or roots[-1].children:
        raise ImportError("Cabecera o final GEDCOM no válidos.")
    headers = roots[0].all("GEDC")
    if len(headers) != 1 or len(headers[0].all("VERS")) != 1 or headers[0].value_of("VERS") != "7.0":
        raise ImportError("Este importador admite la versión GEDCOM 7.0.")
    refs = {}
    for node in roots:
        if node.tag not in {"HEAD", "TRLR", "INDI", "FAM", "SOUR", "REPO", "OBJE", "SNOTE", "SUBM"} and not node.tag.startswith("_"):
            raise ImportError("El archivo contiene un tipo de registro no admitido en GEDCOM 7.")
        if node.xref:
            if node.xref in refs:
                raise ImportError("El GEDCOM contiene identificadores duplicados.")
            refs[node.xref] = node
    pending = [(node, None) for node in roots]
    while pending:
        node, parent = pending.pop()
        if node.is_pointer and node.value != "@VOID@":
            target = refs.get(node.value)
            if not target:
                raise ImportError("El GEDCOM contiene un puntero sin destino en el mismo archivo.")
            expected = {"FAMC": "FAM", "FAMS": "FAM", "HUSB": "INDI", "WIFE": "INDI", "CHIL": "INDI",
                "SNOTE": "SNOTE", "SOUR": "SOUR", "REPO": "REPO", "OBJE": "OBJE", "SUBM": "SUBM",
                "ASSO": "INDI", "ALIA": "INDI", "ANCI": "SUBM", "DESI": "SUBM"}.get(node.tag)
            if expected and target.tag != expected:
                raise ImportError("Un puntero GEDCOM tiene un destino de tipo incompatible.")
        if parent and node.tag in {"FAMC", "FAMS", "CHIL", "SNOTE", "OBJE", "SUBM", "ALIA", "ASSO", "ANCI", "DESI"} and not node.is_pointer:
            raise ImportError("Una relación GEDCOM requiere un puntero, no un texto.")
        if parent and node.tag in {"SOUR", "REPO"} and parent.tag != "HEAD" and not node.is_pointer:
            raise ImportError("GEDCOM 7 requiere punteros en sus citas bibliográficas.")
        if parent and parent.tag == "FAM" and node.tag in {"HUSB", "WIFE"} and not node.is_pointer:
            raise ImportError("Una pareja de la familia requiere un puntero, no un texto.")
        pending.extend((child, node) for child in node.children)
    # Missing xrefs on unreferenced records are legal in 7.0; give the model local
    # identities without interpreting any of them as database UUIDs.
    for index, node in enumerate(roots):
        if node.tag not in {"HEAD", "TRLR"} and not node.xref:
            candidate = f"@_AUTO{index}@"
            while candidate in refs:
                candidate = candidate[:-1] + "_@"
            node.xref, refs[candidate] = candidate, node
    return roots


def project(roots: list[Node]) -> tuple[list[Node], dict]:
    """Produce model-ready nodes without relabeling the input as GEDCOM 5.x."""
    nodes = copy.deepcopy(roots)
    report = {"format": "GEDCOM 7.0", "mode": "append_private", "is_backup_restore": False,
        "counts": {tag: sum(n.tag == tag for n in nodes) for tag in ("INDI", "FAM", "SOUR", "REPO", "OBJE", "SNOTE")},
        "changes": [], "change_counts": {}, "warnings": [
            "Se añadirán personas y familias nuevas, privadas; no se fusionan ni sustituyen las existentes.",
            "Se conserva el archivo original. El modelo no representa toda la información GEDCOM: revisa las transformaciones antes de incorporar el árbol.",
            "No se acredita la veracidad de citas, transcripciones o relaciones; no se ejecuta IA ni se publican documentos."]}
    counts = Counter()

    def change(code, ref, path):
        counts[code] += 1
        if len(report["changes"]) < 5000:
            report["changes"].append({"code": code, "record": ref[:256] if ref else None, "path": path[:2048]})

    refs = {n.xref for n in nodes if n.xref}
    remap = {}
    for index, node in enumerate(nodes):
        if node.xref and len(node.xref) > 64:
            target = f"@_SHORT{index}@"
            while target in refs: target = target[:-1] + "_@"
            refs.add(target)
            remap[node.xref] = target
            change("identifier_remapped", node.xref, node.tag)
            node.xref = target
    pending = list(nodes)
    while pending:
        node = pending.pop()
        if node.is_pointer: node.value = remap.get(node.value, node.value)
        pending.extend(node.children)

    declarations = {}
    for schema in nodes[0].all("SCHMA"):
        for tag in schema.all("TAG"):
            if tag.value and " " in tag.value:
                name, uri = tag.value.split(" ", 1)
                if name.startswith("_"):
                    declarations.setdefault(name, set()).add(uri)
    aliases = {name: standard for name, uris in declarations.items() for standard, uri in EXTENSIONS.items() if uris == {uri}}
    # A private tag with someone else's meaning must never overwrite our fields.
    pending = [(n, n.xref, n.tag) for n in nodes]
    while pending:
        node, ref, path = pending.pop()
        if node.tag in aliases:
            node.tag = aliases[node.tag]
        elif node.tag.startswith("_GS_"):
            change("uninterpreted_extension", ref, path)
            node.tag = "_IMPORTED" + node.tag
        pending.extend((c, ref, path + "." + c.tag) for c in node.children)

    shared = {n.xref: n for n in nodes if n.tag == "SNOTE"}
    family_children = {n.xref: {child.value for child in n.all("CHIL")} for n in nodes if n.tag == "FAM"}
    # No recursive expansion: cycles cannot explode memory, and identity loss is explicit.
    for root in nodes:
        # Reject ambiguous singleton fields instead of silently taking the first.
        singleton = {"INDI": {"SEX"}, "FAM": {"HUSB", "WIFE"}}.get(root.tag, set())
        if any(len(root.all(tag)) > 1 for tag in singleton):
            raise ImportError("Hay campos únicos repetidos; resuelve la ambigüedad antes de importar.")
        pending = [(root, root.tag)]
        while pending:
            node, path = pending.pop()
            for index, child in enumerate(node.children):
                if child.tag == "SNOTE":
                    record = shared.get(child.value)
                    node.children[index] = Node("NOTE", record.value if record else "Nota compartida sin contenido identificado", is_pointer=False)
                    change("shared_note_copied_as_text", root.xref, path + ".SNOTE")
                else:
                    pending.append((child, path + "." + child.tag))

    for root in nodes:
        if root.tag not in {"HEAD", "TRLR", "INDI", "FAM", "SOUR", "REPO", "OBJE"}:
            change("record_retained_only_in_original", root.xref, root.tag)
        if root.tag in {"SOUR", "REPO"}:
            limits = {"SOUR": {"TITL": 512, "AUTH": 255}, "REPO": {"NAME": 255, "WWW": 1024}}[root.tag]
            for tag, limit in limits.items():
                if len(root.value_of(tag) or "") > limit: change("bibliography_field_truncated", root.xref, root.tag + "." + tag)
                if len(root.all(tag)) > 1: change("bibliography_repeated_field_original_only", root.xref, root.tag + "." + tag)
            if root.tag == "SOUR" and len(root.all("REPO")) > 1:
                change("additional_repositories_original_only", root.xref, "SOUR.REPO")
        if root.tag == "INDI":
            sex = root.first("SEX")
            if sex and sex.value not in {"M", "F", "U"}:
                change("sex_not_modeled", root.xref, "INDI.SEX")
            for name in root.all("NAME"):
                if any(len(name.all(tag)) > 1 for tag in ("GIVN", "SURN", "SPFX", "NICK", "TYPE")):
                    raise ImportError("Un nombre contiene campos únicos repetidos.")
                if name.value:
                    change("name_payload_projected_to_parts", root.xref, "INDI.NAME")
                for child in name.children:
                    if child.tag not in {"GIVN", "SURN", "SPFX", "NICK", "TYPE", "SOUR"}:
                        change("structure_retained_only_in_original", root.xref, "INDI.NAME." + child.tag)
                kind = name.first("TYPE")
                if kind:
                    kind.value = (kind.value_of("PHRASE") if kind.value == "OTHER" else None) or (kind.value or "birth").lower()
                    if len(kind.value) > 16: change("name_type_truncated", root.xref, "INDI.NAME.TYPE")
                for tag, limit in (("GIVN", 255), ("SURN", 255), ("SPFX", 64), ("NICK", 128)):
                    if len(name.value_of(tag) or "") > limit: change("name_field_truncated", root.xref, "INDI.NAME." + tag)
            for link in root.all("FAMC"):
                if link.value in family_children and root.xref not in family_children[link.value]:
                    change("nonreciprocal_child_link_original_only", root.xref, "INDI.FAMC")
                kind = link.first("PEDI")
                if kind:
                    kind.value = (kind.value_of("PHRASE") if kind.value == "OTHER" else None) or (kind.value or "birth").lower()
                    if len(kind.value) > 16: change("pedigree_truncated", root.xref, "INDI.FAMC.PEDI")
        for event in root.children if root.tag in {"INDI", "FAM"} else []:
            if event.tag not in ALL_EVENT_TAGS:
                continue
            if any(len(event.all(tag)) > 1 for tag in ("DATE", "PLAC", "TYPE")):
                raise ImportError("Un evento contiene fechas, lugares o tipos repetidos.")
            for child in event.children:
                if child.tag not in {"DATE", "PLAC", "TYPE", "SOUR"} or child.children and child.tag == "PLAC":
                    change("structure_retained_only_in_original", root.xref, root.tag + "." + event.tag + "." + child.tag)
            if len(event.value_of("PLAC") or "") > 512:
                raise ImportError("Un lugar supera 512 caracteres; abrevia su nombre antes de importar para evitar colisiones.")
            if len(event.value_of("TYPE") or "") > 24:
                change("event_type_truncated", root.xref, root.tag + "." + event.tag + ".TYPE")
            date = event.first("DATE")
            if date and date.value_of("PHRASE"):
                if not date.value:
                    date.value = date.value_of("PHRASE")
                    change("date_phrase_used_as_raw_date", root.xref, root.tag + "." + event.tag + ".DATE")
                else:
                    change("date_phrase_retained_only_in_original", root.xref, root.tag + "." + event.tag + ".DATE.PHRASE")
            if date and len(date.value or "") > 128:
                change("date_truncated", root.xref, root.tag + "." + event.tag + ".DATE")
            if date and any(marker in (date.value or "") for marker in ("BCE", "JULIAN", "HEBREW", "FRENCH_R")):
                change("calendar_preserved_as_text_without_year_inference", root.xref, root.tag + "." + event.tag + ".DATE")
        pending = [(root, None, root.tag)]
        while pending:
            node, parent, path = pending.pop()
            if node.tag == "OBJE" and parent and root.tag != "SOUR":
                change("media_association_retained_only_in_original", root.xref, path)
            if node.tag == "SOUR" and parent and parent.tag != "HEAD" and node.value == "@VOID@":
                meta = metadata(node, "_GS_CITATION")
                node.value, node.is_pointer = MISSING_SOURCE, False
                node.children = [c for c in node.children if c.tag != "_GS_CITATION"]
                node.children.append(extension("_GS_CITATION", **{k: v for k, v in meta.items() if k not in {"version", "source_missing"}}, source_missing=True))
                if node.children and node.children[0].tag == "NOTE" and node.children[0].value == MISSING_SOURCE:
                    node.children.pop(0)
            if node.tag == "REPO" and parent and node.value == "@VOID@":
                node.value, node.is_pointer = None, False
            pending.extend((c, node, path + "." + c.tag) for c in node.children)
        if root.tag == "FAM":
            children = [n.value for n in root.all("CHIL") if n.value != "@VOID@"]
            if len(children) != len(set(children)):
                raise ImportError("Una familia repite el mismo hijo; resuelve la ambigüedad antes de importar.")
            if any(n.value == "@VOID@" for n in root.all("CHIL")):
                change("unknown_child_order_not_modeled", root.xref, "FAM.CHIL")
    # Always quantify citations outside current exact-target support.
    all_citations = modeled_citations = 0
    for root in nodes:
        pending = [(root, None, None)]
        while pending:
            node, parent, grandparent = pending.pop()
            if node.tag == "SOUR" and parent and parent.tag != "HEAD":
                all_citations += 1
                if parent is root and root.tag in {"INDI", "FAM"} or grandparent is root and (
                    root.tag == "INDI" and parent.tag in PERSON_EVENT_TAGS | {"NAME"} or
                    root.tag == "FAM" and parent.tag in FAMILY_EVENT_TAGS):
                    modeled_citations += 1
                else:
                    change("citation_retained_only_in_original", root.xref, (parent.tag if parent else "") + ".SOUR")
            pending.extend((child, node, parent) for child in node.children)
    report["counts"].update({"citations": all_citations, "citations_modeled": modeled_citations})
    report["warnings"].append("El inventario de transformaciones no es una validación completa del estándar. Metadatos de eventos, nombres, asociaciones multimedia fuera de fuentes y extensiones pueden quedar solo en el original o en datos sin editor específico.")
    report["change_counts"] = dict(counts)
    report["changes_truncated"] = sum(counts.values()) > len(report["changes"])
    return nodes, report
