"""Local PDF/A-2b conversion and independent, fail-closed validation.

Only generated consultation PDFs enter here. Original objects are never inputs
to a repair tool. No OCR, inferred word positions, provider calls or shell.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import signal
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

from ...settings import settings
from .preservation import file_checksum

REPORT_LIMIT = 512 * 1024
OCR_VERSION = "17.11.0"
VERA_VERSION = "1.30.2"


class PdfaError(ValueError):
    """Messages are static and safe to expose to the owning researcher."""


class ExportCancelled(Exception):
    pass


async def _run(argv: list[str], directory: Path, *, checkpoint=None,
               timeout: int = 30, limit: int = REPORT_LIMIT, version_output: bool = False) -> bytes:
    # Do not pass DB/S3/provider credentials, JVM options or arbitrary plugins
    # from the worker environment to native document parsers.
    environment = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
                   "LANG": "C.UTF-8", "HOME": str(directory), "TMPDIR": str(directory)}
    proc = await asyncio.create_subprocess_exec(*argv, cwd=directory, env=environment,
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT if version_output else asyncio.subprocess.DEVNULL,
        start_new_session=True)

    async def output():
        data = bytearray()
        while chunk := await proc.stdout.read(65536):
            data.extend(chunk)
            if len(data) > limit:
                raise PdfaError("El informe PDF/A supera el límite permitido; no se publica el resultado.")
        await proc.wait()
        return bytes(data)

    reader = asyncio.create_task(output())
    try:
        async with asyncio.timeout(timeout):
            while not reader.done():
                if checkpoint is not None and not await checkpoint():
                    raise ExportCancelled()
                await asyncio.wait({reader}, timeout=2)
            result = await reader
        if proc.returncode != 0:
            raise PdfaError("La conversión o validación PDF/A ha fallado; no se publica el resultado. Los originales se conservan.")
        return result
    except TimeoutError:
        raise PdfaError("La exportación PDF/A ha superado el tiempo límite; los originales se conservan.") from None
    finally:
        # Includes task cancellation and errors while reading. Kill the whole
        # process group (Ghostscript/Java children too), before deleting temp files.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)
        await proc.wait()


async def check_tools(directory: Path) -> dict[str, str]:
    ocr = shutil.which("ocrmypdf")
    vera = shutil.which("verapdf")
    if not ocr or not vera or not shutil.which("gs") or not shutil.which("tesseract"):
        raise PdfaError("Faltan las herramientas del perfil opcional pdfa de Docker Compose.")
    # OCRmyPDF's console writes --version to stderr; document processing logs
    # remain discarded and must never be mixed into the validator's XML.
    ocr_version = (await _run([ocr, "--version"], directory, limit=4096, version_output=True)).decode().strip()
    vera_version = (await _run([vera, "--version"], directory, limit=4096)).decode().strip()
    gs_version = (await _run([shutil.which("gs"), "--version"], directory, limit=4096)).decode().strip()
    if ocr_version != OCR_VERSION or f"veraPDF {VERA_VERSION}" not in vera_version:
        raise PdfaError("Las versiones PDF/A no coinciden con las verificadas; reconstruye el perfil pdfa.")
    return {"ocrmypdf": ocr_version, "verapdf": VERA_VERSION, "ghostscript": gs_version}


def validate_report(raw: bytes, *, output: Path) -> dict:
    """A zero CLI exit code alone is not proof of PDF/A conformance."""
    def invalid():
        return PdfaError("veraPDF no confirma PDF/A-2b para este archivo; no se publica el resultado.")

    if not raw or len(raw) > REPORT_LIMIT or b"\x00" in raw or any(
        token in raw.upper() for token in (b"<!DOCTYPE", b"<!ENTITY")
    ):
        raise invalid()
    try:
        root = ET.fromstring(raw)
        jobs = root.findall("./jobs/job")
        reports = root.findall(".//validationReport")
        summary = root.find("./batchSummary")
        builds = root.findall("./buildInformation/releaseDetails")
        if (root.tag != "report" or len(jobs) != 1 or len(reports) != 1 or summary is None
            or len(root.findall("./batchSummary")) != 1):
            raise invalid()
        report = reports[0]
        item = jobs[0].find("item")
        counts = summary.find("validationReports")
        details = report.find("details")
        if (jobs[0].find("validationReport") is not report or item is None
            or item.findtext("name") != str(output)
            or int(item.attrib["size"]) != output.stat().st_size
            or report.attrib.get("profileName") != "PDF/A-2b validation profile"
            or report.attrib.get("isCompliant") != "true"
            or report.attrib.get("jobEndStatus") != "normal"
            or details is None or details.attrib.get("failedRules") != "0"
            or details.attrib.get("failedChecks") != "0"
            or int(details.attrib["passedRules"]) < 1
            or int(details.attrib["passedChecks"]) < 1
            or summary.attrib.get("totalJobs") != "1"
            or any(summary.attrib.get(key) != "0" for key in
                   ("failedToParse", "encrypted", "outOfMemory", "veraExceptions"))
            or counts is None or counts.attrib != {"compliant": "1", "nonCompliant": "0", "failedJobs": "0"}
            or (counts.text or "").strip() != "1"
            or not any(b.attrib.get("id") == "core" and b.attrib.get("version") == VERA_VERSION for b in builds)):
            raise invalid()
        return {"profile": "PDF/A-2b", "validator_version": VERA_VERSION,
                "report_sha256": hashlib.sha256(raw).hexdigest(),
                "report_xml": raw.decode("utf-8")}
    except (ET.ParseError, KeyError, ValueError, UnicodeError, OSError) as exc:
        if isinstance(exc, PdfaError):
            raise
        raise invalid() from None


async def convert(source: Path, *, checkpoint=None) -> tuple[Path, dict]:
    directory = source.parent
    if source.stat().st_size > settings.pdfa_max_bytes:
        raise PdfaError("El PDF supera el límite del perfil PDF/A; divide el libro o ajusta su capacidad.")
    versions = await check_tools(directory)
    async def content_signature(path):
        raw = await _run([sys.executable, "-m", "app.modules.documents.pdfa_content", str(path)],
            directory, checkpoint=checkpoint, timeout=settings.pdfa_timeout_seconds, limit=4096)
        return json.loads(raw)
    content_before = await content_signature(source)
    before = await asyncio.to_thread(file_checksum, source)
    output = directory / "validated.pdf"
    started = time.monotonic()
    await _run([shutil.which("ocrmypdf"), "--ocr-engine", "none", "--mode", "skip",
        "--output-type", "pdfa-2", "--optimize", "0", "--pdfa-image-compression", "lossless",
        "--jobs", "1", str(source), str(output)], directory,
        checkpoint=checkpoint, timeout=settings.pdfa_timeout_seconds)
    if not output.is_file() or not 0 < output.stat().st_size <= settings.pdfa_max_bytes:
        raise PdfaError("El resultado PDF/A está vacío o supera el límite permitido.")
    digest = await asyncio.to_thread(file_checksum, output)
    raw = await _run([shutil.which("verapdf"), "--format", "xml", "--flavour", "2b",
        "--maxfailuresdisplayed", "1", str(output)], directory,
        checkpoint=checkpoint, timeout=settings.pdfa_timeout_seconds)
    proof = validate_report(raw, output=output)
    if content_before != await content_signature(output):
        raise PdfaError("La conversión PDF/A ha cambiado las páginas o el texto cercable; no se publica el resultado.")
    if (digest != await asyncio.to_thread(file_checksum, output)
        or before != await asyncio.to_thread(file_checksum, source)):
        raise PdfaError("El archivo cambió durante la validación PDF/A; no se publica el resultado.")
    proof.update({"schema": "gen-suite-pdfa/v1", "status": "validated", "tools": versions,
        "input_sha256": before, "output_sha256": digest, "ocr_performed": False,
        "content_check": content_before,
        "text_mode": "appendix", "validated_at": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": round(time.monotonic() - started, 3)})
    return output, proof
