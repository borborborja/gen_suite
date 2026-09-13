"""Dependency-free GEDCOM 5.5/5.5.1 tokenizer + serializer.

GEDCOM is a simple line grammar: ``LEVEL [@XREF@] TAG [VALUE]``, with CONC/CONT lines
continuing the previous value. We parse into a generic Node tree (so unmapped/custom tags
survive round-trip) and can serialize a Node tree back to GEDCOM text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_LINE = re.compile(r"^(\d+)\s+(?:(@[^@]+@)\s+)?(\S+)(?:\s(.*))?$")


@dataclass
class Node:
    tag: str
    value: str | None = None
    xref: str | None = None
    children: list["Node"] = field(default_factory=list)
    is_pointer: bool | None = None

    def first(self, tag: str) -> "Node | None":
        return next((c for c in self.children if c.tag == tag), None)

    def all(self, tag: str) -> list["Node"]:
        return [c for c in self.children if c.tag == tag]

    def value_of(self, tag: str) -> str | None:
        child = self.first(tag)
        return child.value if child else None


def detect_encoding(data: bytes) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16"
    head = data[:16384].decode("latin-1", "replace")
    m = re.search(r"^\s*[12]\s+CHAR\s+(\S+)", head, re.MULTILINE)
    char = m.group(1).strip().upper() if m else "UTF-8"
    return {
        "UTF-8": "utf-8",
        "UTF8": "utf-8",
        "UNICODE": "utf-16",
        "ASCII": "ascii",
        "ANSI": "cp1252",
        "ANSEL": "ansel",
    }.get(char, "utf-8")


def _decode(data: bytes, encoding: str) -> str:
    if encoding == "ansel":
        # No stdlib ANSEL codec; latin-1 preserves bytes 1:1 (best-effort, documented).
        return data.decode("latin-1", "replace")
    try:
        return data.decode(encoding)
    except (LookupError, UnicodeDecodeError):
        return data.decode("utf-8", "replace")


def parse(data: bytes) -> tuple[list[Node], str]:
    """Parse GEDCOM bytes into top-level (level-0) records + the detected encoding."""
    encoding = detect_encoding(data)
    text = _decode(data, encoding)
    roots: list[Node] = []
    stack: list[tuple[int, Node]] = []  # (level, node)

    for raw_line in text.splitlines():
        line = raw_line.lstrip(" \t")
        if not line.strip():
            continue
        m = _LINE.match(line)
        if not m:
            continue
        level, xref, tag, value = int(m.group(1)), m.group(2), m.group(3), m.group(4)

        if tag in ("CONC", "CONT"):
            while stack and stack[-1][0] >= level:
                stack.pop()
            if stack:
                parent = stack[-1][1]
                parent.value = (parent.value or "") + ("\n" if tag == "CONT" else "") + (value or "")
            continue

        node = Node(tag=tag, value=value, xref=xref,
                    is_pointer=bool(re.fullmatch(r"@[^@\s]+@", value or "")))
        while stack and stack[-1][0] >= level:
            stack.pop()
        if stack:
            stack[-1][1].children.append(node)
        else:
            roots.append(node)
        stack.append((level, node))

    pending = list(roots)
    while pending:
        node = pending.pop()
        if node.value is not None: node.value = node.value.replace("@@", "@")
        pending.extend(node.children)
    return roots, encoding


def serialize(records: list[Node]) -> str:
    """Serialize 5.5.1, retaining whitespace and folding long values with CONC."""
    lines: list[str] = []

    def value_line(head: str, value: str, continuation_level: int) -> None:
        # 255 wide characters including the LF terminator; never truncate evidence.
        while True:
            room = 253 - len(head)
            if room < 1:
                raise ValueError("Cabecera GEDCOM demasiado larga")
            lines.append(f"{head} {value[:room]}" if value else head)
            value = value[room:]
            if not value:
                return
            head = f"{continuation_level} CONC"

    def emit(node: Node, level: int) -> None:
        head = str(level)
        if node.xref:
            head += f" {node.xref}"
        head += f" {node.tag}"
        if node.value:
            pointer = node.is_pointer if node.is_pointer is not None else (
                node.tag in {"SOUR", "REPO", "NOTE", "FAMC", "FAMS", "HUSB", "WIFE", "CHIL", "SUBM", "OBJE", "ASSO", "ALIA"}
                and bool(re.fullmatch(r"@[^@\s]+@", node.value)))
            value = node.value
            if not pointer:
                # Preserve standard date/calendar escapes, escaping literal at-signs.
                pieces = re.split(r"(@#[^@\r\n]+@)", value) if node.tag == "DATE" else [value]
                value = "".join(part if i % 2 else part.replace("@", "@@") for i, part in enumerate(pieces))
            parts = value.split("\n")
            value_line(head, parts[0], level + 1)
            for cont in parts[1:]:
                value_line(f"{level + 1} CONT", cont, level + 1)
        else:
            lines.append(head)
        for child in node.children:
            emit(child, level + 1)

    for record in records:
        emit(record, 0)
    return "\n".join(lines) + "\n"


def to_dict(node: Node) -> dict:
    """Serialize a Node subtree to a JSON-safe dict (for the ``raw`` column)."""
    d: dict = {"tag": node.tag}
    if node.value is not None:
        d["value"] = node.value
    if node.xref:
        d["xref"] = node.xref
    if node.children:
        d["children"] = [to_dict(c) for c in node.children]
    if node.is_pointer is not None:
        d["is_pointer"] = node.is_pointer
    return d


def from_dict(d: dict) -> Node:
    return Node(
        tag=d["tag"],
        value=d.get("value"),
        xref=d.get("xref"),
        children=[from_dict(c) for c in d.get("children", [])],
        is_pointer=d.get("is_pointer"),
    )
