"""Untrusted GEDZIP inspection. No extractall, external fetches, or input-derived paths."""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import stat
import tempfile
from urllib.parse import unquote, urlsplit
import zipfile

from PIL import Image
from pypdf import PdfReader

from ...settings import settings
from . import gedcom7_import
from .export_bundle import canonical, file_checksum
from .gedcom7_import import ImportError

CHUNK = 1024**2
_MIMES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/tiff": "tif", "application/pdf": "pdf"}


def safe_name(name):
    if (not isinstance(name, str) or not name or name.startswith("/") or "\\" in name or
        any(ord(c) < 32 or ord(c) == 127 for c in name) or
        any(part in {"", ".", ".."} for part in name.rstrip("/").split("/")) or
        ":" in name.split("/")[0] or len(name.encode("utf-8")) > 4096):
        raise ImportError("El paquete contiene una ruta no segura o ambigua.")
    return name


def local_file(value):
    if not value or any(c in value for c in "\r\n\x00"):
        raise ImportError("Una referencia FILE no contiene una ruta válida.")
    try:
        uri = urlsplit(value)
    except ValueError:
        raise ImportError("Una referencia FILE no contiene una URI válida.") from None
    if uri.scheme or uri.netloc:
        return None  # Preserved in original only, never fetched (including file://).
    if uri.query or uri.fragment or re.search(r"%(?![0-9A-Fa-f]{2})", uri.path):
        raise ImportError("Una ruta local FILE contiene una consulta, fragmento o escape no admitido.")
    try:
        name = unquote(uri.path, encoding="utf-8", errors="strict")
    except UnicodeError:
        raise ImportError("Una ruta FILE no codifica UTF-8 válido.") from None
    return safe_name(name)


def checked_archive(path):
    # CPython's footer reader processes EOCD/ZIP64 without reading the directory.
    # Bound directory memory BEFORE ZipFile materializes its ZipInfo collection.
    with open(path, "rb") as source:
        footer = zipfile._EndRecData(source)
    if not footer or footer[zipfile._ECD_ENTRIES_TOTAL] > settings.tree_import_max_files + 16 or footer[zipfile._ECD_SIZE] > 16 * CHUNK:
        raise ImportError("El directorio ZIP supera los límites o no es válido.")
    if footer[zipfile._ECD_DISK_NUMBER] or footer[zipfile._ECD_DISK_START] or footer[zipfile._ECD_ENTRIES_THIS_DISK] != footer[zipfile._ECD_ENTRIES_TOTAL]:
        raise ImportError("No se admiten archivos ZIP repartidos en varios discos.")
    archive = zipfile.ZipFile(path)
    try:
        names, expanded = set(), 0
        for item in archive.infolist():
            safe_name(item.orig_filename)
            if item.orig_filename != item.filename or item.filename in names:
                raise ImportError("El ZIP contiene nombres duplicados, nulos o ambiguos.")
            if not item.flag_bits & 0x800 and not item.filename.isascii():
                raise ImportError("GEDZIP requiere nombres de archivo UTF-8.")
            names.add(item.filename)
            mode = item.external_attr >> 16
            if stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR}:
                raise ImportError("No se admiten enlaces simbólicos ni archivos especiales dentro del ZIP.")
            if stat.S_IFMT(mode) == stat.S_IFDIR and not item.is_dir() or item.is_dir() and item.file_size:
                raise ImportError("El ZIP contiene un directorio ambiguo.")
            if item.flag_bits & 1 or item.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                raise ImportError("Usa un GEDZIP sin cifrado y con compresión Stored o Deflate.")
            expanded += item.file_size
            if expanded > settings.tree_import_max_expanded_bytes or item.file_size > max(item.compress_size, 1) * 1000:
                raise ImportError("El ZIP supera el límite de expansión o compresión admitido.")
        if "gedcom.ged" not in names or archive.getinfo("gedcom.ged").is_dir():
            raise ImportError("GEDZIP debe contener el archivo gedcom.ged en la raíz.")
        return archive
    except BaseException:
        archive.close()
        raise


def read_capped(source, maximum):
    data = bytearray()
    while part := source.read(min(CHUNK, maximum + 1 - len(data))):
        data.extend(part)
        if len(data) > maximum:
            raise ImportError("El archivo supera el límite de lectura.")
    return bytes(data)


def inspect_media(path, declared_mime):
    if declared_mime not in _MIMES:
        raise ImportError("Este perfil importa JPG, PNG, WebP, TIFF de una página y PDF; conserva el paquete original para otros medios.")
    try:
        if declared_mime == "application/pdf":
            with open(path, "rb") as source:
                pdf = PdfReader(source)
                if pdf.is_encrypted or not 0 < len(pdf.pages) <= settings.tree_import_max_files:
                    raise ImportError("PDF vacío, cifrado o con demasiadas páginas.")
                return {"page_count": len(pdf.pages)}
        with Image.open(path) as image:
            if image.width * image.height > 40_000_000:
                raise ImportError("Una imagen supera el límite de 40 millones de píxeles.")
            if Image.MIME.get(image.format) != declared_mime or getattr(image, "n_frames", 1) != 1:
                raise ImportError("El tipo declarado no coincide con la imagen o contiene varias páginas.")
            dimensions = {"width": image.width, "height": image.height}
            image.verify()
            return dimensions
    except ImportError:
        raise
    except Exception:
        raise ImportError("Un medio del paquete no es una imagen o PDF válido.") from None


def extract_checked(archive, entry, output):
    digest, size = hashlib.sha256(), 0
    with archive.open(entry["path"]) as source, open(output, "wb") as target:
        while part := source.read(CHUNK):
            size += len(part)
            if size > entry["size"]:
                raise ImportError("Un archivo ZIP supera su tamaño declarado.")
            digest.update(part)
            target.write(part)
    if size != entry["size"] or entry.get("sha256") not in {None, digest.hexdigest()}:
        raise ImportError("El contenido ZIP no coincide con el archivo revisado.")
    return digest.hexdigest()


def prepare(path: Path, kind: str):
    """CPU/disk-only inspection; the worker checks cancellation around this step."""
    archive = None
    try:
        if kind not in {"gedcom7", "gedzip"} or not 0 < path.stat().st_size <= settings.tree_import_max_bytes:
            raise ImportError("Tipo o tamaño de importación no admitido.")
        archive = checked_archive(path) if kind == "gedzip" else None
        with archive.open("gedcom.ged") if archive else open(path, "rb") as source:
            data = read_capped(source, settings.tree_export_max_graph_bytes)
        roots = gedcom7_import.parse(data, max_bytes=settings.tree_export_max_graph_bytes,
                                    max_nodes=settings.tree_export_max_rows)
        nodes, report = gedcom7_import.project(roots)
        # Shared notes can amplify a small input into a very large model. Count
        # each occurrence, not each Python object identity, before any DB write.
        projected_bytes, pending = 0, list(nodes)
        while pending:
            node = pending.pop()
            projected_bytes += 128 + len((node.value or "").encode("utf-8")) + len(node.tag.encode("utf-8")) + len(node.xref or "")
            if projected_bytes > settings.tree_export_max_graph_bytes:
                raise ImportError("La proyección del árbol supera el límite de memoria; divide la importación.")
            pending.extend(node.children)
        report.update({"input_sha256": file_checksum(path), "input_bytes": path.stat().st_size,
                       "kind": kind, "report_version": 1})
        books, used = [], {"gedcom.ged"}
        remote = missing = references = planned_bytes = alternates = 0
        cache = {}
        with tempfile.TemporaryDirectory(prefix="gensuite-import-check-") as tmp:
            part = Path(tmp) / "media.bin"
            for node in [node for node in nodes if node.tag == "OBJE"]:
                entries = []
                for file in node.all("FILE"):
                    # Alternative representations must exist in GEDZIP too, even
                    # though this profile keeps them only in the original package.
                    for alternate in file.all("TRAN"):
                        alternate_name = local_file(alternate.value)
                        if archive and alternate_name is not None and (alternate_name not in archive.NameToInfo or archive.getinfo(alternate_name).is_dir()):
                            raise ImportError("Falta una representación TRAN referenciada por el GEDZIP.")
                        alternates += 1
                    name = local_file(file.value)
                    if name is None:
                        remote += 1
                        continue
                    if archive is None:
                        missing += 1
                        continue
                    if name not in archive.NameToInfo or archive.getinfo(name).is_dir() or name == "gedcom.ged":
                        raise ImportError("Falta un archivo local referenciado por el GEDZIP.")
                    info = archive.getinfo(name)
                    references += 1
                    planned_bytes += info.file_size
                    if references > settings.tree_import_max_files or planned_bytes > settings.tree_import_max_expanded_bytes:
                        raise ImportError("Demasiados medios o bytes referenciados; los duplicados también cuentan.")
                    if info.file_size > 256 * CHUNK:
                        raise ImportError("Un medio supera 256 MiB, límite de análisis individual de este perfil.")
                    mime = file.value_of("FORM")
                    entry = {"path": name, "size": info.file_size, "mime": mime}
                    if (name, mime) not in cache:
                        entry["sha256"] = extract_checked(archive, entry, part)
                        entry.update(inspect_media(part, mime))
                        cache[name, mime] = dict(entry)
                    else:
                        entry.update(cache[name, mime])
                    entry["extension"] = _MIMES[mime]
                    entry["title"] = file.value_of("TITL") or name
                    entries.append(entry)
                    used.add(name)
                if entries:
                    # Image pages in the same OBJE stay one ordered book. Mixed media
                    # and separate PDFs become several documents for the same source.
                    groups = [entries] if all(e["mime"].startswith("image/") for e in entries) else [[entry] for entry in entries]
                    for group in groups:
                        books.append({"xref": node.xref, "kind": "pdf" if group[0]["mime"] == "application/pdf" else "image_set",
                                      "title": group[0]["title"][:512], "files": group})
        report["media"] = {"documents": len(books), "files": sum(len(b["files"]) for b in books),
            "remote_not_fetched": remote, "local_not_attached": missing,
            "alternate_representations_original_only": alternates,
            "unreferenced_archive_entries": sum(not i.is_dir() and i.filename not in used for i in archive.infolist()) if archive else 0,
            "books": books}
        if report["media"]["files"] > settings.tree_import_max_files:
            raise ImportError("Demasiadas referencias multimedia para esta importación.")
        if remote or missing:
            report["warnings"].append("No se recuperan archivos externos ni rutas locales fuera del paquete; esos enlaces solo se conservan en el original.")
        report["warnings"].append("Los PDF se rasterizarán en segundo plano después de importar; esto no ejecuta transcripción ni IA.")
        report["review_token"] = hashlib.sha256(canonical(report)).hexdigest()
        return nodes, data.decode("utf-8-sig"), report, books
    except (zipfile.BadZipFile, EOFError, RuntimeError, NotImplementedError):
        raise ImportError("El ZIP está dañado o usa una característica no admitida.") from None
    finally:
        if archive:
            archive.close()
