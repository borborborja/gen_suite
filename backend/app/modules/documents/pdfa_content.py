"""Isolated process: fingerprint page order, dimensions and searchable text.

This is not a visual-fidelity or historical-accuracy certificate. No source text
is printed. MuPDF never shares a thread with the worker's consultation writer.
"""
import hashlib
import json
import sys
import unicodedata

import pymupdf


def signature(path: str) -> dict:
    digest = hashlib.sha256()
    with pymupdf.open(path) as pdf:
        if pdf.needs_pass or pdf.page_count < 1:
            raise ValueError("PDF vacío o protegido")
        for page in pdf:
            content = [round(page.rect.width, 1), round(page.rect.height, 1),
                       " ".join(unicodedata.normalize("NFC", page.get_text()).split())]
            digest.update(json.dumps(content, ensure_ascii=False, separators=(",", ":")).encode())
            digest.update(b"\n")
        return {"pages": pdf.page_count, "sha256": digest.hexdigest(),
                "method": "page-order-dimensions-0.1pt-nfc-whitespace/v1"}


if __name__ == "__main__":
    print(json.dumps(signature(sys.argv[1])))
