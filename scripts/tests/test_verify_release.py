"""Publication must fail closed. Only synthetic responses; no token/network needed."""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib import error, parse

from scripts import verify_release as gate

SHA = "a" * 40
OTHER_SHA = "b" * 40
REPOSITORY = "test-owner/gensuite"


class FakeGitHub:
    repository = REPOSITORY

    def __init__(self):
        self.responses = {}
        self.calls = []
        for number, (filename, names) in enumerate(gate.REQUIRED.items(), 1):
            workflow_id, run_id = number, number * 10
            self.responses[f"actions/workflows/{filename}"] = {
                "id": workflow_id, "state": "active", "path": f".github/workflows/{filename}"}
            run = {"id": run_id, "workflow_id": workflow_id, "run_number": 12,
                   "run_attempt": 2, "head_sha": SHA, "head_branch": "main", "event": "push",
                   "status": "completed", "conclusion": "success",
                   "repository": {"full_name": REPOSITORY}, "head_repository": {"full_name": REPOSITORY}}
            self.responses[f"actions/workflows/{filename}/runs"] = {"total_count": 1, "workflow_runs": [run]}
            jobs = [{"name": name, "run_id": run_id, "head_sha": SHA,
                     "status": "completed", "conclusion": "success"} for name in sorted(names)]
            self.responses[f"actions/runs/{run_id}/attempts/2/jobs"] = {"total_count": len(jobs), "jobs": jobs}

    def get(self, path, **query):
        self.calls.append((path, query))
        return deepcopy(self.responses[path])

    def run(self, filename="ci.yml"):
        return self.responses[f"actions/workflows/{filename}/runs"]["workflow_runs"][0]

    def jobs(self):
        return self.responses["actions/runs/10/attempts/2/jobs"]["jobs"]


class ReleaseGateTests(unittest.TestCase):
    def test_success_requires_both_workflows_and_pinned_attempt_jobs(self):
        api = FakeGitHub()
        self.assertEqual(gate.verify(api, SHA), [("ci.yml", 10, 2), ("recovery.yml", 20, 2)])
        self.assertEqual(len(api.calls), 8)
        for path, query in api.calls:
            if path.endswith("/runs"):
                self.assertEqual(query, {"head_sha": SHA, "branch": "main", "event": "push", "per_page": 100, "page": 1})
                self.assertNotIn("status", query)
            if path.endswith("/jobs"):
                self.assertIn("/attempts/2/jobs", path)

    def test_missing_workflow_or_untrusted_identity_is_rejected(self):
        for field, value in (("id", True), ("id", 0), ("id", "1"), ("path", ".github/workflows/fake.yml"), ("state", "disabled_manually")):
            with self.subTest(field=field, value=value):
                api = FakeGitHub()
                api.responses["actions/workflows/recovery.yml"][field] = value
                with self.assertRaises(gate.GateError): gate.verify(api, SHA)

    def test_empty_runs_in_either_workflow_block(self):
        for filename in gate.REQUIRED:
            api = FakeGitHub()
            api.responses[f"actions/workflows/{filename}/runs"] = {"total_count": 0, "workflow_runs": []}
            with self.subTest(filename=filename), self.assertRaises(gate.GateError): gate.verify(api, SHA)

    def test_never_accepts_pr_fork_other_branch_sha_or_workflow(self):
        for field, value in (("head_sha", OTHER_SHA), ("head_branch", "feature"), ("event", "pull_request"),
                ("event", "workflow_dispatch"), ("workflow_id", 9), ("repository", {"full_name": "fork/gensuite"}),
                ("head_repository", {"full_name": "fork/gensuite"}), ("head_repository", None),
                ("head_repository", {"full_name": None}), ("id", True), ("run_attempt", 0), ("run_number", "12")):
            with self.subTest(field=field, value=value):
                api = FakeGitHub(); api.run()[field] = value
                with self.assertRaises(gate.GateError): gate.verify(api, SHA)

    def test_latest_run_failure_or_pending_cannot_use_older_green(self):
        for status, conclusion in (("completed", "failure"), ("queued", None), ("in_progress", None),
                ("completed", "cancelled"), ("completed", "skipped"), ("completed", "neutral"),
                ("completed", "timed_out"), ("waiting", "success"), (None, None)):
            for reverse in (False, True):
                with self.subTest(status=status, conclusion=conclusion, reverse=reverse):
                    api = FakeGitHub(); newer = {**api.run(), "id": 30, "run_number": 13, "status": status, "conclusion": conclusion}
                    runs = [api.run(), newer]
                    api.responses["actions/workflows/ci.yml/runs"] = {"total_count": 2, "workflow_runs": runs[::-1] if reverse else runs}
                    with self.assertRaises(gate.GateError): gate.verify(api, SHA)

    def test_old_failure_is_not_confused_with_latest_success(self):
        api = FakeGitHub()
        old = {**api.run(), "id": 9, "run_number": 11, "conclusion": "failure"}
        api.responses["actions/workflows/ci.yml/runs"] = {"total_count": 2, "workflow_runs": [api.run(), old]}
        gate.verify(api, SHA)

    def test_duplicate_ambiguous_runs_block(self):
        for changes in ({}, {"id": 11}, {"run_number": 13}):
            api = FakeGitHub()
            duplicate = {**api.run(), **changes}
            api.responses["actions/workflows/ci.yml/runs"] = {"total_count": 2, "workflow_runs": [api.run(), duplicate]}
            with self.subTest(changes=changes), self.assertRaises(gate.GateError): gate.verify(api, SHA)

    def test_incomplete_oversized_or_malformed_lists_block(self):
        for data in ({}, {"total_count": True, "jobs": [{}]}, {"total_count": "0", "jobs": []},
                {"total_count": -1, "jobs": []}, {"total_count": 1, "jobs": []},
                {"total_count": 101, "jobs": [{}] * 100}, {"total_count": 1, "jobs": [None]},
                {"total_count": 0, "jobs": {}}):
            with self.subTest(data=data), self.assertRaises(gate.GateError): gate.complete_list(data, "jobs")

    def test_missing_duplicate_skipped_failed_or_foreign_jobs_block(self):
        for field, value in (("name", "unrelated"), ("name", None), ("conclusion", "skipped"),
                ("conclusion", "failure"), ("status", "queued"), ("run_id", 1), ("run_id", "10"), ("head_sha", OTHER_SHA)):
            api = FakeGitHub(); api.jobs()[0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(gate.GateError): gate.verify(api, SHA)
        api = FakeGitHub(); api.jobs()[0]["name"] = api.jobs()[1]["name"]
        with self.assertRaises(gate.GateError): gate.verify(api, SHA)

    def test_rerun_or_new_run_during_verification_blocks(self):
        for changes in ({"run_attempt": 3}, {"id": 11, "run_number": 13}, {"status": "in_progress", "conclusion": None}):
            api = FakeGitHub(); original = api.get
            def changing(path, **query):
                if path == "actions/runs/20/attempts/2/jobs": api.run().update(changes)
                return original(path, **query)
            api.get = changing
            with self.subTest(changes=changes), self.assertRaises(gate.GateError): gate.verify(api, SHA)

    def test_sha_must_be_full_immutable_lowercase_commit(self):
        for sha in ("main", "v1.2.3", "a" * 7, "A" * 40, SHA + "\n", None):
            with self.subTest(sha=sha), self.assertRaises(gate.GateError): gate.verify(FakeGitHub(), sha)


class GitHubTransportTests(unittest.TestCase):
    def api(self):
        api = gate.GitHub(REPOSITORY, "synthetic-do-not-log-token")
        api.opener = Mock()
        return api

    def response(self, api, raw, status=200):
        response = Mock(status=status)
        response.read.return_value = raw
        api.opener.open.return_value.__enter__ = Mock(return_value=response)
        api.opener.open.return_value.__exit__ = Mock(return_value=False)
        return response

    def test_https_bounded_get_auth_and_query_encoding(self):
        api = self.api(); response = self.response(api, b'{"total_count":0}')
        self.assertEqual(api.get("actions/workflows/ci.yml/runs", head_sha=SHA), {"total_count": 0})
        req = api.opener.open.call_args.args[0]
        self.assertEqual(parse.urlsplit(req.full_url).netloc, "api.github.com")
        self.assertEqual(req.get_method(), "GET")
        self.assertEqual(req.headers["Authorization"], "Bearer synthetic-do-not-log-token")
        self.assertEqual(api.opener.open.call_args.kwargs, {"timeout": 20})
        response.read.assert_called_once_with(gate.MAX_BYTES + 1)

    def test_network_errors_never_echo_token_or_response(self):
        for exc in (error.HTTPError("https://api.github.com", 403, "synthetic-do-not-log-token", {}, None),
                error.URLError("synthetic-do-not-log-token"), TimeoutError("synthetic-do-not-log-token")):
            api = self.api(); api.opener.open.side_effect = exc
            with self.assertRaises(gate.GateError) as caught: api.get("actions/workflows/ci.yml")
            self.assertNotIn("synthetic-do-not-log-token", str(caught.exception))

    def test_bad_json_size_and_non_200_block(self):
        for raw, status in ((b"[]", 200), (b"not JSON", 200), (b"\xff", 200),
                (b"x" * (gate.MAX_BYTES + 1), 200), (b"{}", 302), (b"{}", 204)):
            api = self.api(); self.response(api, raw, status)
            with self.subTest(size=len(raw), status=status), self.assertRaises(gate.GateError): api.get("actions/workflows/ci.yml")

    def test_no_redirect_even_to_same_host(self):
        for url in ("https://evil.example/", "http://api.github.com", "https://api.github.com/renamed"):
            with self.subTest(url=url), self.assertRaises(gate.GateError):
                gate.NoRedirect().redirect_request(None, None, 302, "Found", {}, url)
        opener = gate.GitHub(REPOSITORY, "synthetic").opener
        self.assertTrue(any(isinstance(h, gate.NoRedirect) for h in opener.handlers))

    def test_invalid_repo_endpoint_token_or_path_never_reaches_network(self):
        for repo in ("../repo", "owner/..", "owner/repo/path", "owner/repo?x=1", "owner/repo\n", None):
            with self.subTest(repo=repo), self.assertRaises(gate.GateError): gate.GitHub(repo, "synthetic")
        for endpoint in ("http://api.github.com", "https://evil.example", "https://api.github.com@evil.example", "https://api.github.com/"):
            with self.subTest(endpoint=endpoint), self.assertRaises(gate.GateError): gate.GitHub(REPOSITORY, "synthetic", endpoint)
        for token in ("", "synthetic\nvalue", "synthetic value", None):
            with self.subTest(token=token), self.assertRaises(gate.GateError): gate.GitHub(REPOSITORY, token)
        api = self.api()
        for path in ("https://evil.example", "actions/../../elsewhere", "actions/runs?token=evil"):
            with self.subTest(path=path), self.assertRaises(gate.GateError): api.get(path)
        api.opener.open.assert_not_called()


class EntrypointTests(unittest.TestCase):
    def invoke(self, *, event="release", checked_out=SHA, fail=False, **overrides):
        with tempfile.TemporaryDirectory(prefix="gensuite-release-gate-") as directory:
            output = Path(directory) / "output"
            environment = {"GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": event,
                           "GITHUB_SHA": SHA, "GITHUB_REPOSITORY": REPOSITORY,
                           "GITHUB_API_URL": gate.API_URL, "GH_TOKEN": "synthetic-do-not-log-token",
                           "GITHUB_OUTPUT": str(output), **overrides}
            stdout, stderr = io.StringIO(), io.StringIO()
            with patch.dict(os.environ, environment, clear=True), redirect_stdout(stdout), redirect_stderr(stderr), \
                    patch.object(gate.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, checked_out + "\n")) as git, \
                    patch.object(gate, "GitHub", return_value=FakeGitHub()), \
                    patch.object(gate, "verify", side_effect=gate.GateError("Fallo sintético") if fail else None,
                                 return_value=[("ci.yml", 10, 2), ("recovery.yml", 20, 2)]) as verify:
                result = gate.main()
            return result, output.read_text() if output.exists() else "", stdout.getvalue() + stderr.getvalue(), verify, git

    def test_release_and_dispatch_emit_only_verified_sha(self):
        for event in ("release", "workflow_dispatch"):
            result, output, logs, verify, git = self.invoke(event=event)
            self.assertEqual(result, 0); self.assertEqual(output, f"sha={SHA}\n")
            self.assertEqual(verify.call_args.args[1], SHA)
            self.assertEqual(git.call_args.args[0], ["git", "rev-parse", "--verify", "HEAD"])
            self.assertNotIn("synthetic-do-not-log-token", logs)
            self.assertIn("/actions/runs/20/attempts/2", logs)

    def test_failure_never_emits_sha_or_success(self):
        for kwargs in ({"fail": True}, {"checked_out": OTHER_SHA}, {"event": "pull_request"},
                {"GITHUB_SHA": "main"}, {"GITHUB_ACTIONS": "false"}, {"GITHUB_OUTPUT": ""}):
            result, output, logs, _, _ = self.invoke(**kwargs)
            self.assertEqual(result, 1); self.assertEqual(output, "")
            self.assertNotIn("Gate correcto", logs); self.assertNotIn("synthetic-do-not-log-token", logs)


class WorkflowContractTests(unittest.TestCase):
    def test_required_jobs_follow_actual_workflow_job_ids(self):
        for filename, required in gate.REQUIRED.items():
            workflow = (gate.ROOT / ".github/workflows" / filename).read_text()
            jobs = workflow.split("\njobs:\n", 1)[1]
            self.assertEqual(set(re.findall(r"^  ([a-z0-9_-]+):$", jobs, re.MULTILINE)), required)

    def test_release_has_read_only_gate_before_credentials_and_pinned_build(self):
        workflow = (gate.ROOT / ".github/workflows/docker-release.yml").read_text()
        prefix, build = workflow.split("\n  build-push:\n", 1)
        self.assertNotIn("packages: write", prefix)
        self.assertNotIn("docker/login-action", prefix)
        self.assertIn("actions: read", prefix)
        self.assertIn("ref: ${{ github.sha }}", prefix)
        self.assertIn("run: python3 scripts/verify_release.py", prefix)
        self.assertIn("sha: ${{ steps.gate.outputs.sha }}", prefix)
        self.assertIn("    needs: verify-release\n", build)
        self.assertIn("ref: ${{ needs.verify-release.outputs.sha }}", build)
        self.assertNotIn("always()", build)
        self.assertEqual(workflow.count("persist-credentials: false"), 2)


if __name__ == "__main__":
    unittest.main()
