"""Offline, bounded-page export primitives. No network or model calls."""
from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import fitz


def file_checksum(path: str | Path) -> str:
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class ConsultationPDF:
    """Write one page at a time. Text is an honest appendix, never fabricated OCR geometry.

    This output is deliberately NOT labelled PDF/A: archival conformance requires a
    dedicated conversion/validation stage, not a metadata flag.
    """
    def __init__(self, path: str):
        self.writer = fitz.DocumentWriter(path)
        self.page_count = 0
        self.font = fitz.Font("cjk")

    def _write(self, page):
        device = self.writer.begin_page(page.rect)
        # PyMuPDF 1.28 exposes DeviceWrapper.this while DisplayList.run still expects
        # .device; older supported releases already provide .device.
        if not hasattr(device, "device") and hasattr(device, "this"):
            from types import SimpleNamespace
            device = SimpleNamespace(device=device.this)
        page.get_displaylist().run(device, fitz.Matrix(1, 1), page.rect)
        self.writer.end_page()
        self.page_count += 1

    def add_image(self, image: bytes):
        with fitz.open(stream=image) as source:
            with fitz.open(stream=source.convert_to_pdf(), filetype="pdf") as single:
                self._write(single[0])

    def add_transcription(self, page_no: int, text: str | None, *, reviewed: bool = False):
        label = "Revisión humana; no implica certeza absoluta" if reviewed else "Lectura de IA sin revisión humana"
        remaining = f"Transcripción — imagen {page_no}\n{label}\n\n{text if text is not None else '[Sin transcripción]'}"
        while remaining:
            with fitz.open() as single:
                page = single.new_page(width=595, height=842)
                writer = fitz.TextWriter(page.rect)
                overflow = writer.fill_textbox(fitz.Rect(40, 40, 555, 802), remaining,
                                               font=self.font, fontsize=11, warn=None)
                writer.write_text(page)
                self._write(page)
                next_text = "\n".join(line[0] for line in overflow)
                if next_text == remaining:
                    raise ValueError("No se ha podido paginar la transcripción")
                remaining = next_text

    def close(self):
        self.writer.close()


class PreservationBag:
    """A BagIt ZIP whose payload paths are generated IDs, not untrusted original names."""
    def __init__(self, path: str):
        self.archive = zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED, allowZip64=True)
        self.checksums: dict[str, str] = {}
        self.sizes: list[int] = []

    def add_file(self, path: str, entry: str):
        if not entry.startswith("data/") or ".." in entry.split("/") or "\\" in entry or "\n" in entry:
            raise ValueError("Unsafe payload path")
        if entry in self.checksums:
            raise ValueError("Duplicate payload path")
        self.checksums[entry] = file_checksum(path)
        self.sizes.append(Path(path).stat().st_size)
        self.archive.write(path, entry)

    def add_json(self, value, entry: str = "data/metadata.json"):
        data = json.dumps(value, ensure_ascii=False, default=str, indent=2).encode("utf-8")
        self._add_bytes(data, entry)

    def add_text(self, value: str, entry: str):
        self._add_bytes(value.encode("utf-8"), entry)

    def _add_bytes(self, data: bytes, entry: str):
        if entry in self.checksums or not entry.startswith("data/") or ".." in entry or "\\" in entry or "\n" in entry:
            raise ValueError("Unsafe or duplicate payload path")
        self.checksums[entry] = hashlib.sha256(data).hexdigest()
        self.sizes.append(len(data))
        self.archive.writestr(entry, data)

    def close(self):
        tags = {
            "bagit.txt": "BagIt-Version: 1.0\nTag-File-Character-Encoding: UTF-8\n",
            "bag-info.txt": f"Source-Organization: Gen Suite\nPayload-Oxum: {sum(self.sizes)}.{len(self.sizes)}\n",
            "manifest-sha256.txt": "".join(f"{digest}  {name}\n" for name, digest in sorted(self.checksums.items())),
        }
        for name, content in tags.items():
            self.archive.writestr(name, content.encode("utf-8"))
        self.archive.writestr("tagmanifest-sha256.txt", "".join(
            f"{hashlib.sha256(content.encode('utf-8')).hexdigest()}  {name}\n" for name, content in sorted(tags.items())))
        self.archive.close()
