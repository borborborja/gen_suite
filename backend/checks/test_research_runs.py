"""Offline orchestration contracts, not a substitute for real PostgreSQL/ARQ tests."""
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from pydantic import ValidationError
from app.models.job import Job
from app.models.task_dispatch import TaskDispatch
from app.models.research_run import ResearchRun, ResearchRunStage
from app.modules.research import runs
from app.modules.providers import admission, service
from app.core.execution_context import current_job
from app.tasks import research_run_tasks as task
from app.modules.linkage.service import _in_research_run


def request(**kwargs):
    return runs.RunCreate(**{**dict(client_id=uuid.uuid4(), title="Rama familiar", document_ids=[uuid.uuid4()],
        budget_microusd=1000000), **kwargs})


def fixture(stage_status="pending", job_status=None):
    run = ResearchRun(id=uuid.uuid4(), tenant_id=uuid.uuid4(), title="Test", status="running", version=1,
        budget_microusd=1000000, profiles={}, config={"lang": "spa"}, created_by=uuid.uuid4())
    stage = ResearchRunStage(id=uuid.uuid4(), tenant_id=run.tenant_id, run_id=run.id, kind="transcribe", title="Book",
        document_id=uuid.uuid4(), position=0, status=stage_status, attempts=0,
        job_id=uuid.uuid4() if job_status else None)
    job = Job(id=stage.job_id, tenant_id=run.tenant_id, status=job_status, result={"done": 2}, error=None) if job_status else None
    return run, stage, job


class PlanTests(unittest.TestCase):
    def test_stages_cover_documents_before_discoveries_without_tree_writes(self):
        body = request()
        book = SimpleNamespace(id=uuid.uuid4(), title="Baptisms", doc_type="pdf", is_index=False)
        index = SimpleNamespace(id=uuid.uuid4(), title="Index", doc_type="image_set", is_index=True)
        person = uuid.uuid4()
        stages = runs.stage_plan([book, index], [person], body)
        self.assertEqual([s["kind"] for s in stages], ["rasterize", "transcribe", "extract", "embed_text", "embed_mentions", "pdf", "archive",
                                                   "transcribe", "index_register", "embed_text", "pdf", "archive", "embed_corpus", "discover"])
        self.assertEqual(stages[-1]["person_id"], person)
        self.assertNotIn("accept", " ".join(function for _, function in runs.STAGE_TASKS.values()))

    def test_large_plan_is_bounded_by_books_and_people_not_image_payloads(self):
        docs = [SimpleNamespace(id=uuid.uuid4(), title="Synthetic", doc_type="image_set", is_index=False) for _ in range(1000)]
        plan = runs.stage_plan(docs, [uuid.uuid4() for _ in range(1500)], request())
        self.assertEqual(len(plan), 7501)
        self.assertEqual(sum(item["kind"] == "discover" for item in plan), 1500)

    def test_invalid_budget_duplicate_selection_and_empty_work_are_rejected(self):
        duplicate = uuid.uuid4()
        for kwargs in ({"budget_microusd": -1}, {"budget_microusd": True}, {"document_ids": []},
                       {"document_ids": [duplicate, duplicate]}, {"person_ids": [duplicate]},
                       dict(transcribe=False, extract=False, index=False, pdf=False, archive=False, discover_all_tree=False)):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValidationError): request(**kwargs)


class CoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_stage_atomically_creates_child_and_outbox_with_run_identity(self):
        run, stage, _ = fixture()
        session = MagicMock(scalar=AsyncMock(side_effect=[run, stage, object(), True]))
        with patch.object(task, "schedule", AsyncMock()) as schedule:
            await task.tick_run(session, run.tenant_id, run.id)
            job = schedule.call_args.args[1]
            self.assertEqual(job.params["research_run_id"], str(run.id))
            self.assertEqual(job.id, uuid.uuid5(stage.id, "research-stage-job"))
            self.assertEqual(stage.job_id, job.id); self.assertEqual(stage.attempts, 1)
            self.assertEqual(schedule.call_args.args[2], "transcribe_document")
            self.assertFalse(schedule.call_args.kwargs["options"]["replace"])
            session.commit.assert_not_called()

    async def test_current_active_child_is_not_duplicated(self):
        run, stage, job = fixture("running", "running")
        session = MagicMock(scalar=AsyncMock(side_effect=[run, stage]), get=AsyncMock(return_value=job))
        with patch.object(task, "schedule", AsyncMock()) as schedule:
            await task.tick_run(session, run.tenant_id, run.id)
            schedule.assert_not_awaited(); self.assertEqual(run.version, 1)

    async def test_finished_child_checkpoints_results_and_partial_needs_attention(self):
        for state in ("completed", "partial", "error", "cancelled"):
            run, stage, job = fixture("running", state)
            session = MagicMock(scalar=AsyncMock(side_effect=[run, stage]), get=AsyncMock(return_value=job))
            with self.subTest(state=state):
                await task.tick_run(session, run.tenant_id, run.id)
                self.assertEqual(stage.result, {"job": {"done": 2}})
                self.assertEqual(stage.status, "completed" if state == "completed" else "needs_attention")
                self.assertEqual(run.status, "running" if state == "completed" else "needs_attention")

    async def test_resume_waits_for_real_execution_lock_and_reuses_job(self):
        for lock_free in (False, True):
            run, stage, job = fixture("paused", "paused")
            intent = TaskDispatch(job_id=job.id, status="dispatched", generation=2)
            session = MagicMock(scalar=AsyncMock(side_effect=[run, stage, object(), lock_free]),
                                get=AsyncMock(side_effect=[job, intent]))
            with self.subTest(lock_free=lock_free), patch.object(task, "schedule", AsyncMock()) as schedule:
                await task.tick_run(session, run.tenant_id, run.id)
                schedule.assert_not_awaited()
                self.assertEqual(job.status, "queued" if lock_free else "paused")
                self.assertEqual(intent.generation, 3 if lock_free else 2)
                self.assertEqual(stage.job_id, job.id)

    async def test_no_remaining_stage_means_processing_not_accepted_genealogy(self):
        run, _, _ = fixture()
        session = MagicMock(scalar=AsyncMock(side_effect=[run, None]))
        await task.tick_run(session, run.tenant_id, run.id)
        self.assertEqual(run.status, "completed")
        event = session.add.call_args.args[0]
        self.assertIn("revisión humana", event.detail["meaning"])


class ControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_snapshot_omits_irrelevant_url_and_secrets(self):
        rc = service.ResolvedCredential(engine="tesseract", model=None, api_key="unused-fixture",
            base_url="https://example.invalid/unused", params={})
        with patch.object(service.ProviderService, "resolve", AsyncMock(return_value=rc)):
            profiles = await runs.freeze_profiles(MagicMock(), uuid.uuid4(), {"transcription"})
        self.assertIsNone(profiles["transcription"]["base_url"])
        self.assertNotIn("api_key", profiles["transcription"])

    async def test_strict_discovery_is_scoped_to_research_not_ordinary_jobs(self):
        tid, jid = uuid.uuid4(), uuid.uuid4()
        token = current_job.set((tid, jid))
        try:
            session = MagicMock(scalar=AsyncMock(side_effect=[{}, {"research_run_id": str(uuid.uuid4())}]))
            self.assertFalse(await _in_research_run(session, tid))
            self.assertTrue(await _in_research_run(session, tid))
            self.assertFalse(await _in_research_run(session, uuid.uuid4()))
            self.assertEqual(session.scalar.await_count, 2)
        finally:
            current_job.reset(token)

    async def test_pause_marks_children_without_deleting_results(self):
        run, _, _ = fixture()
        session = MagicMock(execute=AsyncMock(), flush=AsyncMock())
        principal = SimpleNamespace(tenant_id=run.tenant_id, user_id=uuid.uuid4())
        with patch.object(runs, "get_run", AsyncMock(return_value=run)), patch.object(runs, "run_out", AsyncMock(return_value={})):
            await runs.control(run.id, "pause", runs.RunAction(expected_version=1), principal, session)
            self.assertEqual(run.status, "paused"); self.assertEqual(run.version, 2)
            self.assertEqual(session.execute.await_count, 2)
            for call in session.execute.call_args_list:
                self.assertTrue(str(call.args[0]).startswith("UPDATE "))
            session.delete.assert_not_called(); session.commit.assert_not_called()

    async def test_stale_control_and_unreconciled_resume_do_not_change_state(self):
        run, _, _ = fixture(); run.status = "paused"
        principal = SimpleNamespace(tenant_id=run.tenant_id, user_id=uuid.uuid4())
        session = MagicMock()
        with patch.object(runs, "get_run", AsyncMock(return_value=run)), \
             patch.object(runs, "totals", AsyncMock(return_value={"unknown_calls": 1})):
            for version in (0, 1):
                body = SimpleNamespace(expected_version=version, reason="")
                with self.assertRaises(HTTPException): await runs.control(run.id, "resume", body, principal, session)
                self.assertEqual(run.status, "paused")

    async def test_worker_resolves_frozen_run_profile_not_new_binding_or_override(self):
        tid, jid, rid, cid = (uuid.uuid4() for _ in range(4))
        policy = dict(model="frozen-model", context_tokens=10000, max_output_tokens=100,
            input_usd_per_million="1", output_usd_per_million="1",
            valid_until=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(), source="Synthetic fixture")
        profile = dict(engine="openai", model="frozen-model", base_url="https://example.invalid/v1", credential_id=str(cid), budget_policy=policy)
        job = SimpleNamespace(params={"research_run_id": str(rid)})
        run = SimpleNamespace(id=rid, profiles={"inference": profile})
        cred = SimpleNamespace(id=cid, is_active=True, scope="tenant", tenant_id=tid)
        session = MagicMock(scalar=AsyncMock(side_effect=[job, run]), get=AsyncMock(return_value=cred))
        token = current_job.set((tid, jid))
        try:
            with patch.object(service, "_decrypt", return_value="synthetic-key"):
                rc = await service.ProviderService(session).resolve(tenant_id=tid, task_type="inference", override={"engine": "different"})
                self.assertEqual(rc.model, "frozen-model"); self.assertEqual(rc.research_run_id, rid)
                self.assertEqual(rc.job_id, jid)
        finally: current_job.reset(token)

    async def test_research_budget_checked_before_monthly_admission(self):
        tid, jid, rid = (uuid.uuid4() for _ in range(3))
        policy = dict(model="synthetic", context_tokens=1000, max_output_tokens=0,
            input_usd_per_million="1", output_usd_per_million="0",
            valid_until=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(), source="Synthetic fixture")
        rc = service.ResolvedCredential(engine="openai", model="synthetic", base_url=None, api_key="synthetic", params={"budget_policy": policy},
            tenant_id=tid, job_id=jid, research_run_id=rid, task_type="embedding", credential_id=uuid.uuid4())
        session = MagicMock(scalar=AsyncMock(side_effect=[SimpleNamespace(monthly_budget_cents=10000), None, "running",
            SimpleNamespace(id=rid, status="running", budget_microusd=1000), None]))
        manager = MagicMock(__aenter__=AsyncMock(return_value=session), __aexit__=AsyncMock(return_value=False))
        with patch.object(admission, "SessionLocal", return_value=manager), patch.object(admission, "set_rls_context", AsyncMock()), \
             patch.object(runs, "run_balance", AsyncMock(return_value={"spent_microusd": 1, "reserved_microusd": 0})):
            with self.assertRaises(HTTPException) as error: await admission._reserve(rc, admission.snapshot(rc), "a" * 64, "b" * 64, 1000)
            self.assertEqual(error.exception.status_code, 402); session.add.assert_not_called()


if __name__ == "__main__": unittest.main()
