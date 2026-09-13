"""Recovery contracts with real local bytes; PostgreSQL/S3 transports are synthetic."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.recovery import archive as a, database as d, objects as o, service as s, remote
from app.recovery.__main__ import main


class BoundedBody(io.BytesIO):
    def read(self, n=-1):
        assert 0 < n <= a.BLOCK, "Recovery must stream object bytes"
        return super().read(n)


class MemoryS3:
    def __init__(self, values=None):
        self.values = values or {"old-private": {"folder/../original.jpg": b"synthetic original"}, "old-public": {"latin.txt": "Baptizavi María".encode()}}
        self.bodies = []
        self.calls = []

    def row(self, key, value):
        return dict(Key=key, Size=len(value), ETag='"' + hashlib.sha256(value).hexdigest() + '"', LastModified=datetime(2026, 1, 1, tzinfo=timezone.utc))

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        def paginate(Bucket):
            for key, value in sorted(self.values[Bucket].items()):
                yield {"Contents": [self.row(key, value)]}
        return SimpleNamespace(paginate=paginate)

    def get_object(self, *, Bucket, Key, IfMatch=None):
        self.calls.append(("get", Bucket, Key, IfMatch))
        value = self.values[Bucket][Key]
        row = self.row(Key, value)
        if IfMatch is not None and IfMatch != row["ETag"]: raise RuntimeError("Synthetic precondition failed")
        body = BoundedBody(value); self.bodies.append(body)
        return dict(Body=body, ETag=row["ETag"], ContentType="application/octet-stream", Metadata={"fixture": "retained"})

    def head_bucket(self, *, Bucket):
        assert Bucket in self.values

    def list_objects_v2(self, *, Bucket, MaxKeys, Prefix=""):
        return {"Contents": [self.row(key, value) for key, value in sorted(self.values[Bucket].items()) if key.startswith(Prefix)][:MaxKeys]}

    def list_object_versions(self, **kw): return {}
    def list_multipart_uploads(self, **kw): return {}

    def upload_file(self, path, bucket, key, **kw):
        self.calls.append(("upload", bucket, key))
        self.values[bucket][key] = Path(path).read_bytes()


def settings(**changes):
    values = dict(postgres_host="fixture.invalid", postgres_port=5432, postgres_user="owner", postgres_password="synthetic-private",
        app_db_user="gensuite_app", app_db_password="synthetic-app", postgres_db="gensuite",
        suite_master_key=base64.b64encode(b"x" * 32).decode(), minio_bucket_private="old-private", minio_bucket_public="old-public")
    return SimpleNamespace(**{**values, **changes})


def fixture(root):
    root = Path(root); root.mkdir()
    backend = MemoryS3()
    with a.private_file(root / "database.dump") as stream: stream.write(b"PGDMPsynthetic-not-real-postgresql")
    totals, inventory = o.download_objects(backend, s.bucket_map(settings()), root)
    reference = {"role": "private", "key": "folder/../original.jpg", "sha256": hashlib.sha256(b"synthetic original").hexdigest(), "size": 18, "kind": "page"}
    with a.private_file(root / "references.jsonl") as stream: stream.write(a.canonical(reference) + b"\n")
    # Keep the baseline fixture on the documented v1 compatibility path. Dedicated
    # tests below exercise the stronger v2 database-content contract.
    manifest = {"format": a.LEGACY_FORMAT, "complete": True, "id": "fixture-backup", "objects": totals, "inventory_sha256": inventory,
        "references": 1, "buckets": s.bucket_map(settings()), "master_key_fingerprint": s.key_fingerprint(settings()),
        "source": {"system_identifier": "111", "database": "gensuite", "server_major": 16},
        "owner_role": "owner", "app_role": "gensuite_app", "database": {"revisions": ["0044"], "counts": {"persons": 2}},
        "files": {name: a.descriptor(root / name) for name in ("database.dump", "objects.jsonl", "references.jsonl")}}
    with a.private_file(root / "manifest.json") as stream: stream.write(a.canonical(manifest))
    return backend, manifest


def v2_database(*, revisions=None, counts=None):
    counts = counts or {"persons": 2}
    return {
        "revisions": revisions or ["0044"],
        "counts": counts,
        "triggers": [],
        "row_integrity": {
            "algorithm": d.ROW_HASH_ALGORITHM,
            "tables": {name: {"rows": rows, "sha256": hashlib.sha256(name.encode()).hexdigest()}
                       for name, rows in counts.items()},
        },
    }


def update_manifest(root, manifest):
    manifest["files"] = {name: a.descriptor(root / name) for name in manifest["files"]}
    (root / "manifest.json").write_bytes(a.canonical(manifest))


class ArchiveTests(unittest.TestCase):
    def test_complete_archive_checks_real_bytes_references_and_no_path_interpretation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "copy"
            backend, manifest = fixture(root)
            self.assertEqual(a.verify(root), manifest)
            self.assertEqual(a.object_path(root, "private", "folder/../original.jpg").parent, root / "objects" / "private")
            self.assertTrue(all(body.closed for body in backend.bodies))
            self.assertTrue(all(call[3] is not None for call in backend.calls))
            self.assertEqual((root / "objects.jsonl").stat().st_mode & 0o777, 0o600)

    def test_corrupt_dump_or_original_and_missing_completion_are_rejected(self):
        for target in ("database.dump", "original", "manifest.json"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as temp:
                root = Path(temp) / "copy"; fixture(root)
                path = a.object_path(root, "private", "folder/../original.jpg") if target == "original" else root / target
                if target == "manifest.json": path.unlink()
                else: path.write_bytes(b"corrupt")
                with self.assertRaises((a.RecoveryError, FileNotFoundError)): a.verify(root)

    def test_missing_database_reference_is_not_hidden_by_valid_inventory_checksums(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "copy"; _, manifest = fixture(root)
            (root / "references.jsonl").write_bytes(a.canonical({"role": "private", "key": "missing", "sha256": None, "size": None}) + b"\n")
            update_manifest(root, manifest)
            with self.assertRaises(a.RecoveryError): a.verify(root)

    def test_duplicate_inventory_and_symbolic_links_are_rejected(self):
        for kind in ("duplicate", "file-link", "directory-link"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temp:
                root = Path(temp) / "copy"; _, manifest = fixture(root)
                if kind == "duplicate":
                    listing = root / "objects.jsonl"; listing.write_bytes(listing.read_bytes() * 2)
                    update_manifest(root, manifest)
                elif kind == "file-link":
                    path = a.object_path(root, "private", "folder/../original.jpg")
                    other = Path(temp) / "other"; path.rename(other); path.symlink_to(other)
                else:
                    path = root / "objects" / "private"; other = Path(temp) / "other"
                    path.rename(other); path.symlink_to(other, target_is_directory=True)
                with self.assertRaises(a.RecoveryError): a.verify(root)

    def test_verify_cli_is_offline_and_does_not_claim_restore(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "copy"; fixture(root)
            with patch("sys.stdout", new_callable=io.StringIO) as output, patch.object(d, "connect", AsyncMock()) as connect:
                self.assertEqual(main(["verify", str(root)]), 0)
            connect.assert_not_awaited()
            result = json.loads(output.getvalue())
            self.assertTrue(result["integrity_verified"]); self.assertFalse(result["restoration_verified"])

    def test_new_directory_never_overwrites_an_existing_copy(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(a.RecoveryError): s.new_directory(temp)

    def test_v2_requires_complete_row_fingerprints_and_trigger_catalog(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "copy"; _, manifest = fixture(root)
            manifest["format"] = a.FORMAT
            manifest["database"] = v2_database()
            update_manifest(root, manifest)
            self.assertEqual(a.verify(root), manifest)
            for changed in (
                    {**manifest["database"], "triggers": None},
                    {**manifest["database"], "row_integrity": {}},
                    {**manifest["database"], "row_integrity": {**manifest["database"]["row_integrity"], "tables": {}}}):
                manifest["database"] = changed; update_manifest(root, manifest)
                with self.subTest(changed=changed), self.assertRaises(a.RecoveryError): a.verify(root)


class ObjectTests(unittest.TestCase):
    def test_inventory_change_does_not_finalize_copy(self):
        with tempfile.TemporaryDirectory() as temp:
            backend = MemoryS3()
            real_inventory = o.inventory
            count = 0
            def changing(s3, buckets):
                nonlocal count
                count += 1
                if count == 2: backend.values["old-private"]["new"] = b"changed"
                return real_inventory(s3, buckets)
            with patch.object(o, "inventory", changing), self.assertRaises(a.RecoveryError):
                o.download_objects(backend, s.bucket_map(settings()), temp)
            self.assertFalse((Path(temp) / "manifest.json").exists())

    def test_transfer_reads_back_exact_bytes_and_preserves_original_keys(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "copy"; fixture(root)
            backend = MemoryS3({"target-private": {}, "target-public": {}})
            buckets = {"private": "target-private", "public": "target-public"}
            o.require_empty_buckets(backend, buckets)
            o.upload_objects(backend, buckets, root)
            self.assertEqual(backend.values["target-private"]["folder/../original.jpg"], b"synthetic original")
            self.assertTrue(all(body.closed for body in backend.bodies))
            with self.assertRaises(a.RecoveryError): o.require_empty_buckets(backend, buckets)

    def test_old_versions_and_multipart_are_not_an_empty_destination(self):
        for method, result in (("list_object_versions", {"DeleteMarkers": [{}]}), ("list_multipart_uploads", {"Uploads": [{}]})):
            with self.subTest(method=method):
                backend = MemoryS3({"target-private": {}, "target-public": {}})
                with patch.object(backend, method, return_value=result), self.assertRaises(a.RecoveryError):
                    o.require_empty_buckets(backend, {"private": "target-private", "public": "target-public"})


class DatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_rls_probe_does_not_pass_when_every_query_is_denied(self):
        import uuid
        connection = MagicMock(fetch=AsyncMock(side_effect=[[{"table_name": "persons", "has_visibility": False}], [{"id": uuid.uuid4()}]]),
            fetchval=AsyncMock(side_effect=[1, 0, 0]), execute=AsyncMock())
        connection.transaction.return_value = MagicMock(__aenter__=AsyncMock(), __aexit__=AsyncMock(return_value=False))
        with self.assertRaises(a.RecoveryError): await d.check_rls(connection, "gensuite_app")

    async def test_source_refuses_even_idle_other_clients(self):
        connection = MagicMock(execute=AsyncMock(), fetchval=AsyncMock(return_value=1))
        with self.assertRaises(a.RecoveryError): await d.require_quiet(connection)

    async def test_restore_rejects_source_cluster_live_name_and_privileged_role(self):
        with tempfile.TemporaryDirectory() as temp:
            _, manifest = fixture(Path(temp) / "copy")
        for source_cluster, dbname, elevated in ((True, "gensuite_restore_test", False), (False, "gensuite", False), (False, "gensuite_restore_test", True)):
            with self.subTest(source_cluster=source_cluster, dbname=dbname, elevated=elevated):
                connection = MagicMock(fetchval=AsyncMock(return_value=0), fetchrow=AsyncMock(return_value={"rolsuper": elevated}), execute=AsyncMock())
                with patch.object(d, "identity", AsyncMock(return_value={"system_identifier": "111" if source_cluster else "222", "server_major": 16})), \
                     patch.object(d, "require_quiet", AsyncMock()), self.assertRaises(a.RecoveryError):
                    await d.require_empty_target(connection, settings(postgres_db=dbname), manifest)
                connection.execute.assert_not_awaited()

    async def test_subprocess_contract_never_drops_data_or_echoes_password(self):
        with patch.object(d.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="pg_restore (PostgreSQL) 16.0")) as run:
            d.pg_command(settings(), "pg_restore", "/tmp/fixture.dump")
        command = run.call_args.args[0]
        self.assertNotIn("--clean", command); self.assertNotIn("--create", command)
        self.assertIn("--single-transaction", command); self.assertIn("--exit-on-error", command)
        self.assertNotIn("synthetic-private", " ".join(command))
        self.assertNotIn("capture_output", run.call_args.kwargs)

    async def test_quarantine_preserves_ai_reservations_and_disables_automatic_execution(self):
        connection = MagicMock(execute=AsyncMock(return_value="UPDATE 1"))
        connection.transaction.return_value = MagicMock(__aenter__=AsyncMock(), __aexit__=AsyncMock(return_value=False))
        await d.quarantine(connection, {"private": "new-private", "public": "new-public"}, {"database": {"revisions": ["0044"]}, "buckets": {"private": "old-private", "public": "old-public"}})
        sql = "\n".join(call.args[0] for call in connection.execute.call_args_list)
        self.assertIn("state='unknown'", sql); self.assertIn("status='paused'", sql)
        self.assertIn("is_active=false", sql); self.assertIn("monthly_budget_cents=NULL", sql)
        self.assertNotIn("DELETE", sql); self.assertNotIn("reserved_microusd=", sql)


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_uses_one_exported_snapshot_and_finalizes_only_after_reference_verification(self):
        for valid_reference in (True, False):
            with self.subTest(valid_reference=valid_reference), tempfile.TemporaryDirectory() as temp:
                root = Path(temp) / "new"
                connection = MagicMock(close=AsyncMock(), fetchval=AsyncMock(return_value="fixture-snapshot"))
                connection.transaction.return_value = MagicMock(__aenter__=AsyncMock(), __aexit__=AsyncMock(return_value=False))
                async def references(conn, buckets, directory):
                    row = {"role": "private", "key": "folder/../original.jpg" if valid_reference else "missing", "sha256": None, "size": None}
                    with a.private_file(Path(directory) / "references.jsonl") as stream: stream.write(a.canonical(row) + b"\n")
                    return 1
                def dump(settings, action, path, *, snapshot):
                    self.assertEqual(snapshot, "fixture-snapshot")
                    Path(path).write_bytes(b"PGDMPfixture")
                with patch.object(d, "connect", AsyncMock(return_value=connection)), patch.object(d, "require_quiet", AsyncMock()), \
                     patch.object(d, "identity", AsyncMock(return_value={"system_identifier": "111", "server_major": 16})), \
                     patch.object(d, "metadata", AsyncMock(return_value=v2_database())), \
                     patch.object(d, "save_references", references), patch.object(d, "pg_command", dump):
                    if valid_reference:
                        result = await s.create(settings(), MemoryS3(), root, quiesced=True, key_escrow=True)
                        self.assertEqual(a.verify(root), result)
                    else:
                        with self.assertRaises(a.RecoveryError): await s.create(settings(), MemoryS3(), root, quiesced=True, key_escrow=True)
                        self.assertFalse((root / "manifest.json").exists())
                connection.transaction.assert_called_once_with(isolation="repeatable_read", readonly=True)
                connection.close.assert_awaited_once()

    async def test_missing_authority_or_wrong_master_key_never_contacts_destination(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "copy"; fixture(root)
            with patch.object(d, "connect", AsyncMock()) as connect:
                with self.assertRaises(a.RecoveryError): await s.create(settings(), MagicMock(), Path(temp) / "new")
                with self.assertRaises(a.RecoveryError): await s.restore(settings(), MagicMock(), root)
                with self.assertRaises(a.RecoveryError):
                    await s.restore(settings(suite_master_key=base64.b64encode(b"y" * 32).decode(),
                        minio_bucket_private="gensuite-restore-private", minio_bucket_public="gensuite-restore-public"),
                        MagicMock(), root, isolated=True, trusted=True, key_escrow=True)
                connect.assert_not_awaited()

    async def test_restore_verifies_metadata_before_object_writes_and_quarantines_before_success(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "copy"; _, manifest = fixture(root)
            connection = MagicMock(close=AsyncMock())
            backend = MemoryS3({"gensuite-restore-private": {}, "gensuite-restore-public": {}})
            target_settings = settings(postgres_db="gensuite_restore_fixture", minio_bucket_private="gensuite-restore-private", minio_bucket_public="gensuite-restore-public")
            for matching in (False, True):
                with self.subTest(matching=matching), patch.object(d, "connect", AsyncMock(return_value=connection)), \
                     patch.object(d, "require_empty_target", AsyncMock(return_value={"system_identifier": "222"})), \
                     patch.object(d, "prepare_role", AsyncMock()), patch.object(d, "pg_command", MagicMock()), \
                     patch.object(d, "metadata", AsyncMock(return_value=manifest["database"] if matching else {})), \
                     patch.object(d, "require_quiet", AsyncMock()), patch.object(d, "quarantine", AsyncMock(return_value={"jobs": "UPDATE 1"})) as quarantine, \
                     patch.object(d, "check_rls", AsyncMock(return_value=6)):
                    if matching:
                        report = await s.restore(target_settings, backend, root, isolated=True, trusted=True, key_escrow=True)
                        self.assertFalse(report["production_ready"]); self.assertFalse(report["app_role_login"])
                        self.assertFalse(report["row_data_verified"]); self.assertTrue(report["warnings"])
                        quarantine.assert_awaited_once()
                    else:
                        with self.assertRaises(a.RecoveryError): await s.restore(target_settings, backend, root, isolated=True, trusted=True, key_escrow=True)
                        self.assertFalse(backend.calls); quarantine.assert_not_awaited()

    async def test_v2_restore_rejects_row_content_mismatch_before_object_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "copy"; _, manifest = fixture(root)
            manifest["format"] = a.FORMAT; manifest["database"] = v2_database()
            update_manifest(root, manifest)
            connection = MagicMock(close=AsyncMock())
            connection.transaction.return_value = MagicMock(__aenter__=AsyncMock(), __aexit__=AsyncMock(return_value=False))
            backend = MemoryS3({"gensuite-restore-private": {}, "gensuite-restore-public": {}})
            target_settings = settings(postgres_db="gensuite_restore_fixture", minio_bucket_private="gensuite-restore-private", minio_bucket_public="gensuite-restore-public")
            different = v2_database(); different["row_integrity"]["tables"]["persons"]["sha256"] = "0" * 64
            with patch.object(d, "connect", AsyncMock(return_value=connection)), \
                 patch.object(d, "require_empty_target", AsyncMock(return_value={"system_identifier": "222"})), \
                 patch.object(d, "prepare_role", AsyncMock()), patch.object(d, "pg_command", MagicMock()), \
                 patch.object(d, "metadata", AsyncMock(return_value=different)), self.assertRaises(a.RecoveryError):
                await s.restore(target_settings, backend, root, isolated=True, trusted=True, key_escrow=True)
            self.assertFalse(backend.calls)


class RemoteTests(unittest.TestCase):
    def test_offsite_roundtrip_preserves_every_byte_and_publishes_manifest_last(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "copy"; _, manifest = fixture(root)
            backend = MemoryS3({"backup": {}})
            report = remote.upload(backend, "backup", root)
            uploads = [call for call in backend.calls if call[0] == "upload"]
            self.assertTrue(uploads[-1][2].endswith("/manifest.json"))
            downloaded = remote.download(backend, "backup", report["bundle_id"], Path(temp) / "download")
            self.assertTrue(downloaded["integrity_verified"])
            self.assertEqual(a.verify(Path(temp) / "download"), manifest)
            self.assertFalse(report["restoration_verified"])

    def test_offsite_readback_failure_never_uploads_completion_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "copy"; fixture(root)
            backend = MemoryS3({"backup": {}})
            with patch.object(remote, "read_hash", return_value={"sha256": "0" * 64, "size": 0}), self.assertRaises(a.RecoveryError):
                remote.upload(backend, "backup", root)
            self.assertFalse(any(key.endswith("/manifest.json") for key in backend.values["backup"]))

    def test_malformed_remote_bundle_and_shared_credentials_are_rejected(self):
        with self.assertRaises(ValueError): remote.prefix("../../another-object")
        values = settings()
        values.backup_s3_bucket = values.minio_bucket_private
        values.backup_s3_endpoint = "backup.invalid"
        values.backup_s3_access_key = "same"
        values.backup_s3_secret_key = "synthetic"
        values.minio_access_key = "same"
        with self.assertRaises(a.RecoveryError): remote.require_separate_store(values)


class ScheduledBackupTests(unittest.TestCase):
    def test_legacy_database_backup_streams_to_private_disk_and_labels_its_actual_scope(self):
        from app.tasks import maintenance_tasks as tasks
        backend = MagicMock()
        captured = {}
        def run(command, **kw):
            self.assertNotIn("capture_output", kw)
            self.assertEqual(kw["stdout"], d.subprocess.DEVNULL)
            path = Path(command[command.index("--file") + 1])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            path.write_bytes(b"PGDMPfixture")
            return SimpleNamespace(returncode=0)
        def upload(path, bucket, key, **kw): captured["bytes"] = Path(path).read_bytes()
        backend.upload_file.side_effect = upload
        with patch("subprocess.run", run), patch("app.core.storage.backup_s3", return_value=backend):
            key = tasks._pg_dump_to_s3()
        self.assertIn("database-only", key)
        self.assertEqual(captured["bytes"], b"PGDMPfixture")
        metadata = json.loads(backend.put_object.call_args.kwargs["Body"])
        self.assertFalse(metadata["objects_included"])
