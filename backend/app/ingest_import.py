"""Import folders via the same authenticated/resumable API used by the browser.

No database/S3 access, no external dependencies, no writes to the source directory.
Run `python -m app.ingest_import --help`; credentials are accepted only via environment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".pdf"}


def natural_path_key(value):
    folded = re.sub("[A-Z]", lambda match: match.group().lower(), value)
    tokens = tuple((1, int(part)) if index % 2 else (0, part.encode("utf-16-be"))
                   for index, part in enumerate(re.split("([0-9]+)", folded)))
    return tokens, value.encode("utf-16-be")


def scan_collection(root: Path, *, max_files=100_000):
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("El origen debe ser una carpeta")
    found = []
    def fail(error):
        raise error
    for directory, directories, filenames in os.walk(root, followlinks=False, onerror=fail):
        for name in [*directories, *filenames]:
            if (Path(directory) / name).is_symlink():
                raise ValueError("La colección contiene enlaces simbólicos; selecciona originales regulares y explícitos")
        for name in filenames:
            path = Path(directory) / name
            if path.suffix.lower() not in EXTENSIONS:
                continue
            info = path.stat(follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or info.st_size <= 0:
                raise ValueError(f"Original no regular o vacío: {path.relative_to(root)}")
            found.append({"path": path.relative_to(root).as_posix(), "size": info.st_size,
                          "last_modified": info.st_mtime_ns // 1_000_000})
            if len(found) > max_files:
                raise ValueError(f"La colección supera {max_files} archivos")
    if not found:
        raise ValueError("No hay originales PDF/JPG/PNG/TIFF/WebP en la carpeta")
    return sorted(found, key=lambda entry: natural_path_key(entry["path"]))


def plan_collection(scans, root_name, defaults=None, books=None):
    """Include the selected root's name, matching webkitRelativePath in a browser resume."""
    saved = {book["group"]: book for book in (books or [])}
    positions = {}
    for scan in scans:
        relative = f"{root_name}/{scan['path']}"
        pdf = relative.lower().endswith(".pdf")
        folder = relative.rsplit("/", 1)[0]
        group = f"pdf:{relative}" if pdf else f"images:{folder}"
        positions[group] = positions.get(group, 0) + 1
        previous = saved.get(group, {})
        title = Path(relative).stem if pdf else folder
        yield {"path": relative, "group": group, "title": previous.get("title", title[:512]),
               "kind": "pdf" if pdf else "image_set", "position": positions[group],
               "byte_size": scan["size"], "last_modified": scan["last_modified"],
               "options": previous.get("options", defaults or {})}


def open_original(root: Path, relative: str):
    """Open each path component without following symlinks, including a swapped directory."""
    parts = relative.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError("Ruta de original no válida")
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            nested = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = nested
        descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            raise ValueError("El original dejó de ser un archivo regular")
        return os.fdopen(descriptor, "rb")
    finally:
        os.close(directory)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("No se siguen redirecciones al transmitir credenciales")


class Client:
    def __init__(self, base_url, token, *, allow_http=False):
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("URL de API no válida")
        if parsed.scheme == "http" and parsed.hostname not in ("127.0.0.1", "localhost", "::1") and not allow_http:
            raise ValueError("Utiliza HTTPS; --allow-http solo para una red local de confianza")
        if not token or any(c in token for c in "\r\n"):
            raise ValueError("Define GEN_SUITE_IMPORT_TOKEN con una clave API del espacio")
        self.base = base_url.rstrip("/") + "/documents/ingestion"
        self.token = token
        self.opener = urllib.request.build_opener(NoRedirect())

    def request(self, path="", *, method="GET", body=None, data=None, headers=None):
        request_headers = {"Authorization": "Bearer " + self.token, **(headers or {})}
        if body is not None:
            data = json.dumps(body).encode()
            request_headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base + path, data=data, method=method, headers=request_headers)
        for attempt in range(4):
            try:
                with self.opener.open(request, timeout=120) as response:
                    return json.load(response)
            except urllib.error.HTTPError as error:
                if error.code in (429, 502, 503, 504) and attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                try:
                    detail = json.load(error).get("detail", "")
                except Exception:
                    detail = "Respuesta no válida"
                raise ValueError(f"API {error.code}: {detail}") from None
            except urllib.error.URLError:
                if attempt == 3:
                    raise ValueError("No se pudo conectar a la API; reanuda con el mismo identificador de sesión") from None
                time.sleep(2 ** attempt)


def run_import(client, root, scans, *, session_id=None, title=None, options=None, root_label=None):
    if session_id:
        current = client.request(f"/{uuid.UUID(session_id)}")
    else:
        current = client.request(method="POST", body={"client_id": str(uuid.uuid4()), "title": title or root.name,
            "expected_files": len(scans), "reserved_bytes": sum(scan["size"] for scan in scans), "options": options or {}})
    session_id = current["id"]
    print(f"Sesión guardada: {session_id}", flush=True)
    print("Para reanudar, utiliza --session-id con este identificador y la misma carpeta.", flush=True)
    if current["expected_files"] != len(scans) or current["reserved_bytes"] != sum(scan["size"] for scan in scans):
        raise ValueError("La carpeta no coincide con los archivos/tamaño del manifiesto")
    books, after = [], ""
    while True:
        batch = client.request(f"/{session_id}/books?limit=1000{after}")
        books.extend(batch)
        if len(batch) < 1000:
            break
        after = "&after=" + batch[-1]["id"]
    entries = list(plan_collection(scans, root_label or root.name, current["options"], books))
    files = {}
    for start in range(0, len(entries), 500):
        batch = client.request(f"/{session_id}/manifest", method="POST", body={"files": entries[start:start + 500]})
        files.update({entry["path"]: entry for entry in batch})
    current = client.request(f"/{session_id}/seal", method="POST")
    chunk_size = current["chunk_bytes"]
    for number, (scan, entry) in enumerate(zip(scans, entries), 1):
        item = client.request(f"/{session_id}/files/{files[entry['path']]['id']}")
        with open_original(root, scan["path"]) as source:
            before = os.fstat(source.fileno())
            if before.st_size != item["byte_size"] or before.st_mtime_ns // 1_000_000 != item["last_modified"]:
                raise ValueError(f"El original cambió: {scan['path']}")
            offset = 0
            while block := source.read(chunk_size):
                checksum = hashlib.sha256(block).hexdigest()
                old = item["parts"].get(str(offset))
                if old:
                    if old["size"] != len(block) or old["sha256"] != checksum:
                        raise ValueError(f"El original local no coincide con los fragmentos guardados: {scan['path']}")
                else:
                    client.request(f"/{session_id}/files/{item['id']}/chunks/{offset}", method="PUT", data=block,
                                   headers={"Content-Type": "application/octet-stream", "X-Content-SHA256": checksum})
                offset += len(block)
            after_stat = os.fstat(source.fileno())
            if offset != item["byte_size"] or (before.st_mtime_ns, before.st_size) != (after_stat.st_mtime_ns, after_stat.st_size):
                raise ValueError(f"El original se modificó durante la lectura: {scan['path']}")
        print(f"Original {number}/{len(entries)} verificado y recibido", flush=True)
    result = client.request(f"/{session_id}/complete", method="POST")
    print(f"Verificación en segundo plano: sesión {session_id}, trabajo {result['job_id']}", flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description="Importar originales sin modificarlos, con envío fragmentado y reanudable")
    parser.add_argument("folder", type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080/api")
    parser.add_argument("--session-id")
    parser.add_argument("--title")
    parser.add_argument("--root-label", help="Nombre original de la carpeta, si Compose la monta con otro nombre")
    parser.add_argument("--origin", default="own_photo", choices=["own_photo", "public_archive", "licensed", "familysearch"])
    parser.add_argument("--municipality")
    parser.add_argument("--record-type")
    parser.add_argument("--may-contain-living", action="store_true")
    parser.add_argument("--allow-http", action="store_true")
    parser.add_argument("--upload", action="store_true", help="Enviar; sin esta opción solo se comprueba la carpeta")
    args = parser.parse_args()
    try:
        root = args.folder.resolve(strict=True)
        scans = scan_collection(root)
        print(f"Colección: {len(scans)} originales, {sum(item['size'] for item in scans)} bytes")
        if not args.upload:
            print("Comprobación local terminada; no se ha enviado ni modificado ningún archivo.")
            return
        client = Client(args.base_url, os.environ.get("GEN_SUITE_IMPORT_TOKEN", ""), allow_http=args.allow_http)
        run_import(client, root, scans, session_id=args.session_id, title=args.title, root_label=args.root_label,
            options={"source_origin": args.origin, "record_type": args.record_type, "municipality": args.municipality,
                     "may_contain_living": args.may_contain_living})
    except (ValueError, OSError) as error:
        print(f"Importación detenida: {error}", file=sys.stderr)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        print("Envío pausado. Los fragmentos confirmados se conservan en el servidor.", file=sys.stderr)
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
