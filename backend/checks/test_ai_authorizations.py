"""Offline contracts; no database, network, real keys or provider calls."""
import copy
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql
from app.modules.providers import admission as a, authorizations as api, billing as b, service as s
from test_ai_admission import credential


def renewal(profile, **changes):
    return {**profile["budget_policy"], "input_usd_per_million": "2", "output_usd_per_million": "3",
            "valid_until": "2099-01-01T00:00:00Z", "source": "Synthetic new tariff evidence", **changes}


def authorization(profile, **changes):
    return SimpleNamespace(id=uuid.uuid4(), version=2, base_profile_hash=a.fingerprint(profile),
        budget_policy=renewal(profile), **changes)


class FinancialPolicyTests(unittest.TestCase):
    def test_only_financial_fields_may_change(self):
        original = a.snapshot(credential())
        self.assertEqual(b.compatible_policy(original, renewal(original)).reservation, 203000)
        for key, value in {"model": "another", "context_tokens": 999999, "max_output_tokens": 999,
                           "output_parameter": "max_tokens", "response_format": "json_object"}.items():
            with self.subTest(key=key), self.assertRaises(HTTPException):
                b.compatible_policy(original, renewal(original, **{key: value}))

    def test_scope_priority_is_run_then_generation_then_job(self):
        rc = credential(job_id=uuid.uuid4(), research_run_id=uuid.uuid4(), embedding_generation_id=uuid.uuid4())
        self.assertEqual(b.credential_scope(rc), ("research_run", rc.research_run_id))
        rc.research_run_id = None
        self.assertEqual(b.credential_scope(rc), ("embedding_generation", rc.embedding_generation_id))
        rc.embedding_generation_id = None
        self.assertEqual(b.credential_scope(rc), ("job", rc.job_id))
        rc.job_id = None
        self.assertEqual(b.credential_scope(rc), (None, None))

    def test_explicit_true_and_no_execution_or_secret_overrides(self):
        values = dict(scope="job", target_id=uuid.uuid4(), task_type="inference", input_usd_per_million="2",
            output_usd_per_million="3", valid_until="2099-01-01T00:00:00Z", source="Synthetic evidence",
            client_id=uuid.uuid4(), preview_hash="a" * 64, authorize=True, reason="Test reason")
        api.Approval(**values)
        for changes in ({"authorize": 1}, {"authorize": "true"}, {"authorize": False}, {"model": "other"},
                        {"api_key": "not-real"}, {"source": " " * 10}, {"valid_until": "2099-01-01"}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                api.Approval(**{**values, **changes})


class BillingAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_receipt_carries_renewal_but_root_fingerprint_is_unchanged(self):
        rc = credential(job_id=uuid.uuid4())
        rc.params["budget_policy"]["valid_until"] = "2000-01-01T00:00:00Z"
        original = a.snapshot(rc); before = copy.deepcopy(original)
        row = authorization(original)
        with patch.object(b, "latest", AsyncMock(return_value=row)):
            effective, amount = await b.admitted_profile(MagicMock(), rc, original, 1)
        self.assertEqual(amount, 203000)
        self.assertEqual(effective["execution_profile"], before)
        self.assertEqual(effective["billing_authorization"]["id"], str(row.id))
        self.assertEqual(original, before); self.assertEqual(a.snapshot(rc), before)
        self.assertNotIn("synthetic-not-a-real-key", a.canonical(effective).decode())

    async def test_wrong_base_expired_and_invalid_renewals_block_admission(self):
        rc = credential(job_id=uuid.uuid4()); profile = a.snapshot(rc)
        for mode in ("base", "expired", "limits", "output_price"):
            row = authorization(profile)
            if mode == "base": row.base_profile_hash = "0" * 64
            if mode == "expired": row.budget_policy["valid_until"] = "2000-01-01T00:00:00Z"
            if mode == "limits": row.budget_policy["context_tokens"] = 200000
            if mode == "output_price": row.budget_policy["output_usd_per_million"] = "0"
            with self.subTest(mode=mode), patch.object(b, "latest", AsyncMock(return_value=row)), self.assertRaises(HTTPException):
                await b.admitted_profile(MagicMock(), rc, profile, 1)

    async def test_original_expiry_still_applies_without_authorization(self):
        rc = credential(); rc.params["budget_policy"]["valid_until"] = "2000-01-01T00:00:00Z"
        with patch.object(b, "latest", AsyncMock(return_value=None)), self.assertRaises(HTTPException):
            await b.admitted_profile(MagicMock(), rc, a.snapshot(rc), 1)

    async def test_lookup_is_tenant_owner_task_scoped_and_newest_first(self):
        session = MagicMock(scalar=AsyncMock(return_value=None))
        tid, target = uuid.uuid4(), uuid.uuid4()
        await b.latest(session, tid, "job", target, "inference")
        sql = str(session.scalar.call_args.args[0].compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        for fragment in (str(tid), str(target), "inference", "version DESC", "LIMIT 1"):
            self.assertIn(fragment, sql)

    async def test_existing_receipt_replayed_before_lookup_or_expiry(self):
        rc = credential(job_id=uuid.uuid4()); profile = a.snapshot(rc)
        previous = SimpleNamespace(id=uuid.uuid4())
        session = MagicMock(scalar=AsyncMock(side_effect=[SimpleNamespace(monthly_budget_cents=0), previous]))
        manager = MagicMock(__aenter__=AsyncMock(return_value=session), __aexit__=AsyncMock(return_value=False))
        with patch.object(a, "SessionLocal", return_value=manager), patch.object(a, "set_rls_context", AsyncMock()), \
             patch.object(b, "latest", AsyncMock()) as lookup:
            self.assertEqual(await a._reserve(rc, profile, "a" * 64, "b" * 64, 1), (previous, False))
        lookup.assert_not_awaited(); session.add.assert_not_called()

    async def test_renewed_price_is_checked_against_budget_inside_lock(self):
        rc = credential(job_id=uuid.uuid4()); profile = a.snapshot(rc)
        session = MagicMock(scalar=AsyncMock(side_effect=[SimpleNamespace(monthly_budget_cents=1), None, "running"]), commit=AsyncMock())
        manager = MagicMock(__aenter__=AsyncMock(return_value=session), __aexit__=AsyncMock(return_value=False))
        async def lookup(*args):
            sql = str(session.scalar.call_args_list[0].args[0].compile(dialect=postgresql.dialect()))
            self.assertIn("FOR NO KEY UPDATE", sql)
            return authorization(profile)
        with patch.object(a, "SessionLocal", return_value=manager), patch.object(a, "set_rls_context", AsyncMock()), \
             patch.object(b, "latest", side_effect=lookup), patch.object(a, "totals", AsyncMock(return_value={"unknown_calls": 0, "settled_microusd": 0, "reserved_microusd": 0})):
            with self.assertRaises(HTTPException) as error:
                await a._reserve(rc, profile, "a" * 64, "b" * 64, 1)
            self.assertEqual(error.exception.status_code, 402)
        session.add.assert_not_called(); session.commit.assert_not_awaited()

    async def test_settlement_uses_receipt_price_not_original_worker_policy(self):
        rc = credential(); profile = a.snapshot(rc)
        call = SimpleNamespace(id=uuid.uuid4(), response_write_id=uuid.uuid4(), profile={**profile, "budget_policy": renewal(profile), "execution_profile": profile})
        with patch.object(a.response_guard, "hold", return_value=MagicMock(__aenter__=AsyncMock(), __aexit__=AsyncMock(return_value=False))), \
             patch.object(a, "_reserve", AsyncMock(return_value=(call, True))), \
             patch.object(a.storage, "put_object", AsyncMock()), patch.object(a, "_settle", AsyncMock()) as settle:
            await a.execute(rc, kind="synthetic", request="source", transport=lambda: ("answer", {"prompt": 100, "completion": 10}))
        self.assertEqual(settle.call_args.args[-1], 230)
        self.assertEqual(a.snapshot(rc), profile)

    async def test_job_redelivery_resolves_original_execution_profile(self):
        tid, jid = uuid.uuid4(), uuid.uuid4()
        original = a.snapshot(credential(tenant_id=tid, job_id=jid))
        receipt = SimpleNamespace(profile={**original, "budget_policy": renewal(original), "execution_profile": original})
        session = MagicMock(scalar=AsyncMock(side_effect=[SimpleNamespace(params={}), receipt]),
            get=AsyncMock(return_value=SimpleNamespace(id=uuid.UUID(original["credential_id"]), is_active=True, scope="tenant", tenant_id=tid)))
        with patch.object(s, "_decrypt", return_value="synthetic"):
            resolved = await s.ProviderService(session).resolve(tenant_id=tid, job_id=jid, task_type="inference")
        self.assertEqual(a.snapshot(resolved), original)


class AuthorizationApiTests(unittest.IsolatedAsyncioTestCase):
    def setup_values(self):
        profile = a.snapshot(credential())
        tid, uid, rid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        owner = SimpleNamespace(id=rid, tenant_id=tid, title="Test run", status="paused", version=3, profiles={"inference": profile})
        body = api.Proposal(scope="research_run", target_id=rid, task_type="inference", **{
            key: value for key, value in renewal(profile).items() if key in {"input_usd_per_million", "output_usd_per_million", "valid_until", "source"}})
        return profile, owner, body, SimpleNamespace(tenant_id=tid, user_id=uid)

    async def test_preview_is_readonly_preserves_profile_and_verifies_credential(self):
        profile, owner, body, principal = self.setup_values()
        session = MagicMock(); before = copy.deepcopy(profile)
        with patch.object(api, "owner_profile", AsyncMock(return_value=("research_run", owner, profile))), \
             patch.object(api, "latest", AsyncMock(return_value=None)), patch.object(s.ProviderService, "resolve_frozen", AsyncMock()) as resolve:
            result, _ = await api.preview_data(session, principal.tenant_id, body)
        self.assertEqual(result["after_reservation_microusd"], 203000)
        self.assertEqual(profile, before); resolve.assert_awaited_once()
        session.add.assert_not_called(); session.commit.assert_not_called()
        self.assertNotIn("api_key", a.canonical(result).decode())

    async def test_approval_has_event_but_does_not_start_or_change_profile(self):
        profile, owner, body, principal = self.setup_values()
        session = MagicMock(scalar=AsyncMock(), get=AsyncMock(return_value=None), flush=AsyncMock())
        with patch.object(api, "owner_profile", AsyncMock(return_value=("research_run", owner, profile))), \
             patch.object(api, "latest", AsyncMock(return_value=None)), patch.object(s.ProviderService, "resolve_frozen", AsyncMock()):
            result, _ = await api.preview_data(session, principal.tenant_id, body)
            approval = api.Approval(**body.model_dump(), client_id=uuid.uuid4(), preview_hash=result["preview_hash"], authorize=True, reason="Test renewal")
            response = await api.approve(approval, principal, session)
        self.assertEqual(owner.status, "paused"); self.assertEqual(owner.version, 4)
        self.assertEqual(owner.profiles, {"inference": profile})
        self.assertEqual(response["version"], 1); self.assertEqual(session.add.call_count, 2)
        session.commit.assert_not_called()

    async def test_stale_preview_rejected_without_appending(self):
        profile, owner, body, principal = self.setup_values()
        session = MagicMock(scalar=AsyncMock(), get=AsyncMock(return_value=None), flush=AsyncMock())
        with patch.object(api, "owner_profile", AsyncMock(return_value=("research_run", owner, profile))), \
             patch.object(api, "latest", AsyncMock(return_value=None)), patch.object(s.ProviderService, "resolve_frozen", AsyncMock()):
            result, _ = await api.preview_data(session, principal.tenant_id, body)
            owner.version += 1
            approval = api.Approval(**body.model_dump(), client_id=uuid.uuid4(), preview_hash=result["preview_hash"], authorize=True, reason="Test renewal")
            with self.assertRaises(HTTPException) as error: await api.approve(approval, principal, session)
        self.assertEqual(error.exception.status_code, 409); session.add.assert_not_called()

    async def test_terminal_run_blocked_but_ready_index_can_be_renewed(self):
        profile, owner, body, principal = self.setup_values()
        for scope, status, allowed in (("research_run", "completed", False), ("research_run", "cancelled", False),
                                      ("embedding_generation", "ready", True)):
            owner.status = status
            with self.subTest(scope=scope), patch.object(api, "owner_profile", AsyncMock(return_value=(scope, owner, profile))), \
                 patch.object(api, "latest", AsyncMock(return_value=None)), patch.object(s.ProviderService, "resolve_frozen", AsyncMock()):
                if allowed: await api.preview_data(MagicMock(), principal.tenant_id, body)
                else:
                    with self.assertRaises(HTTPException): await api.preview_data(MagicMock(), principal.tenant_id, body)

    async def test_job_owner_canonicalizes_to_parent_without_job_lock(self):
        profile, owner, body, principal = self.setup_values()
        session = MagicMock(scalar=AsyncMock(side_effect=[SimpleNamespace(params={"research_run_id": str(owner.id)}), owner]))
        result = await api.owner_profile(session, principal.tenant_id, "job", uuid.uuid4(), "inference", lock=True)
        self.assertEqual(result, ("research_run", owner, profile))
        first = str(session.scalar.call_args_list[0].args[0].compile(dialect=postgresql.dialect()))
        second = str(session.scalar.call_args_list[1].args[0].compile(dialect=postgresql.dialect()))
        self.assertNotIn("FOR UPDATE", first); self.assertIn("FOR UPDATE", second)


if __name__ == "__main__": unittest.main()
