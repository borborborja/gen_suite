"""Opt-in real converter smoke test, entirely synthetic and without DB/S3/IA.

Run inside the PDF/A image: python checks/smoke_pdfa.py
Missing tools or a non-compliant output FAIL, never skip or fake success.
"""
import asyncio
import tempfile
from pathlib import Path

import fitz
from PIL import Image

from app.modules.documents.pdfa import convert, validate_report, PdfaError
from app.modules.documents.preservation import ConsultationPDF, file_checksum


async def main():
    with tempfile.TemporaryDirectory(prefix="gensuite-pdfa-smoke-") as tmp:
        original = Path(tmp) / "original.jpg"
        Image.new("RGB", (160, 240), (130, 20, 40)).save(original, "JPEG")
        original_hash = file_checksum(original)
        source = Path(tmp) / "consultation.pdf"
        writer = ConsultationPDF(str(source))
        writer.add_image(original.read_bytes())
        writer.add_transcription(1, "Baptizavi Josep Puig, fill de Maria. Parròquia de Sant Martí.")
        writer.add_transcription(2, "Lectura corregida: Lluïsa. FINAL_VERIFICABLE", reviewed=True)
        writer.close()
        source_hash = file_checksum(source)
        output, proof = await convert(source)
        assert proof["status"] == "validated"
        assert proof["profile"] == "PDF/A-2b"
        assert proof["output_sha256"] == file_checksum(output)
        assert file_checksum(original) == original_hash
        assert file_checksum(source) == source_hash
        with fitz.open(output) as pdf:
            assert len(pdf) == 3
            assert pdf[0].get_text().strip() == ""  # no fabricated manuscript OCR
            pixel = pdf[0].get_pixmap().pixel(20, 20)
            assert all(abs(a - b) <= 5 for a, b in zip(pixel, (130, 20, 40)))
            text = "".join(page.get_text() for page in pdf)
            for expected in ("Parròquia de Sant Martí", "sin revisión humana", "Revisión humana", "FINAL_VERIFICABLE", "Lluïsa"):
                assert expected in text, f"Searchable text lost: {expected}"
        try:
            validate_report(proof["report_xml"].encode().replace(b'isCompliant="true"', b'isCompliant="false"'), output=output)
        except PdfaError:
            pass
        else:
            raise AssertionError("Invalid conformance accepted")
        print("PASS: real OCRmyPDF + veraPDF PDF/A-2b; 3 pages; Unicode appendix searchable; originals unchanged; negative report rejected.")


if __name__ == "__main__":
    asyncio.run(main())
