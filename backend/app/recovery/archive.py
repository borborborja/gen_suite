"""Disk-backed recovery format. Verification never connects to a database or S3."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile
from contextlib import contextmanager

FORMAT = "gen-suite-recovery/v2"
LEGACY_FORMAT = "gen-suite-recovery/v1"
FORMATS = {FORMAT, LEGACY_FORMAT}
BLOCK = 1024 * 1024
ROLES = ("private", "public")
HEX = re.compile(r"[0-9a-f]{64}\Z")


class RecoveryError(Exception):
    """Only sanitized, operator-facing messages cross the CLI boundary."""


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


@contextmanager
def private_file(path):
    with os.fdopen(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "wb") as stream:
        yield stream
        stream.flush()
        os.fsync(stream.fileno())


def regular(path):
    path = Path(path)
    if not stat.S_ISREG(path.lstat().st_mode):
        raise RecoveryError("La copia contiene un enlace o un archivo no regular.")
    return path


def digest(path):
    with regular(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def descriptor(path):
    return {"sha256": digest(path), "size": regular(path).stat().st_size}


def object_path(root, role, key):
    if role not in ROLES or not isinstance(key, str) or not key or len(key.encode("utf-8")) > 1024:
        raise RecoveryError("Identidad de objeto no válida.")
    # S3 keys are opaque, not paths. Even ../ or Unicode names never become local paths.
    return Path(root) / "objects" / role / hashlib.sha256(key.encode("utf-8")).hexdigest()


def lines(path):
    with regular(path).open("rb") as stream:
        while raw := stream.readline(BLOCK + 1):
            if len(raw) > BLOCK or not raw.endswith(b"\n"):
                raise RecoveryError("Manifiesto truncado o entrada demasiado grande.")
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise RecoveryError("Entrada de manifiesto no válida.")
            yield value


def check_file(path, expected):
    if (not isinstance(expected, dict) or set(expected) != {"sha256", "size"}
            or not HEX.fullmatch(str(expected["sha256"]))
            or type(expected["size"]) is not int or expected["size"] < 0
            or descriptor(path) != expected):
        raise RecoveryError("La copia está incompleta o no coincide con sus checksums.")


def load_manifest(root):
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise RecoveryError("Selecciona un directorio de copia, no un enlace.")
    path = regular(root / "manifest.json")
    if path.stat().st_size > 8 * BLOCK:
        raise RecoveryError("Manifiesto demasiado grande.")
    manifest = json.loads(path.read_bytes())
    validate_manifest(root, manifest)
    return manifest


def validate_manifest(root, manifest):
    if len(canonical(manifest)) > 8 * BLOCK:
        raise RecoveryError("Manifiesto demasiado grande para este formato de recuperación.")
    if manifest.get("format") not in FORMATS or manifest.get("complete") is not True:
        raise RecoveryError("Formato desconocido o copia no finalizada.")
    if manifest["format"] == FORMAT:
        from .database import ROW_HASH_ALGORITHM
        database = manifest.get("database", {})
        integrity = database.get("row_integrity", {})
        tables, counts = integrity.get("tables"), database.get("counts")
        if (integrity.get("algorithm") != ROW_HASH_ALGORITHM or not isinstance(tables, dict)
                or not isinstance(counts, dict) or set(tables) != set(counts)
                or not isinstance(database.get("triggers"), list)):
            raise RecoveryError("La copia v2 requiere integridad de todas las tablas y catálogo de triggers.")
        for table, entry in tables.items():
            if (not isinstance(entry, dict) or set(entry) != {"rows", "sha256"}
                    or type(entry["rows"]) is not int or entry["rows"] < 0 or entry["rows"] != counts[table]
                    or not HEX.fullmatch(str(entry["sha256"]))):
                raise RecoveryError("Las huellas de contenido de PostgreSQL no son válidas.")
    if set(manifest.get("files", {})) != {"database.dump", "objects.jsonl", "references.jsonl"}:
        raise RecoveryError("No se encuentran todos los componentes de recuperación.")
    for name, expected in manifest["files"].items():
        check_file(root / name, expected)


class Catalog:
    """An index on scratch disk, not one Python dictionary per scanned page."""
    def __enter__(self):
        self.temp = tempfile.TemporaryDirectory(prefix="gensuite-recovery-check-")
        self.db = sqlite3.connect(str(Path(self.temp.name) / "index.sqlite"))
        self.db.execute("CREATE TABLE objects (role TEXT, key TEXT, sha TEXT, size INTEGER, PRIMARY KEY(role,key))")
        return self

    def __exit__(self, *args):
        self.db.close()
        self.temp.cleanup()

    def add(self, row):
        if not HEX.fullmatch(str(row.get("sha256"))) or type(row.get("size")) is not int or row["size"] < 0:
            raise RecoveryError("Descriptor de objeto no válido.")
        try:
            self.db.execute("INSERT INTO objects VALUES (?,?,?,?)", (row["role"], row["key"], row["sha256"], row["size"]))
        except sqlite3.IntegrityError:
            raise RecoveryError("Objeto duplicado en el inventario.") from None

    def check_reference(self, row):
        found = self.db.execute("SELECT sha,size FROM objects WHERE role=? AND key=?", (row["role"], row["key"])).fetchone()
        if (not found or (row.get("sha256") and found[0] != row["sha256"])
                or (row.get("size") is not None and found[1] != row["size"])):
            raise RecoveryError("Falta un objeto citado por PostgreSQL o sus bytes no coinciden.")


def verify(root, *, manifest=None):
    root = Path(root)
    if manifest is None:
        manifest = load_manifest(root)
    else:
        validate_manifest(root, manifest)
    for directory in (root / "objects", *(root / "objects" / role for role in ROLES)):
        if directory.is_symlink() or not directory.is_dir():
            raise RecoveryError("Directorio de objetos ausente o no seguro.")
    totals = {role: {"count": 0, "bytes": 0} for role in ROLES}
    references = 0
    with Catalog() as catalog:
        for row in lines(root / "objects.jsonl"):
            path = object_path(root, row["role"], row["key"])
            check_file(path, {"sha256": row["sha256"], "size": row["size"]})
            catalog.add(row)
            totals[row["role"]]["count"] += 1
            totals[row["role"]]["bytes"] += row["size"]
        for row in lines(root / "references.jsonl"):
            object_path(root, row["role"], row["key"])
            catalog.check_reference(row)
            references += 1
    if totals != manifest.get("objects") or references != manifest.get("references"):
        raise RecoveryError("Los recuentos no coinciden con el manifiesto.")
    return manifest
