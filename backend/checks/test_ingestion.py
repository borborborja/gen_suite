"""Offline checks: real image parsing, streamed assembly and no external credentials/services."""
import hashlib
import io
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from PIL import Image
from pydantic import ValidationError

from app.ingest_import import Client, natural_path_key, open_original, plan_collection, scan_collection
from app.modules.documents import ingestion as api
from app.tasks import ingestion_tasks as task


def request(*blocks, length=None):
    async def stream():
        for block in blocks:
            yield block
    return SimpleNamespace(headers={} if length is None else {"content-length": str(length)}, stream=stream)


class ManifestTests(unittest.TestCase):
    def test_safe_relative_paths_preserve_unicode(self):
        self.assertEqual(api.safe_relative_path("Batejos/Martí/001.jpg"), "Batejos/Martí/001.jpg")
        for path in ("/etc/passwd", "../x.jpg", "x/../y", "x//y", "x\\y", "C:/x", "x\ny", "", "x/./y"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                api.safe_relative_path(path)

    def test_manifest_rejects_empty_oversized_and_multi_pdf_book(self):
        entry = dict(path="a.jpg", group="book", title="Libro", kind="image_set", position=1, byte_size=10, last_modified=1)
        for changes in ({"byte_size": 0}, {"byte_size": api.MAX_IMAGE_BYTES + 1}, {"position": 0}, {"kind": "pdf", "position": 2}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                api.ManifestEntry(**{**entry, **changes})
        self.assertEqual(api.ManifestEntry(**entry).options.source_origin, "own_photo")

    def test_metadata_rejects_impossible_ranges_and_unknown_fields(self):
        with self.assertRaises(ValidationError):
            api.BookOptions(year_from=1900, year_to=1800)
        with self.assertRaises(ValidationError):
            api.BookOptions(municipality_lat=100)
        with self.assertRaises(ValidationError):
            api.BookOptions(api_key="never-store-this")

    def test_order_and_folder_groups_are_stable(self):
        names = ["p10.jpg", "p2.jpg", "p02.jpg", "P1.jpg", "àvia3.jpg", "àvia12.jpg"]
        self.assertEqual(sorted(names, key=natural_path_key), ["P1.jpg", "p02.jpg", "p2.jpg", "p10.jpg", "àvia3.jpg", "àvia12.jpg"])
        scans = [{"path": path, "size": 10, "last_modified": 1} for path in ("A/1.jpg", "A/2.jpg", "B/1.jpg", "B/libro.pdf")]
        entries = list(plan_collection(scans, "Colección"))
        self.assertEqual([row["position"] for row in entries], [1, 2, 1, 1])
        self.assertEqual(entries[0]["group"], "images:Colección/A")
        self.assertEqual(entries[-1]["group"], "pdf:Colección/B/libro.pdf")

    def test_importer_never_follows_a_swapped_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "regular.jpg").write_bytes(b"image")
            (root / "linked.jpg").symlink_to(root / "regular.jpg")
            with self.assertRaises(ValueError):
                scan_collection(root)
            with self.assertRaises(OSError):
                open_original(root, "linked.jpg")
            with self.assertRaises(ValueError):
                open_original(root, "../outside")

    def test_importer_dry_scan_keeps_sources_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "12.jpg"
            original.write_bytes(b"original")
            before = original.stat()
            scans = scan_collection(root)
            self.assertEqual(len(scans), 1)
            self.assertEqual(scans[0]["size"], len(b"original"))
            self.assertEqual(original.stat().st_mtime_ns, before.st_mtime_ns)
            with open_original(root, "12.jpg") as stream:
                self.assertEqual(stream.read(), b"original")

    def test_importer_rejects_remote_plaintext_credentials_and_url_secrets(self):
        for url in ("http://remote.example/api", "https://user:password@example.org/api", "ftp://example.org/api", "https://example.org/api?token=x"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                Client(url, "not-real-token")

    def test_image_verification_keeps_bytes_and_rejects_multiframe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            single = root / "original.jpg"
            with Image.new("RGB", (13, 17), "blue") as image:
                image.save(single)
                image.save(root / "multiple.tif", save_all=True, append_images=[image])
            before = single.read_bytes()
            result = task.inspect_original(single, "image_set")
            self.assertEqual((result["width"], result["height"]), (13, 17))
            self.assertEqual(single.read_bytes(), before)
            with self.assertRaises(ValueError):
                task.inspect_original(root / "multiple.tif", "image_set")


class UploadTests(unittest.IsolatedAsyncioTestCase):
    async def test_chunk_reader_is_bounded_and_detects_truncation(self):
        self.assertEqual(await api.read_chunk(request(b"12", b"34"), 4), b"1234")
        for req in (request(b"12345"), request(b"123"), request(b"1234", length=5)):
            with self.subTest(), self.assertRaises(HTTPException):
                await api.read_chunk(req, 4)

    def setup_chunk(self, *, parts=None, received=0, state="uploading"):
        item = SimpleNamespace(id=uuid.uuid4(), book_id=uuid.uuid4(), relative_path="1.jpg", position=1,
            byte_size=4, last_modified=0, received_bytes=received, parts=parts or {}, status="uploading", checksum=None, error=None)
        collection = SimpleNamespace(status=state, received_bytes=received)
        db = MagicMock(scalar=AsyncMock(return_value=item), flush=AsyncMock())
        return item, collection, db

    async def test_upload_retry_never_duplicates_bytes_or_overwrites_parts(self):
        digest = hashlib.sha256(b"1234").hexdigest()
        item, collection, db = self.setup_chunk()
        with patch.object(api, "owned", AsyncMock(return_value=collection)), patch.object(api.storage, "put_object", AsyncMock()) as put:
            await api.put_chunk(db, uuid.uuid4(), uuid.uuid4(), item.id, 0, digest, request(b"1234"))
            self.assertEqual(item.received_bytes, 4)
            self.assertEqual(collection.received_bytes, 4)
            await api.put_chunk(db, uuid.uuid4(), uuid.uuid4(), item.id, 0, digest, request(b"1234"))
            self.assertEqual(collection.received_bytes, 4)
            put.assert_awaited_once()
            with self.assertRaises(HTTPException) as error:
                await api.put_chunk(db, uuid.uuid4(), uuid.uuid4(), item.id, 0, hashlib.sha256(b"xxxx").hexdigest(), request(b"xxxx"))
            self.assertEqual(error.exception.status_code, 409)
            put.assert_awaited_once()

    async def test_bad_checksum_cannot_reach_storage(self):
        item, collection, db = self.setup_chunk()
        with patch.object(api, "owned", AsyncMock(return_value=collection)), patch.object(api.storage, "put_object", AsyncMock()) as put:
            with self.assertRaises(HTTPException) as error:
                await api.put_chunk(db, uuid.uuid4(), uuid.uuid4(), item.id, 0, "0" * 64, request(b"1234"))
            self.assertEqual(error.exception.status_code, 422)
            put.assert_not_awaited()

    async def test_other_tenant_is_rejected_before_reading_upload_body(self):
        db = MagicMock(scalar=AsyncMock(return_value=None))
        with self.assertRaises(HTTPException) as error:
            await api.put_chunk(db, uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), 0, "0" * 64, None)
        self.assertEqual(error.exception.status_code, 404)

    async def test_collection_quota_is_reserved_under_tenant_lock(self):
        db = MagicMock(scalar=AsyncMock(side_effect=[uuid.uuid4(), api.settings.ingest_tenant_quota_bytes]), get=AsyncMock(return_value=None))
        with self.assertRaises(HTTPException) as error:
            await api.create_session(db, uuid.uuid4(), uuid.uuid4(), api.NewSession(client_id=uuid.uuid4(), title="Libro", expected_files=1, reserved_bytes=1))
        self.assertEqual(error.exception.status_code, 413)
        self.assertIn("FOR UPDATE", str(db.scalar.call_args_list[0].args[0]))
        db.add.assert_not_called()

    async def test_streaming_assembly_verifies_every_part_and_full_sha(self):
        blocks = [b"abc", b"def"]
        item = SimpleNamespace(byte_size=6, parts={str(i * 3): {"key": str(i), "size": 3, "sha256": hashlib.sha256(b).hexdigest()} for i, b in enumerate(blocks)})
        async def open_object(bucket, key):
            return {"Body": io.BytesIO(blocks[int(key)])}
        with tempfile.TemporaryDirectory() as directory, patch.object(task, "CHUNK_BYTES", 3), patch.object(task.storage, "open_object", open_object):
            path = Path(directory) / "assembled"
            progress = AsyncMock()
            digest = await task.assemble_file("test", item, path, progress)
            self.assertEqual(digest, hashlib.sha256(b"abcdef").hexdigest())
            self.assertEqual(path.read_bytes(), b"abcdef")
            self.assertEqual(progress.await_count, 2)
            item.parts["3"]["sha256"] = "0" * 64
            with self.assertRaises(ValueError):
                await task.assemble_file("test", item, path, progress)

    async def test_large_original_is_read_in_one_mib_blocks(self):
        class Body:
            remaining = api.CHUNK_BYTES
            largest_read = 0
            def read(self, size):
                self.largest_read = max(self.largest_read, size)
                count = min(size, self.remaining)
                self.remaining -= count
                return b"x" * count
            def close(self):
                pass
        bodies = []
        digest = hashlib.sha256()
        for _ in range(8): digest.update(b"x" * 1024 ** 2)
        item = SimpleNamespace(byte_size=8 * api.CHUNK_BYTES, parts={str(i * api.CHUNK_BYTES): {"key": str(i), "size": api.CHUNK_BYTES, "sha256": digest.hexdigest()} for i in range(8)})
        async def open_object(bucket, key):
            body = Body(); bodies.append(body)
            return {"Body": body}
        with tempfile.TemporaryDirectory() as directory, patch.object(task.storage, "open_object", open_object):
            path = Path(directory) / "large"
            await task.assemble_file("test", item, path, AsyncMock())
            self.assertEqual(path.stat().st_size, 64 * 1024 ** 2)
            self.assertTrue(all(body.largest_read <= 1024 ** 2 for body in bodies))

    async def test_cancelled_collection_stops_before_more_io(self):
        db = MagicMock(scalar=AsyncMock(return_value=uuid.uuid4()))
        with self.assertRaises(task.IngestStopped):
            await task._check_running(db, uuid.uuid4(), uuid.uuid4())


if __name__ == "__main__":
    unittest.main()
