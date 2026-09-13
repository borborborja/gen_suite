"""Offline generation contracts. Real PostgreSQL isolation/activation tests are separate."""
import inspect
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from app.models.embedding_index import EmbeddingGeneration
from app.models.job import Job
from app.models.task_dispatch import TaskDispatch
from app.modules.search import indexes, index_router, service
from app.modules.linkage import service as linkage
from app.core.execution_context import current_job
from app.tasks import embedding_tasks


def generation(**changes):
    return EmbeddingGeneration(**{**dict(id=uuid.uuid4(), tenant_id=uuid.uuid4(), title="Synthetic", purpose="full",
        status="ready", is_active=False, include_public=False, version=1, profile={"engine": "test", "model": "fixed"},
        space_key="a" * 64, job_id=None, research_run_id=None), **changes})


class IdentityTests(unittest.TestCase):
    def test_space_identity_separates_model_endpoint_recipe_but_not_tariff_or_key(self):
        original = {"engine": "openai", "model": "synthetic", "base_url": "https://example.invalid", "credential_id": "first"}
        key = indexes.space_key(original)
        self.assertEqual(key, indexes.space_key({**original, "credential_id": "rotated", "budget_policy": {"price": "changed"}}))
        for field in ("model", "base_url", "engine"):
            self.assertNotEqual(key, indexes.space_key({**original, field: "different"}))
        with patch.object(indexes, "RECIPE", "another-recipe"):
            self.assertNotEqual(key, indexes.space_key(original))

    def test_vectors_reject_invalid_dimension_nonfinite_boolean_zero_and_partial(self):
        indexes.validate_vectors([[1.] + [0.] * 1023], 1)
        for vectors in ([], [[0.] * 1024], [[1.] * 1023], [[True] * 1024], [[float("nan")] * 1024], [[float("inf")] * 1024], [[1e300] * 1024], [[1e-300] * 1024]):
            with self.subTest(vectors=len(vectors)), self.assertRaises(ValueError): indexes.validate_vectors(vectors, 1)

    def test_generation_worker_never_updates_original_vector_columns_or_deletes(self):
        source = inspect.getsource(embedding_tasks)
        for operation in ("update(Transcription)", "update(PersonMention)", "delete(", "embedding=None"):
            self.assertNotIn(operation, source)


class ManifestTests(unittest.IsolatedAsyncioTestCase):
    async def test_source_page_is_bounded_checkpointed_and_not_committed_inline(self):
        g = generation()
        job = Job(id=uuid.uuid4(), tenant_id=g.tenant_id, params={})
        row = dict(source_id=uuid.uuid4(), source_tenant_id=g.tenant_id, document_id=uuid.uuid4(), page_id=uuid.uuid4(),
            transcription_id=uuid.uuid4(), transcription_end_id=None, record_id=None, input_text="Baptizavi Maria", input_fields={})
        result = MagicMock(); result.mappings.return_value.all.return_value = [row]
        session = MagicMock(execute=AsyncMock(return_value=result))
        await indexes.capture_manifest(session, g, job, "transcription")
        sql, params = session.execute.call_args_list[0].args
        self.assertIn("t.is_active", str(sql)); self.assertIn("LIMIT :limit", str(sql)); self.assertEqual(params["limit"], 128)
        self.assertFalse(params["public"])
        insert = session.execute.call_args_list[1].args[0]
        self.assertIn("ON CONFLICT ON CONSTRAINT uq_embedding_item_source DO NOTHING", str(insert))
        self.assertTrue(job.params["index_manifest_transcription"]["done"])
        self.assertFalse(await indexes.capture_manifest(session, g, job, "transcription"))
        self.assertEqual(session.execute.await_count, 2)
        session.commit.assert_not_called()

    async def test_current_sources_recheck_both_revisions_and_input_fields(self):
        session = MagicMock(scalars=AsyncMock(return_value=MagicMock(all=lambda: [])))
        await indexes.current_item_ids(session, uuid.uuid4(), "mention", [uuid.uuid4()])
        sql = str(session.scalars.call_args.args[0])
        for guard in ("tr.is_active", "te.is_active", "e.input_fields", "r.status IN", "m.tenant_id=e.source_tenant_id", "e.id = ANY(:ids)"):
            self.assertIn(guard, sql)


class QueryTests(unittest.IsolatedAsyncioTestCase):
    async def test_query_resolves_index_profile_not_current_binding(self):
        g = generation(is_active=True)
        provider = MagicMock(resolve=AsyncMock(), resolve_frozen=AsyncMock(return_value="fixed-credential"))
        token = current_job.set(None)
        try:
            with patch.object(indexes, "active_generation", AsyncMock(return_value=g)), patch.object(indexes, "ProviderService", return_value=provider):
                found, rc = await indexes.query_context(MagicMock(), g.tenant_id)
            self.assertEqual(found, g); self.assertEqual(rc, "fixed-credential")
            provider.resolve.assert_not_awaited()
            self.assertEqual(provider.resolve_frozen.call_args.kwargs["profile"], g.profile)
        finally: current_job.reset(token)

    async def test_unattributed_vector_search_rejected_before_any_sql(self):
        session = MagicMock(execute=AsyncMock())
        with self.assertRaises(HTTPException):
            await service.vector_search(session, [1.] * 1024, "tenant", uuid.uuid4(), 10)
        session.execute.assert_not_awaited()

    async def test_semantic_result_keeps_generation_vector_and_paid_query_provenance(self):
        g = generation(is_active=True)
        row = SimpleNamespace(id=uuid.uuid4(), document_id=uuid.uuid4(), page_no=1, page_id=uuid.uuid4(), title="Source",
            index_generation_id=g.id, embedding_item_id=uuid.uuid4(), embedding_ai_call_id=uuid.uuid4(), source_hash="b" * 64,
            sim=1.0, snippet="Maria")
        session = MagicMock(execute=AsyncMock(return_value=MagicMock(all=lambda: [row])))
        hits = await service.vector_search(session, [1.] * 1024, "all", g.tenant_id, 10, generation_id=g.id, query_call_id="query-fixture")
        self.assertEqual(hits[0]["index_generation_id"], g.id)
        self.assertEqual(hits[0]["embedding_ai_call_id"], row.embedding_ai_call_id)
        self.assertEqual(hits[0]["query_ai_call_id"], "query-fixture")
        sql, params = session.execute.call_args.args
        self.assertIn("e.generation_id=:gid", str(sql)); self.assertIn("t.text=e.input_text", str(sql))
        self.assertNotIn("t.embedding", str(sql)); self.assertEqual(params["gid"], g.id)

    async def test_discovery_preserves_trace_and_does_not_hide_managed_index_failure(self):
        g = generation()
        row = SimpleNamespace(id=uuid.uuid4(), item_id=uuid.uuid4(), ai_call_id=uuid.uuid4(), source_hash="b" * 64)
        session = MagicMock(scalar=AsyncMock(return_value=True), execute=AsyncMock(return_value=MagicMock(all=lambda: [row])))
        seed = SimpleNamespace(given="Maria", surname="Puig", place_key="test")
        with patch.object(indexes, "query_context", AsyncMock(return_value=(g, object()))), \
             patch.object(linkage, "embed_texts_with_usage", AsyncMock(return_value=([[1.] * 1024], {"ai_call_id": "query"}))):
            hits = await linkage._vector_retrieve(session, g.tenant_id, seed, 10)
        self.assertEqual(hits[row.id]["embedding_ai_call_id"], str(row.ai_call_id))
        self.assertEqual(hits[row.id]["query_ai_call_id"], "query")
        with patch.object(indexes, "query_context", AsyncMock(side_effect=HTTPException(409, "mismatch"))), \
             patch.object(linkage, "_in_research_run", AsyncMock(return_value=True)):
            with self.assertRaises(HTTPException): await linkage._vector_retrieve(session, g.tenant_id, seed, 10)


class ActivationTests(unittest.IsolatedAsyncioTestCase):
    async def test_atomic_switch_preserves_old_vectors_and_checks_active_identity(self):
        target = generation(); old = generation(tenant_id=target.tenant_id, is_active=True)
        principal = SimpleNamespace(tenant_id=target.tenant_id, user_id=uuid.uuid4())
        session = MagicMock(scalar=AsyncMock(side_effect=[True, target]), flush=AsyncMock())
        body = index_router.GenerationAction(expected_version=1, expected_active_id=old.id, reason="Fixture comparison")
        with patch.object(indexes, "active_generation", AsyncMock(return_value=old)), \
             patch.object(indexes, "coverage", AsyncMock(return_value={"counts": {"ready": 2}, "searchable": {"transcription": 2}})), \
             patch.object(indexes, "generation_out", AsyncMock(return_value={})):
            await index_router.control(target.id, "activate", body, principal, session)
        self.assertFalse(old.is_active); self.assertTrue(target.is_active)
        session.delete.assert_not_called(); session.execute.assert_not_called(); session.commit.assert_not_called()
        self.assertEqual(session.flush.await_count, 2)

    async def test_racing_activation_or_stale_version_never_replaces_index(self):
        for stale_version in (False, True):
            target = generation(); old = generation(tenant_id=target.tenant_id, is_active=True)
            session = MagicMock(scalar=AsyncMock(side_effect=[True, target]))
            body = index_router.GenerationAction(expected_version=2 if stale_version else 1, expected_active_id=uuid.uuid4(), reason="Fixture")
            with patch.object(indexes, "active_generation", AsyncMock(return_value=old)), self.assertRaises(HTTPException):
                await index_router.control(target.id, "activate", body, SimpleNamespace(tenant_id=target.tenant_id), session)
            self.assertTrue(old.is_active); self.assertFalse(target.is_active)
            session.add.assert_not_called(); session.delete.assert_not_called()

    async def test_pending_entries_allow_only_previously_active_rollback_with_live_coverage(self):
        for previously_active, searchable, permitted in ((False, 2, False), (True, 2, True), (True, 0, False)):
            with self.subTest(previously_active=previously_active, searchable=searchable):
                target = generation(); old = generation(tenant_id=target.tenant_id, is_active=True)
                principal = SimpleNamespace(tenant_id=target.tenant_id, user_id=uuid.uuid4())
                session = MagicMock(scalar=AsyncMock(side_effect=[True, target, uuid.uuid4() if previously_active else None]), flush=AsyncMock())
                body = index_router.GenerationAction(expected_version=1, expected_active_id=old.id, reason="Reviewed rollback")
                with patch.object(indexes, "active_generation", AsyncMock(return_value=old)), \
                     patch.object(indexes, "coverage", AsyncMock(return_value={"counts": {"ready": 2, "pending": 1}, "searchable": {"transcription": searchable}})), \
                     patch.object(indexes, "generation_out", AsyncMock(return_value={})):
                    if permitted:
                        await index_router.control(target.id, "activate", body, principal, session)
                    else:
                        with self.assertRaises(HTTPException) as error:
                            await index_router.control(target.id, "activate", body, principal, session)
                        self.assertEqual(error.exception.status_code, 409)
                self.assertEqual(target.is_active, permitted)
                self.assertEqual(old.is_active, not permitted)
                self.assertEqual(target.version, 2 if permitted else 1)
                session.delete.assert_not_called(); session.commit.assert_not_called()

    async def test_explicit_resume_reuses_job_and_retained_paid_responses(self):
        target = generation(status="building", job_id=uuid.uuid4())
        job = Job(id=target.job_id, tenant_id=target.tenant_id, status="paused")
        intent = TaskDispatch(job_id=job.id, status="dispatched", generation=0)
        session = MagicMock(scalar=AsyncMock(side_effect=[True, target, True]), get=AsyncMock(side_effect=[job, intent, job]), flush=AsyncMock())
        with patch.object(index_router, "totals", AsyncMock(return_value={"unknown_calls": 0})), patch.object(indexes, "generation_out", AsyncMock(return_value={})):
            await index_router.control(target.id, "resume", index_router.GenerationAction(expected_version=1, reason="Resume fixture"),
                SimpleNamespace(tenant_id=target.tenant_id, user_id=uuid.uuid4()), session)
        self.assertEqual(job.id, target.job_id); self.assertEqual(intent.generation, 1)
        self.assertEqual(job.status, "queued"); session.delete.assert_not_called()
