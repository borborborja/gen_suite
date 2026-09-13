"""Opt-in real-directory scale probe, with generated originals only (never a live collection).

python backend/checks/benchmark_ingestion.py --files 100000
This proves scan/manifest scale, not PostgreSQL/S3 end-to-end ingestion throughput.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import time

from PIL import Image
from app.ingest_import import plan_collection, scan_collection
from app.modules.documents.ingestion import ManifestBatch


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--files", type=int, default=100_000)
    args = parser.parse_args()
    if not 1 <= args.files <= 100_000:
        parser.error("--files debe estar entre 1 y 100000")
    original = io.BytesIO()
    Image.new("RGB", (32, 48), "white").save(original, "JPEG", quality=95)
    with tempfile.TemporaryDirectory(prefix="gensuite-ingest-scale-") as directory:
        root = Path(directory)
        seed = root / "seed.bin"
        seed.write_bytes(original.getvalue())
        start = time.perf_counter()
        for index in range(args.files):
            folder = root / f"Libro-{index // 1000:03d}"
            folder.mkdir(exist_ok=True)
            folder_seed = folder / "seed.bin"
            if index % 1000 == 0:
                folder_seed.write_bytes(original.getvalue())
            # At most 1000 links per seed (filesystems commonly cap link counts near 65000).
            os.link(folder_seed, folder / f"Página-{index % 1000 + 1}.jpg")
        create_seconds = time.perf_counter() - start
        start = time.perf_counter()
        scans = scan_collection(root)
        entries = list(plan_collection(scans, "Colección"))
        for offset in range(0, len(entries), 500):
            ManifestBatch(files=entries[offset:offset + 500])
        assert len(scans) == len(entries) == args.files
        assert len({entry["path"] for entry in entries}) == args.files
        assert hashlib.sha256(seed.read_bytes()).digest() == hashlib.sha256(original.getvalue()).digest()
        print(json.dumps({"scope": "synthetic_directory_and_manifest_only", "files": len(entries),
                          "books": len({entry["group"] for entry in entries}),
                          "source_bytes": sum(entry["byte_size"] for entry in entries),
                          "create_seconds": round(create_seconds, 3),
                          "scan_validate_seconds": round(time.perf_counter() - start, 3),
                          "database_or_storage_integration": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
