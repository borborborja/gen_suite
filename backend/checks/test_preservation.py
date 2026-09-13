"""No database, Redis, external credentials or paid model calls."""
import hashlib
import io
import json
import tempfile
import unittest
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import fitz
from PIL import Image
from fastapi import HTTPException

from app.core.dispatch import assert_no_secrets, schedule
from app.models.job import Job
from app.models.transcription import Transcription
from app.modules.documents.exports import parse_range
from app.modules.documents.preservation import ConsultationPDF, PreservationBag
from app.modules.documents.service import _iter_rasterize_pdf
from app.modules.jobs.constants import TERMINAL_STATUSES, terminal_event
from app.modules.transcription import service
from app.tasks.extraction_tasks import _final_status, _parse_page


class PreservationTests(unittest.TestCase):
    def test_pdf_contains_image_and_searchable_unicode_appendix(self):
        original = io.BytesIO()
        Image.new("RGB", (160, 240), (130, 20, 40)).save(original, "JPEG")
        content = "Baptizavi Josep Puig, fill de Maria. Parròquia de Sant Martí."
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "book.pdf")
            writer = ConsultationPDF(path)
            writer.add_image(original.getvalue())
            writer.add_transcription(1, content)
            writer.close()
            with fitz.open(path) as pdf:
                self.assertEqual(len(pdf), 2)
                self.assertEqual(pdf[0].get_text().strip(), "")
                pixel = pdf[0].get_pixmap().pixel(20, 20)
                self.assertTrue(all(abs(a - b) <= 5 for a, b in zip(pixel, (130, 20, 40))))
                self.assertIn("Parròquia de Sant Martí", pdf[1].get_text())
                self.assertIn("sin revisión humana", pdf[1].get_text())

    def test_long_transcription_is_not_truncated(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "book.pdf")
            writer = ConsultationPDF(path)
            writer.add_transcription(8, "Baptizavi Maria. " * 1000 + "FINAL_VERIFICABLE", reviewed=True)
            writer.close()
            with fitz.open(path) as pdf:
                self.assertGreater(len(pdf), 2)
                self.assertIn("FINAL_VERIFICABLE", "".join(page.get_text() for page in pdf))

    def test_bagit_has_verifiable_original_bytes_and_metadata(self):
        original = b"original exacto, sin transformar"
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "scan.jpg"
            source.write_bytes(original)
            destination = str(Path(tmp) / "book.zip")
            bag = PreservationBag(destination)
            bag.add_file(str(source), "data/images/1.jpg")
            bag.add_json({"original_filename": "àvia.jpg", "text": "Martí"})
            bag.close()
            with zipfile.ZipFile(destination) as archive:
                self.assertEqual(archive.read("data/images/1.jpg"), original)
                self.assertEqual(json.loads(archive.read("data/metadata.json"))["text"], "Martí")
                for manifest in ("manifest-sha256.txt", "tagmanifest-sha256.txt"):
                    for line in archive.read(manifest).decode().splitlines():
                        digest, path = line.split("  ", 1)
                        self.assertEqual(hashlib.sha256(archive.read(path)).hexdigest(), digest)

    def test_bagit_rejects_unsafe_and_duplicate_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            bag = PreservationBag(str(Path(tmp) / "book.zip"))
            with self.assertRaises(ValueError):
                bag.add_json({}, "data/../../outside")
            bag.add_json({})
            with self.assertRaises(ValueError):
                bag.add_json({})
            bag.close()

    def test_raster_preserves_physical_page_mapping(self):
        with fitz.open() as pdf:
            pdf.new_page(width=400, height=200)
            pdf.new_page(width=200, height=400)
            data = pdf.tobytes()
        pages = list(_iter_rasterize_pdf(data, dpi=100, max_w=200, autosplit=True))
        self.assertEqual([p[2] for p in pages], [1, 1, 2])
        self.assertEqual(pages[0][3]["bbox"], [0, 0, .5, 1])
        self.assertEqual(pages[1][3]["bbox"], [.5, 0, .5, 1])
        self.assertEqual(pages[2][3]["bbox"], [0, 0, 1, 1])

    def test_ranges(self):
        self.assertEqual(parse_range("bytes=2-9", 6), (2, 5))
        self.assertEqual(parse_range("bytes=2-", 6), (2, 5))
        self.assertEqual(parse_range("bytes=-2", 6), (4, 5))
        self.assertIsNone(parse_range(None, 6))
        for header in ("bytes=6-", "bytes=-0", "bytes=3-1", "bytes=0-1,3-4", "garbage"):
            with self.subTest(header=header), self.assertRaises(HTTPException) as error:
                parse_range(header, 6)
            self.assertEqual(error.exception.status_code, 416)

    def test_partial_is_never_clean_success(self):
        self.assertEqual(_final_status(1, 20), "partial")
        self.assertEqual(_final_status(20, 20), "error")
        self.assertEqual(_final_status(0, 20), "completed")
        self.assertIn("partial", TERMINAL_STATUSES)
        self.assertEqual(terminal_event("partial", {}, None)["kind"], "book_fail")

    def test_invalid_act_is_not_silently_discarded(self):
        with self.assertRaises(Exception):
            _parse_page({"has_record": True, "records": [{"record_type": 123, "mentions": "invalid"}]})
        for raw in ({}, {"records": []}, {"has_record": True, "records": []}):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                _parse_page(raw)
        self.assertEqual(_parse_page({"has_record": False, "records": []}).records, [])

    def test_jpeg_vision_input_is_not_recompressed(self):
        from app.tasks.transcription_tasks import _to_jpeg
        original = io.BytesIO()
        Image.new("RGB", (100, 100), "white").save(original, "JPEG", quality=98)
        self.assertEqual(_to_jpeg(original.getvalue()), original.getvalue())

    def test_queued_payload_rejects_nested_credentials(self):
        assert_no_secrets({"override": {"credential_id": str(uuid.uuid4())}})
        with self.assertRaises(HTTPException):
            assert_no_secrets({"override": {"api_key": "not-a-real-key"}})


class RevisionTests(unittest.IsolatedAsyncioTestCase):
    def session(self):
        session = MagicMock()
        for name in ("get", "scalar", "execute", "flush", "refresh", "commit", "delete"):
            setattr(session, name, AsyncMock())
        return session

    def transcription(self, **values):
        return Transcription(id=uuid.uuid4(), tenant_id=uuid.uuid4(), document_id=uuid.uuid4(),
            page_no=1, engine="vision", text="lectura original", status="ok", visibility="private",
            is_active=True, **values)

    async def test_correction_creates_revision_preserves_text_and_no_implicit_spend(self):
        old = self.transcription()
        session = self.session()
        session.get.return_value = old
        revision = await service.correct_text(session, old.id, "corrección humana", tenant_id=old.tenant_id, created_by=uuid.uuid4())
        self.assertEqual(old.text, "lectura original")
        self.assertFalse(old.is_active)
        self.assertEqual(revision.text, "corrección humana")
        self.assertEqual(revision.supersedes_id, old.id)
        self.assertIsNone(revision.confidence)
        self.assertEqual(revision.status, "corrected")
        session.commit.assert_not_awaited()
        session.delete.assert_not_awaited()
        self.assertEqual(len(session.add.call_args_list), 1)  # no hidden paid embedding job

    async def test_stale_revision_cannot_be_overwritten(self):
        old = self.transcription()
        old.is_active = False
        session = self.session()
        session.get.return_value = old
        with self.assertRaises(HTTPException) as error:
            await service.correct_text(session, old.id, "otra lectura", tenant_id=old.tenant_id)
        self.assertEqual(error.exception.status_code, 409)
        session.add.assert_not_called()

    async def test_upload_reader_never_requests_unbounded_bytes(self):
        from app.modules.documents.router import _read_capped
        upload = MagicMock(filename="page.jpg", content_type="image/jpeg", size=None)
        upload.read = AsyncMock(return_value=b"x" * 11)
        with patch("app.modules.documents.router._MAX_IMAGE_BYTES", 10):
            with self.assertRaises(HTTPException) as error:
                await _read_capped([upload])
        self.assertEqual(error.exception.status_code, 413)
        upload.read.assert_awaited_once_with(11)

    async def test_cannot_correct_other_tenant(self):
        old = self.transcription()
        session = self.session()
        session.get.return_value = old
        with self.assertRaises(HTTPException) as error:
            await service.correct_text(session, old.id, "otra lectura", tenant_id=uuid.uuid4())
        self.assertEqual(error.exception.status_code, 404)

    async def test_reconcile_preserves_all_evidence_even_legacy_delete_request(self):
        old = self.transcription()
        candidate = self.transcription()
        candidate.is_active = False
        session = self.session()
        session.scalar.return_value = MagicMock(visibility="private")
        with patch.object(service, "list_versions", AsyncMock(return_value=[(1, old, candidate)])):
            count = await service.reconcile_versions(session, old.tenant_id, old.document_id,
                                                     mode="substitute", keep_history=False)
        self.assertEqual(count, 1)
        self.assertEqual(old.text, "lectura original")
        self.assertEqual(candidate.text, "lectura original")
        session.delete.assert_not_awaited()
        session.commit.assert_not_awaited()

    async def test_dispatch_is_in_caller_transaction_and_contains_only_ids(self):
        session = self.session()
        job = Job(id=uuid.uuid4(), tenant_id=uuid.uuid4(), type="transcription", params={})
        await schedule(session, job, "transcribe_document", document_id=str(uuid.uuid4()))
        dispatch = session.add.call_args.args[0]
        self.assertEqual(dispatch.job_id, job.id)
        self.assertEqual(dispatch.payload["tenant_id"], str(job.tenant_id))
        session.commit.assert_not_awaited()

    async def test_reconcile_rejects_an_unseen_candidate(self):
        old, candidate = self.transcription(), self.transcription()
        session = self.session()
        session.scalar.return_value = MagicMock(visibility="private")
        with patch.object(service, "list_versions", AsyncMock(return_value=[(1, old, candidate)])):
            with self.assertRaises(HTTPException) as error:
                await service.reconcile_versions(session, old.tenant_id, old.document_id, mode="substitute",
                    expected_versions={"1": {"active_id": str(old.id), "candidate_id": str(uuid.uuid4())}})
        self.assertEqual(error.exception.status_code, 409)
        session.add.assert_not_called()

    async def test_irreversible_deletion_is_blocked_before_storage_access(self):
        from app.modules.documents import service as documents
        session = self.session()
        doc = MagicMock(tenant_id=uuid.uuid4())
        with patch.object(documents, "get_document", AsyncMock(return_value=doc)), \
             patch.object(documents.storage, "delete_prefix", AsyncMock()) as remove:
            for operation in (documents.delete_document, documents.discard_images):
                with self.assertRaises(HTTPException) as error:
                    await operation(session, doc.tenant_id, uuid.uuid4())
                self.assertEqual(error.exception.status_code, 409)
            remove.assert_not_awaited()
            session.delete.assert_not_awaited()


class DispatchTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, status, *, unavailable=False):
        from app.models.task_dispatch import TaskDispatch
        from app.tasks import dispatch_tasks
        job = Job(id=uuid.uuid4(), tenant_id=uuid.uuid4(), status=status)
        intent = TaskDispatch(job_id=job.id, tenant_id=job.tenant_id, function="transcribe_document",
            payload={"job_id": str(job.id), "tenant_id": str(job.tenant_id)}, status="pending", attempts=0)
        session = MagicMock()
        session.scalar = AsyncMock(side_effect=[intent, None])
        session.get = AsyncMock(return_value=job)
        session.commit = AsyncMock()
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=session)
        context.__aexit__ = AsyncMock(return_value=False)
        engine = MagicMock(dispose=AsyncMock())
        queue = MagicMock(enqueue_job=AsyncMock(return_value=None))
        get_queue = AsyncMock(side_effect=ConnectionError() if unavailable else None, return_value=queue)
        with patch.object(dispatch_tasks, "create_async_engine", return_value=engine), \
             patch.object(dispatch_tasks, "AsyncSession", return_value=context), \
             patch.object(dispatch_tasks, "get_queue", get_queue):
            await dispatch_tasks.dispatch_pending_tasks({})
        return intent, job, queue, session

    async def test_redis_failure_keeps_intent_and_job_for_retry(self):
        intent, job, queue, session = await self.exercise("queued", unavailable=True)
        self.assertEqual(intent.status, "pending")
        self.assertEqual(job.status, "queued")
        self.assertEqual(intent.attempts, 1)
        self.assertGreater(intent.available_at, datetime.now(timezone.utc))
        session.commit.assert_awaited_once()

    async def test_cancelled_job_is_not_dispatched(self):
        intent, job, queue, _ = await self.exercise("cancelled")
        self.assertEqual(intent.status, "cancelled")
        queue.enqueue_job.assert_not_awaited()

    async def test_redis_deduplication_uses_stable_job_identifier(self):
        intent, job, queue, _ = await self.exercise("queued")
        self.assertEqual(intent.status, "dispatched")
        self.assertEqual(queue.enqueue_job.call_args.kwargs["_job_id"], f"durable:{job.id}")


class WorkerLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_terminal_transition_is_conditional(self):
        from app.core.job_lifecycle import update_active_job
        from sqlalchemy.dialects import postgresql
        session = MagicMock(execute=AsyncMock(return_value=MagicMock(rowcount=0)))
        self.assertFalse(await update_active_job(session, uuid.uuid4(), status="completed"))
        statement = session.execute.call_args.args[0].compile(dialect=postgresql.dialect())
        self.assertIn("jobs.status IN", str(statement))
        self.assertIn(["queued", "running"], list(statement.params.values()))

    async def test_cancelled_embeddings_do_not_resolve_or_call_provider(self):
        from app.tasks import embedding_tasks as task
        session = MagicMock()
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=session)
        context.__aexit__ = AsyncMock(return_value=False)
        with patch.object(task, "SessionLocal", return_value=context), \
             patch.object(task, "set_rls_context", AsyncMock()), \
             patch.object(task, "_start", AsyncMock(return_value=False)), \
             patch.object(task, "ProviderService") as provider:
            for function in (task.embed_document, task.embed_mentions):
                await function.__wrapped__({}, job_id=uuid.uuid4(), tenant_id=uuid.uuid4(), document_id=uuid.uuid4())
            provider.assert_not_called()

    async def test_embedding_completion_cannot_announce_success_after_cancel(self):
        from app.tasks import embedding_tasks as task
        with patch.object(task, "update_active_job", AsyncMock(return_value=False)), \
             patch.object(task, "commit_keep_rls", AsyncMock()), \
             patch.object(task, "_publish", AsyncMock()) as publish:
            await task._finish(MagicMock(), uuid.uuid4(), uuid.uuid4(), result={"embedded": 16})
        self.assertEqual(publish.call_args.args[2]["kind"], "cancelled")

    async def test_missing_batch_pages_are_failures_and_empty_pages_checkpointed(self):
        from app.tasks import extraction_tasks as task
        from app.modules.providers import service as providers
        tenant_id, job_id, document_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        first, second = uuid.uuid4(), uuid.uuid4()
        targets = [(tid, uuid.uuid4(), i, "Baptizavi", "private") for i, tid in enumerate((first, second), 1)]
        session = MagicMock(scalar=AsyncMock(return_value=True), execute=AsyncMock(),
                            get=AsyncMock(return_value=MagicMock(params={"batch_id": "existing-batch"})))
        rc = MagicMock(model="test-model")
        for results, expected, checkpoint_count in (({str(first): {"has_record": False, "records": []}}, "partial", 1), ({}, "error", 0)):
            with self.subTest(status=expected), \
                 patch.object(task, "set_rls_context", AsyncMock()), \
                 patch.object(task, "commit_keep_rls", AsyncMock()), \
                 patch.object(task, "job_is_active", AsyncMock(return_value=True)), \
                 patch.object(task, "update_active_job", AsyncMock(return_value=True)) as transition, \
                 patch.object(task, "_checkpoint", AsyncMock()) as checkpoint, \
                 patch.object(task, "_normalize_folios", AsyncMock(return_value=0)), \
                 patch.object(task, "_stitch_spans", AsyncMock(return_value=0)), \
                 patch.object(task, "_validate_sequence", AsyncMock(return_value=0)), \
                 patch.object(providers, "poll_batch", return_value=("completed", "output")), \
                 patch.object(providers, "fetch_batch_results", return_value=(results, {"prompt": 10, "completion": 0})), \
                 patch.object(providers, "record_usage", AsyncMock()):
                await task._run_batch_extraction(session, job_id=job_id, tenant_id=tenant_id, document_id=document_id,
                    rc=rc, targets=targets, doc_context="", schema={}, pub=AsyncMock())
                self.assertEqual(transition.call_args.kwargs["status"], expected)
                self.assertEqual(transition.call_args.kwargs["result"]["pages"], 2)
                self.assertEqual(checkpoint.await_count, checkpoint_count)


if __name__ == "__main__":
    unittest.main()
