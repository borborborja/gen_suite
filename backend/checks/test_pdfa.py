"""Offline PDF/A contracts. No database, S3, paid calls or system installs."""
import asyncio
import hashlib
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from app.core.dispatch import PDFA_HEALTH_KEY, PDFA_QUEUE, pdfa_available, queue_options
from app.modules.documents import exports, pdfa


def report(output: Path) -> bytes:
    return f'''<report>
      <buildInformation><releaseDetails id="core" version="1.30.2"/></buildInformation>
      <jobs><job><item size="{output.stat().st_size}"><name>{output}</name></item>
      <validationReport profileName="PDF/A-2b validation profile" isCompliant="true" jobEndStatus="normal">
        <details passedRules="100" failedRules="0" passedChecks="300" failedChecks="0"/>
      </validationReport></job></jobs>
      <batchSummary totalJobs="1" failedToParse="0" encrypted="0" outOfMemory="0" veraExceptions="0">
        <validationReports compliant="1" nonCompliant="0" failedJobs="0">1</validationReports>
      </batchSummary></report>'''.encode()


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="gensuite-pdfa-check-")
        self.addCleanup(self.tmp.cleanup)
        self.output = Path(self.tmp.name) / "validated.pdf"
        self.output.write_bytes(b"synthetic PDF stand-in")

    def test_explicit_profile_single_file_summary_and_checksum(self):
        raw = report(self.output)
        proof = pdfa.validate_report(raw, output=self.output)
        self.assertEqual(proof["profile"], "PDF/A-2b")
        self.assertEqual(proof["report_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(proof["report_xml"].encode(), raw)

    def test_fail_closed_on_every_inconsistent_claim(self):
        raw = report(self.output)
        for old, new in [(b'isCompliant="true"', b'isCompliant="false"'),
                         (b'2b validation', b'1b validation'),
                         (b'passedRules="100"', b'passedRules="0"'),
                         (b'failedChecks="0"', b'failedChecks="1"'),
                         (b'failedRules="0"', b'failedRules="1"'),
                         (b'veraExceptions="0"', b'veraExceptions="1"'),
                         (b'outOfMemory="0"', b'outOfMemory="1"'),
                         (b'failedToParse="0"', b'failedToParse="1"'),
                         (b'encrypted="0"', b'encrypted="1"'),
                         (b'totalJobs="1"', b'totalJobs="2"'),
                         (b'nonCompliant="0"', b'nonCompliant="1"'),
                         (b'failedJobs="0"', b'failedJobs="1"'),
                         (b'jobEndStatus="normal"', b'jobEndStatus="aborted"'),
                         (b'1.30.2', b'unknown'), (b'validated.pdf', b'other.pdf'),
                         (b'size="22"', b'size="100"'),
                         (b'</jobs>', b'<job/></jobs>'),
                         (b'</job>', b'<validationReport isCompliant="true"/></job>')]:
            with self.subTest(old=old), self.assertRaises(pdfa.PdfaError):
                self.assertIn(old, raw)
                pdfa.validate_report(raw.replace(old, new), output=self.output)

    def test_entities_malformed_empty_and_oversize_reports_rejected(self):
        for raw in [b"", b"not XML", b"<report/>", b"\x00" + report(self.output),
                    b"<!DOCTYPE report [<!ENTITY x 'boom'>]>" + report(self.output),
                    b"x" * (pdfa.REPORT_LIMIT + 1)]:
            with self.subTest(size=len(raw)), self.assertRaises(pdfa.PdfaError):
                pdfa.validate_report(raw, output=self.output)

    def test_routing_is_fixed_and_optional_worker_has_no_admin_cron(self):
        from app.tasks.worker import WorkerSettings, PdfaWorkerSettings
        self.assertEqual(queue_options("export_document_pdfa"), {"_queue_name": PDFA_QUEUE})
        self.assertEqual(queue_options("compact_to_pdf"), {})
        self.assertEqual(PdfaWorkerSettings.queue_name, PDFA_QUEUE)
        self.assertEqual(PdfaWorkerSettings.health_check_key, PDFA_HEALTH_KEY)
        self.assertEqual(PdfaWorkerSettings.max_jobs, 1)
        self.assertFalse(hasattr(PdfaWorkerSettings, "cron_jobs"))
        self.assertIn(PdfaWorkerSettings.functions[0], WorkerSettings.functions)


class PdfaAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_availability_requires_fresh_expiring_heartbeat(self):
        queue = AsyncMock()
        with patch("app.core.queue.get_queue", AsyncMock(return_value=queue)):
            for ttl, available in [(-2, False), (-1, False), (0, False), (11001, False), (9000, True)]:
                queue.pttl.return_value = ttl
                self.assertEqual(await pdfa_available(), available)
            queue.pttl.side_effect = ConnectionError("secret must not leak")
            self.assertFalse(await pdfa_available())

    async def test_run_limits_output_failure_timeout_and_cancellation(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            with self.assertRaises(pdfa.PdfaError):
                await pdfa._run([sys.executable, "-c", "print('x' * 10000)"], directory, limit=10)
            with self.assertRaises(pdfa.PdfaError):
                await pdfa._run([sys.executable, "-c", "raise SystemExit(1)"], directory)
            with self.assertRaises(pdfa.PdfaError):
                await pdfa._run([sys.executable, "-c", "import time; time.sleep(30)"], directory, timeout=.05)
            checkpoint = AsyncMock(return_value=False)
            with self.assertRaises(pdfa.ExportCancelled):
                await pdfa._run([sys.executable, "-c", "import time; time.sleep(30)"], directory, checkpoint=checkpoint)
            task = asyncio.create_task(pdfa._run([sys.executable, "-c", "import time; time.sleep(30)"], directory))
            await asyncio.sleep(.05)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

    async def test_native_tools_do_not_receive_worker_secrets(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"MINIO_SECRET_KEY": "synthetic-secret", "JAVA_TOOL_OPTIONS": "synthetic-option"}):
            out = await pdfa._run([sys.executable, "-c", "import os; print(sorted(os.environ))"], Path(tmp))
            self.assertNotIn(b"MINIO", out)
            self.assertNotIn(b"JAVA_TOOL_OPTIONS", out)

    async def test_conversion_uses_no_ocr_and_validates_exact_output(self):
        calls = []
        async def run(argv, directory, **kwargs):
            if argv[0] == sys.executable:
                return b'{"pages":1,"sha256":"synthetic"}'
            calls.append(argv)
            if argv[0] == "ocrmypdf":
                Path(argv[-1]).write_bytes(b"converted synthetic bytes")
                return b""
            return report(Path(argv[-1]))
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "input.pdf"; source.write_bytes(b"original synthetic bytes")
            with patch.object(pdfa, "check_tools", AsyncMock(return_value={"ocrmypdf": pdfa.OCR_VERSION})), \
                 patch.object(pdfa.shutil, "which", side_effect=lambda command: command), patch.object(pdfa, "_run", side_effect=run):
                output, proof = await pdfa.convert(source)
            self.assertEqual(source.read_bytes(), b"original synthetic bytes")
            self.assertEqual(proof["output_sha256"], hashlib.sha256(output.read_bytes()).hexdigest())
            self.assertFalse(proof["ocr_performed"])
            self.assertEqual(calls[0][1:5], ["--ocr-engine", "none", "--mode", "skip"])
            self.assertIn("lossless", calls[0]); self.assertIn("pdfa-2", calls[0])
            self.assertEqual(calls[1][-1], str(output))

    async def test_modified_output_during_validation_is_rejected(self):
        async def run(argv, directory, **kwargs):
            if argv[0] == sys.executable:
                return b'{"pages":1,"sha256":"synthetic"}'
            target = Path(argv[-1])
            if argv[0] == "ocrmypdf":
                target.write_bytes(b"first")
                return b""
            target.write_bytes(b"other")
            return report(target)
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "input.pdf"; source.write_bytes(b"input")
            with patch.object(pdfa, "check_tools", AsyncMock(return_value={})), \
                 patch.object(pdfa.shutil, "which", side_effect=lambda command: command), \
                 patch.object(pdfa, "_run", side_effect=run), self.assertRaises(pdfa.PdfaError):
                await pdfa.convert(source)

    async def test_searchable_text_loss_is_rejected_even_when_validator_passes(self):
        async def run(argv, directory, **kwargs):
            target = Path(argv[-1])
            if argv[0] == sys.executable:
                return b'{"sha256":"before"}' if target.name == "input.pdf" else b'{"sha256":"after"}'
            if argv[0] == "ocrmypdf":
                target.write_bytes(b"synthetic converted")
                return b""
            return report(target)
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "input.pdf"; source.write_bytes(b"input")
            with patch.object(pdfa, "check_tools", AsyncMock(return_value={})), \
                 patch.object(pdfa.shutil, "which", side_effect=lambda command: command), \
                 patch.object(pdfa, "_run", side_effect=run), self.assertRaisesRegex(pdfa.PdfaError, "texto cercable"):
                await pdfa.convert(source)

    async def test_optional_tools_must_match_pinned_versions(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(pdfa.shutil, "which", return_value="fake"):
            with patch.object(pdfa, "_run", AsyncMock(side_effect=[b"17.11.0", b"veraPDF 1.30.2", b"10.05.1"])):
                self.assertEqual((await pdfa.check_tools(Path(tmp)))["verapdf"], "1.30.2")
            with patch.object(pdfa, "_run", AsyncMock(side_effect=[b"16.0", b"veraPDF 1.30.2", b"10.05.1"])), self.assertRaises(pdfa.PdfaError):
                await pdfa.check_tools(Path(tmp))

    async def test_api_disabled_does_not_create_job_or_dispatch(self):
        db = MagicMock(); principal = SimpleNamespace(tenant_id=uuid.uuid4(), user_id=uuid.uuid4())
        with patch.object(exports, "_owned", AsyncMock()), patch.object(exports, "active_job_for", AsyncMock(return_value=None)), \
             patch.object(exports, "pdfa_available", AsyncMock(return_value=False)), patch.object(exports, "schedule", AsyncMock()) as schedule:
            with self.assertRaises(HTTPException) as error:
                await exports.create_export(uuid.uuid4(), exports.ExportRequest(kind="pdfa"), principal, db)
            self.assertEqual(error.exception.status_code, 503)
            db.add.assert_not_called(); schedule.assert_not_awaited()

    async def test_api_existing_job_survives_temporarily_unavailable_worker(self):
        job = SimpleNamespace(id=uuid.uuid4(), status="queued")
        with patch.object(exports, "_owned", AsyncMock()), patch.object(exports, "active_job_for", AsyncMock(return_value=job)), \
             patch.object(exports, "pdfa_available", AsyncMock(return_value=False)) as available:
            result = await exports.create_export(uuid.uuid4(), exports.ExportRequest(kind="pdfa"), SimpleNamespace(tenant_id=uuid.uuid4()), MagicMock())
            self.assertEqual(result["id"], str(job.id)); available.assert_not_awaited()
