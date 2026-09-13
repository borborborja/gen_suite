"""No network/keys/DB: admission state machine, arithmetic and real SDK request contracts."""
import asyncio
import hashlib
import io
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from pydantic import ValidationError

from app.modules.providers import admission as a, service as s
from app.modules.providers.router import BudgetUpdate, ReconcileCall


def credential(**changes):
    policy = dict(model="test-exact-model", context_tokens=100000, max_output_tokens=1000,
        input_usd_per_million="0.123456", output_usd_per_million="1.234567",
        valid_until=(datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
        source="Synthetic test fixture, not a provider tariff")
    values = dict(engine="openai", model="test-exact-model", api_key="synthetic-not-a-real-key",
        base_url="https://provider.invalid/v1", params={"budget_policy": policy},
        tenant_id=uuid.uuid4(), credential_id=uuid.uuid4(), task_type="inference")
    return s.ResolvedCredential(**{**values, **changes})


class PolicyTests(unittest.TestCase):
    def test_embedding_batches_fit_reserved_aggregate_window(self):
        from app.tasks.embedding_tasks import embedding_chunks
        rc = credential()
        rc.params["budget_policy"].update(context_tokens=1000, max_output_tokens=0)
        targets = [(index, "à" * 200) for index in range(9)]
        chunks = list(embedding_chunks(rc, targets))
        self.assertEqual([len(chunk) for chunk in chunks], [2, 2, 2, 2, 1])
        self.assertEqual([row for chunk in chunks for row in chunk], targets)
        self.assertTrue(all(sum(len(t.encode()) + 32 for _, t in chunk) <= 1000 for chunk in chunks))

    def test_decimal_reserves_without_cent_rounding(self):
        policy = a.policy_for(credential())
        self.assertEqual(policy.estimate(1, 0), 1)  # one micro-USD, not one cent
        self.assertEqual(policy.estimate(100, 100), 136)
        self.assertEqual(policy.reservation, 13581)

    def test_malformed_negative_expired_and_wrong_model_rejected(self):
        rc = credential()
        for updates in ({"context_tokens": -1}, {"context_tokens": True}, {"max_output_tokens": 100001},
                        {"input_usd_per_million": "NaN"}, {"api_key": "forbidden"}, {"output_usd_per_million": "0"}):
            with self.subTest(updates=updates), self.assertRaises(ValidationError):
                a.BudgetPolicy.model_validate({**rc.params["budget_policy"], **updates})
        rc.model = "test-exact-model-plus"
        with self.assertRaises(HTTPException): a.policy_for(rc)
        rc.model = "test-exact-model"
        rc.params["budget_policy"]["valid_until"] = "2000-01-01T00:00:00Z"
        with self.assertRaises(HTTPException): a.policy_for(rc)
        self.assertEqual(a.policy_for(rc, fresh=False).model, rc.model)

    def test_snapshot_and_repr_exclude_key_and_arbitrary_params(self):
        rc = credential()
        rc.params["api_key"] = "another-synthetic-secret"
        saved = a.canonical(a.snapshot(rc)).decode()
        self.assertNotIn("synthetic-not-a-real-key", saved)
        self.assertNotIn("another-synthetic-secret", saved)
        self.assertNotIn("synthetic-not-a-real-key", repr(rc))
        for url in ("https://user:pass@example.invalid/v1", "https://example.invalid/v1?key=test", "https://example.invalid/#secret"):
            rc.base_url = url
            with self.assertRaises(HTTPException): a.snapshot(rc)

    def test_missing_false_zero_or_over_ceiling_usage_never_assumed_free(self):
        policy = a.policy_for(credential())
        for usage in ({}, {"prompt": None, "completion": 0}, {"prompt": True, "completion": 0},
                      {"prompt": 0, "completion": 0}, {"prompt": 100001, "completion": 1},
                      {"prompt": 10, "completion": 1001}):
            with self.subTest(usage=usage), self.assertRaises(ValueError): a.validate_usage(usage, policy)

    def test_budget_and_reconciliation_reject_invalid_amounts(self):
        for value in (-1, True, 1.2, "20", 100_000_001):
            with self.subTest(value=value), self.assertRaises(ValidationError): BudgetUpdate(monthly_budget_cents=value)
        with self.assertRaises(ValidationError): ReconcileCall(cost_microusd=0, evidence="timeout")

    def test_local_label_cannot_point_to_paid_endpoint(self):
        rc = credential(engine="ollama", api_key=None, base_url="https://api.openai.com/v1")
        with self.assertRaises(HTTPException): a.validate_local(rc)
        rc.base_url = "http://ollama:11434/v1"
        a.validate_local(rc)
        rc.model = "model:cloud"
        with self.assertRaises(HTTPException): a.validate_local(rc)

    def test_direct_paid_transports_and_batch_submission_are_blocked(self):
        rc = credential()
        with patch("openai.OpenAI") as client:
            with self.assertRaises(RuntimeError): s._embed_texts_transport(rc, ["text"])
            with self.assertRaises(RuntimeError): s._extract_structured_transport(rc, "text", schema={}, system="test")
            with self.assertRaises(HTTPException): s.submit_chat_batch(rc, [("id", "text")], system="test")
            client.assert_not_called()


class AdmissionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # These tests isolate the financial/result pipeline; the real DB guard has
        # separate contract and PostgreSQL tests in test_unused_storage.py.
        guard = patch.object(a.response_guard, "hold", return_value=MagicMock(__aenter__=AsyncMock(), __aexit__=AsyncMock(return_value=False)))
        guard.start(); self.addCleanup(guard.stop)

    async def test_reserve_rejects_before_any_dispatch_or_insert(self):
        rc = credential()
        for budget, balance, status in (
            (None, dict(settled_microusd=0, reserved_microusd=0, unknown_calls=0), 409),
            (1, dict(settled_microusd=0, reserved_microusd=9000, unknown_calls=0), 402),
            (100, dict(settled_microusd=0, reserved_microusd=1, unknown_calls=1), 409),
        ):
            session = MagicMock(scalar=AsyncMock(side_effect=[SimpleNamespace(monthly_budget_cents=budget), None]), commit=AsyncMock())
            manager = MagicMock(__aenter__=AsyncMock(return_value=session), __aexit__=AsyncMock(return_value=False))
            with self.subTest(budget=budget), patch.object(a, "SessionLocal", return_value=manager), \
                 patch.object(a, "set_rls_context", AsyncMock()), patch.object(a, "totals", AsyncMock(return_value=balance)):
                with self.assertRaises(HTTPException) as error:
                    await a._reserve(rc, a.snapshot(rc), "a" * 64, "b" * 64, 2000)
                self.assertEqual(error.exception.status_code, status)
                session.add.assert_not_called(); session.commit.assert_not_awaited()

    async def test_reservation_is_committed_before_transport_and_result_before_settlement(self):
        rc = credential()
        call = SimpleNamespace(id=uuid.uuid4(), profile=a.snapshot(rc), response_write_id=uuid.uuid4())
        order = []
        async def reserve(*args): order.append("durable-reserve"); return call, True
        async def put(*args, **kwargs):
            self.assertEqual(kwargs["write_id"], call.response_write_id)
            order.append("private-result")
        async def settle(*args): order.append("durable-settlement")
        def transport():
            self.assertTrue(a._ADMITTED.get()); order.append("provider")
            return {"records": []}, {"prompt": 100, "completion": 10, "request_id": "request-test"}
        with patch.object(a, "_reserve", side_effect=reserve), patch.object(a, "_settle", side_effect=settle), \
             patch.object(a.storage, "put_object", side_effect=put), patch.object(a, "_unknown", AsyncMock()) as unknown:
            data, usage = await a.execute(rc, kind="test", request="source", transport=transport)
            self.assertEqual(data, {"records": []}); self.assertEqual(usage["ai_call_id"], str(call.id))
            self.assertEqual(order, ["durable-reserve", "provider", "private-result", "durable-settlement"])
            unknown.assert_not_awaited(); self.assertFalse(a._ADMITTED.get())

    async def test_timeout_bad_usage_and_storage_failure_retain_reservation(self):
        rc, call = credential(), SimpleNamespace(id=uuid.uuid4(), response_write_id=uuid.uuid4())
        call.profile = a.snapshot(rc)
        for mode in ("timeout", "no-usage", "storage", "db"):
            transport = MagicMock(side_effect=TimeoutError("not logged: synthetic private input")) if mode == "timeout" else MagicMock(return_value=("text", {"prompt": None if mode == "no-usage" else 10, "completion": 5}))
            with self.subTest(mode=mode), patch.object(a, "_reserve", AsyncMock(return_value=(call, True))), \
                 patch.object(a.storage, "put_object", AsyncMock(side_effect=OSError() if mode == "storage" else None)), \
                 patch.object(a, "_settle", AsyncMock(side_effect=OSError() if mode == "db" else None)), \
                 patch.object(a, "_unknown", AsyncMock()) as unknown:
                with self.assertRaises(HTTPException) as error:
                    await a.execute(rc, kind="test", request="text", transport=transport)
                self.assertEqual(transport.call_count, 1); unknown.assert_awaited_once()
                self.assertNotIn("synthetic private input", error.exception.detail)

    async def test_cancellation_marks_unknown_without_claiming_remote_cancel(self):
        rc, call = credential(), SimpleNamespace(id=uuid.uuid4(), response_write_id=uuid.uuid4())
        call.profile = a.snapshot(rc)
        with patch.object(a, "_reserve", AsyncMock(return_value=(call, True))), \
             patch.object(a.asyncio, "to_thread", AsyncMock(side_effect=asyncio.CancelledError)), \
             patch.object(a, "_unknown", AsyncMock()) as unknown:
            with self.assertRaises(asyncio.CancelledError):
                await a.execute(rc, kind="test", request="text", transport=MagicMock())
            self.assertEqual(unknown.call_args.args[-1], "cancelled_after_admission")

    async def test_paid_invalid_response_is_settled_and_not_retried(self):
        rc, call = credential(), SimpleNamespace(id=uuid.uuid4(), response_write_id=uuid.uuid4())
        call.profile = a.snapshot(rc)
        transport = MagicMock(side_effect=a.ReceivedResponseError("invalid_json", "truncated {", {"prompt": 100, "completion": 10}))
        with patch.object(a, "_reserve", AsyncMock(return_value=(call, True))), \
             patch.object(a.storage, "put_object", AsyncMock()), patch.object(a, "_settle", AsyncMock()) as settle, \
             patch.object(a, "_unknown", AsyncMock()) as unknown:
            with self.assertRaises(ValueError): await a.execute(rc, kind="test", request="text", transport=transport)
            settle.assert_awaited_once(); unknown.assert_not_awaited(); self.assertEqual(transport.call_count, 1)

    async def test_replay_uses_saved_result_even_if_profile_expired(self):
        rc = credential(); rc.params["budget_policy"]["valid_until"] = "2000-01-01T00:00:00Z"
        call = SimpleNamespace(id=uuid.uuid4(), state="succeeded", result_key="private-result", result_checksum="digest")
        transport = MagicMock()
        with patch.object(a, "_reserve", AsyncMock(return_value=(call, False))), \
             patch.object(a, "read_saved_response", AsyncMock(return_value=({"data": [1], "usage": {"prompt": 1, "completion": 0}}, "digest"))):
            result, _ = await a.execute(rc, kind="test", request="same", transport=transport)
            self.assertEqual(result, [1]); transport.assert_not_called()

    async def test_unknown_or_reconciled_without_response_is_not_retried(self):
        for state in ("unknown", "inflight", "reconciled"):
            transport = MagicMock()
            call = SimpleNamespace(id=uuid.uuid4(), state=state)
            with self.subTest(state=state), patch.object(a, "_reserve", AsyncMock(return_value=(call, False))):
                with self.assertRaises(HTTPException): await a.execute(credential(), kind="test", request="text", transport=transport)
                transport.assert_not_called()

    async def test_saved_response_bounded_and_checksum_verified(self):
        for oversize, wrong_hash in ((False, False), (True, False), (False, True)):
            content = b'{"data": []}'
            stream = MagicMock(wraps=io.BytesIO(content))
            obj = {"Body": stream, "ContentLength": a.MAX_RESULT_BYTES + 1 if oversize else len(content)}
            digest = "wrong" if wrong_hash else hashlib.sha256(content).hexdigest()
            with self.subTest(oversize=oversize, wrong_hash=wrong_hash), patch.object(a.storage, "open_object", AsyncMock(return_value=obj)):
                if oversize or wrong_hash:
                    with self.assertRaises(ValueError): await a.read_saved_response("test", digest)
                else:
                    self.assertEqual((await a.read_saved_response("test", digest))[0], {"data": []})
                    stream.read.assert_called_once_with(a.MAX_RESULT_BYTES + 1)
                stream.close.assert_called_once()


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_anthropic_uses_native_client_and_accounts_for_cache_tokens(self):
        rc = credential(engine="claude", base_url="https://anthropic.invalid")
        response = SimpleNamespace(content=[SimpleNamespace(type="text", text='{"records":[]}')], stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=10, output_tokens=3, cache_creation_input_tokens=5, cache_read_input_tokens=2),
            _request_id="anthropic-test")
        client = MagicMock(); client.messages.create.return_value = response
        token = a._ADMITTED.set(True)
        try:
            with patch("openai.OpenAI") as wrong_client, patch("anthropic.Anthropic", return_value=client) as native:
                data, usage = s._extract_structured_transport(rc, "text", schema={}, system="test")
                wrong_client.assert_not_called()
                self.assertEqual(native.call_args.kwargs["base_url"], "https://anthropic.invalid")
                self.assertEqual(native.call_args.kwargs["max_retries"], 0)
                self.assertEqual(client.messages.create.call_args.kwargs["max_tokens"], 1000)
                self.assertEqual(usage["prompt"], 17); self.assertEqual(data, {"records": []})
        finally: a._ADMITTED.reset(token)

    async def test_openai_single_request_has_output_cap_no_retry_or_paid_fallback(self):
        rc = credential()
        client = MagicMock()
        response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"records":[]}'), finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=3, total_tokens=13), _request_id="req-test")
        client.chat.completions.create.return_value = response
        token = a._ADMITTED.set(True)
        try:
            with patch("openai.OpenAI", return_value=client) as factory:
                data, usage = s._extract_structured_transport(rc, "text", schema={}, system="test")
                self.assertEqual(data, {"records": []}); self.assertEqual(usage["request_id"], "req-test")
                self.assertEqual(factory.call_args.kwargs["max_retries"], 0)
                self.assertEqual(client.chat.completions.create.call_args.kwargs["max_completion_tokens"], 1000)
                self.assertEqual(client.chat.completions.create.call_args.kwargs["n"], 1)
                client.chat.completions.create.reset_mock(side_effect=True)
                client.chat.completions.create.side_effect = TimeoutError()
                with self.assertRaises(TimeoutError): s._extract_structured_transport(rc, "text", schema={}, system="test")
                client.chat.completions.create.assert_called_once()
        finally: a._ADMITTED.reset(token)

    async def test_embedding_indices_are_validated_and_sorted(self):
        rc = credential()
        client = MagicMock()
        response = SimpleNamespace(data=[SimpleNamespace(index=1, embedding=[2.] * 1024), SimpleNamespace(index=0, embedding=[1.] * 1024)],
                                   usage=SimpleNamespace(prompt_tokens=10), _request_id="req-emb")
        client.embeddings.create.return_value = response
        token = a._ADMITTED.set(True)
        try:
            with patch("openai.OpenAI", return_value=client):
                vectors, _ = s._embed_texts_transport(rc, ["first", "second"])
                self.assertEqual([v[0] for v in vectors], [1., 2.])
                response.data[0].index = 0
                with self.assertRaises(a.ReceivedResponseError): s._embed_texts_transport(rc, ["first", "second"])
        finally: a._ADMITTED.reset(token)


if __name__ == "__main__": unittest.main()
