"""Installer checks run in temporary directories; never read the deployment's .env."""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import install


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="gensuite-installer-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "config").mkdir()
        shutil.copyfile(install.ROOT / "config/.env.example", self.root / "config/.env.example")

    def configured(self, **overrides):
        values = {
            "FRONTEND_PUBLIC_URL": "https://tree.example.org", "FRONTEND_PORT": "8080",
            "SMTP_HOST": "smtp.example.org", "SMTP_FROM": "sender@example.org",
            "POSTGRES_PASSWORD": "test-admin-password", "APP_DB_PASSWORD": "test-app-password",
            "REDIS_PASSWORD": "test-redis-password", "JWT_SECRET": "test-jwt-placeholder",
            "MINIO_SECRET_KEY": "test-storage-password", "SUITE_MASTER_KEY": "test-master-key",
            **overrides,
        }
        template = (self.root / "config/.env.example").read_text()
        install.exclusive_write(self.root / "config/.env", install.render_env(template, values))

    def test_generates_private_config_without_printing_passwords(self):
        output = io.StringIO()
        with patch.object(install, "ask", side_effect=["smtp.example.org", "587", "sender@example.org", "never-print-this", "Gen Suite <sender@example.org>"]), redirect_stdout(output):
            install.make_config(self.root, "https://Tree.Example.org/", 8123, release="1.2.3")
        contents = (self.root / "config/.env").read_text()
        self.assertIn("FRONTEND_PUBLIC_URL='https://tree.example.org'", contents)
        self.assertIn("CORS_ORIGINS='[\"https://tree.example.org\"]'", contents)
        self.assertIn("ALLOW_FIRST_USER_ADMIN='false'", contents)
        self.assertIn("INVITATION_ONLY='true'", contents)
        self.assertIn("GEN_SUITE_VERSION='1.2.3'", contents)
        self.assertNotIn("change-me", contents)
        self.assertNotIn("never-print-this", output.getvalue())
        self.assertEqual(stat.S_IMODE((self.root / "config/.env").stat().st_mode), 0o600)
        self.assertIn("127.0.0.1:8123", (self.root / "config/Caddyfile.example").read_text())

    def test_existing_secrets_and_data_are_preserved(self):
        self.configured()
        before = (self.root / "config/.env").read_bytes()
        with self.assertRaises(install.InstallError):
            install.make_config(self.root, "https://tree.example.org", 8080)
        self.assertEqual((self.root / "config/.env").read_bytes(), before)

    def test_refuses_new_credentials_when_data_exist(self):
        (self.root / "data").mkdir()
        (self.root / "data/restore-needed").touch()
        with self.assertRaisesRegex(install.InstallError, "configuración original"):
            install.make_config(self.root, "https://tree.example.org", 8080)
        self.assertFalse((self.root / "config/.env").exists())
        self.assertTrue((self.root / "data/restore-needed").exists())

    def test_existing_config_reused_in_configure_only(self):
        self.configured()
        before = (self.root / "config/.env").read_bytes()
        with patch.object(install, "ask", side_effect=AssertionError("must not prompt")), redirect_stdout(io.StringIO()):
            self.assertEqual(install.main(["--configure-only"], root=self.root), 0)
            self.assertEqual(install.main(["--configure-only"], root=self.root), 0)
        self.assertEqual((self.root / "config/.env").read_bytes(), before)

    def test_failed_preflight_does_not_write_configuration(self):
        with patch.object(install, "preflight", side_effect=install.InstallError("Docker no disponible")), \
                patch.object(install, "ask", side_effect=AssertionError("must not prompt")), redirect_stderr(io.StringIO()):
            self.assertEqual(install.main([], root=self.root), 1)
        self.assertFalse((self.root / "config/.env").exists())
        self.assertFalse((self.root / "config/installer.json").exists())

    def test_status_is_read_only_and_does_not_start_services(self):
        self.configured()
        before = (self.root / "config/.env").read_bytes()
        with patch.object(install, "run_command", return_value="") as run, \
                patch.object(install, "ask", side_effect=AssertionError("must not prompt")):
            self.assertEqual(install.main(["--status"], root=self.root), 0)
        self.assertEqual(run.call_args.args[0][-2:], ["ps", "--all"])
        self.assertEqual((self.root / "config/.env").read_bytes(), before)
        self.assertFalse((self.root / "config/installer.json").exists())

    def test_rejects_shell_and_proxy_injection_and_mutable_release(self):
        for url in ("http://tree.example.org", "https://example.org/path", "https://evil;whoami", "https://user:secret@example.org", "https://example.org\n}"):
            with self.subTest(url=url), self.assertRaises(install.InstallError):
                install.validate_url(url)
        for version in ("latest", "main", "0.2", "1.2.3; whoami"):
            with self.subTest(version=version), self.assertRaises(install.InstallError):
                install.validate_release(version)
        self.assertEqual(install.validate_release("v0.2.0-rc.1"), "0.2.0-rc.1")

    def test_environment_does_not_override_generated_secrets(self):
        with patch.dict(os.environ, {"JWT_SECRET": "injected", "COMPOSE_FILE": "elsewhere", "DOCKER_HOST": "unix:///custom/socket"}):
            environment = install.command_environment(self.root)
        self.assertNotIn("JWT_SECRET", environment)
        self.assertNotIn("COMPOSE_FILE", environment)
        self.assertEqual(environment["DOCKER_HOST"], "unix:///custom/socket")

    def test_no_new_mode_selected_on_resume(self):
        install.exclusive_write(self.root / "config/installer.json", '{"release": "0.2.0"}')
        with self.assertRaises(install.InstallError):
            install.select_mode(self.root, argparse.Namespace(release=None, build=True))
        self.assertEqual(install.select_mode(self.root, argparse.Namespace(release=None, build=False)), "0.2.0")

    def fake_deployment(self, *, fail=None, bundled=True, arguments=None, pdfa=False):
        self.configured()
        calls = []

        def command(argv, **kwargs):
            self.assertNotIn("test-admin-password", " ".join(argv))
            calls.append(argv)
            if fail and fail(argv):
                raise install.InstallError("simulated failure")
            if argv[-3:] == ["config", "--format", "json"]:
                services = {"gen-suite-backend": {"environment": {
                    "FRONTEND_PUBLIC_URL": "https://tree.example.org", "ENVIRONMENT": "production"}}}
                if bundled:
                    services["gen-suite-minio"] = {}
                if pdfa:
                    services["gen-suite-pdfa"] = {}
                return json.dumps({"services": services})
            return ""

        with patch.object(install, "preflight"), patch.object(install, "run_command", side_effect=command), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            result = install.main(arguments or [], root=self.root)
        return result, calls

    def test_source_build_orders_storage_migrations_admin_before_web(self):
        result, calls = self.fake_deployment()
        self.assertEqual(result, 0)
        build = next(i for i, call in enumerate(calls) if "build" in call)
        validate = next(i for i, call in enumerate(calls) if "--validate" in call)
        storage = next(i for i, call in enumerate(calls) if "up" in call and "gen-suite-minio" in call)
        migrate = next(i for i, call in enumerate(calls) if call[-1] == "gen-suite-migrate")
        admin = next(i for i, call in enumerate(calls) if call[-1] == "app.bootstrap_admin")
        web = max(i for i, call in enumerate(calls) if "up" in call)
        self.assertLess(build, validate)
        self.assertLess(validate, storage)
        self.assertLess(storage, migrate)
        self.assertLess(migrate, admin)
        self.assertLess(admin, web)
        self.assertTrue(all("--wait-timeout" in call for call in calls if "up" in call))
        self.assertFalse(any("down" in call for call in calls))

    def test_stops_after_migration_failure_without_bootstrapping_or_starting_web(self):
        result, calls = self.fake_deployment(fail=lambda call: call[-1] == "gen-suite-migrate")
        self.assertEqual(result, 1)
        self.assertFalse(any(call[-1] == "app.bootstrap_admin" for call in calls))
        self.assertEqual(sum("up" in call for call in calls), 1)

    def test_external_storage_and_release_do_not_start_minio_or_build(self):
        result, calls = self.fake_deployment(bundled=False, arguments=["--release", "0.2.0"])
        self.assertEqual(result, 0)
        self.assertTrue(any("pull" in call for call in calls))
        self.assertFalse(any("build" in call or "gen-suite-minio" in call for call in calls))

    def test_optional_pdfa_is_built_only_when_selected(self):
        for selected in (False, True):
            (self.root / "config/.env").unlink(missing_ok=True)
            result, calls = self.fake_deployment(pdfa=selected)
            self.assertEqual(result, 0)
            build = next(call for call in calls if "build" in call)
            self.assertEqual("gen-suite-pdfa" in build, selected)

    @unittest.skipUnless(shutil.which("docker"), "Docker CLI not installed")
    def test_pdfa_profile_is_optional_private_non_root_and_bounded(self):
        for filename in ("compose.yaml", "compose.release.yaml"):
            shutil.copyfile(install.ROOT / filename, self.root / filename)
            for selected in (False, True):
                (self.root / "config/.env").unlink(missing_ok=True)
                self.configured(COMPOSE_PROFILES="bundled-storage,pdfa" if selected else "bundled-storage")
                services = self.compose_model(filename)["services"]
                self.assertEqual("gen-suite-pdfa" in services, selected)
                if not selected:
                    continue
                worker = services["gen-suite-pdfa"]
                self.assertNotIn("ports", worker); self.assertNotIn("volumes", worker)
                self.assertEqual(worker["profiles"], ["pdfa"])
                self.assertTrue(worker["read_only"])
                self.assertEqual(worker["user"], "10001:10001")
                self.assertEqual(worker["cap_drop"], ["ALL"])
                self.assertEqual(worker["pids_limit"], 256)
                self.assertIn("size=4g", worker["tmpfs"][0])
                self.assertEqual(worker["command"][-1], "app.tasks.worker.PdfaWorkerSettings")
                self.assertNotEqual(worker["image"], services["gen-suite-backend"]["image"])
                if filename == "compose.yaml":
                    self.assertEqual(worker["build"]["target"], "pdfa")
                else:
                    self.assertNotIn("build", worker)

    def test_failed_command_never_echoes_captured_config_secrets(self):
        with patch.object(install.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "secret", "secret")):
            with self.assertRaises(install.InstallError) as raised:
                install.run_command(["docker"], root=self.root, env={}, capture=True, label="validación")
        self.assertNotIn("secret", str(raised.exception))

    @unittest.skipUnless(shutil.which("docker"), "Docker CLI not installed")
    def test_compose_parses_generated_passwords_literally_in_both_modes(self):
        # No daemon or real config is used. Include dollar, backtick and quote cases
        # that often break shell-based installers. Never print the resolved model.
        password = "a$NOT_AN_ENV${NOPE} #quote'`command`\\tail"
        passwords = (password, "ends-in-backslash\\", "double\\\\backslash", "backslash\\'quote")
        for filename in ("compose.yaml", "compose.release.yaml"):
            shutil.copyfile(install.ROOT / filename, self.root / filename)
            for password in passwords:
                with self.subTest(filename=filename, password=password):
                    (self.root / "config/.env").unlink(missing_ok=True)
                    self.configured(SMTP_PASSWORD=password)
                    services = self.compose_model(filename)["services"]
                    # The canonical Compose model escapes literal dollars for reuse as YAML;
                    # Docker receives a single dollar, not a shell-expanded value.
                    self.assertEqual(services["gen-suite-backend"]["environment"]["SMTP_PASSWORD"].replace("$$", "$"), password)
                    self.assertEqual(services["gen-suite-worker"]["image"], services["gen-suite-backend"]["image"])
                    self.assertEqual(services["gen-suite-migrate"]["image"], services["gen-suite-backend"]["image"])
                    self.assertIn("http://127.0.0.1/readyz", services["gen-suite-frontend"]["healthcheck"]["test"])

    def compose_model(self, filename):
        result = subprocess.run(["docker", "compose", "--env-file", str(self.root / "config/.env"), "-f", str(self.root / filename),
                                 "config", "--format", "json"], capture_output=True, text=True,
                                env=install.command_environment(self.root), timeout=30)
        self.assertEqual(result.returncode, 0, "Compose must accept the generated configuration")
        return json.loads(result.stdout)

    @unittest.skipUnless(shutil.which("docker"), "Docker CLI not installed")
    def test_recovery_overlay_is_opt_in_has_no_source_volume_and_mounts_copies_read_only(self):
        self.configured()
        for filename in ("compose.yaml", "compose.recovery.yaml"):
            shutil.copyfile(install.ROOT / filename, self.root / filename)
        environment = install.command_environment(self.root)
        environment["GEN_SUITE_RECOVERY_DIR"] = str(self.root / "archives")
        result = subprocess.run(["docker", "compose", "--env-file", str(self.root / "config/.env"),
            "-f", str(self.root / "compose.yaml"), "-f", str(self.root / "compose.recovery.yaml"),
            "--profile", "recovery", "config", "--format", "json"], capture_output=True, text=True, env=environment, timeout=30)
        self.assertEqual(result.returncode, 0, "Recovery overlay must parse without daemon or deployment configuration")
        model = json.loads(result.stdout)["services"]["gen-suite-recovery"]
        self.assertEqual(model["profiles"], ["recovery"])
        self.assertNotIn("ports", model); self.assertNotIn("depends_on", model)
        self.assertEqual(len(model["volumes"]), 1)
        volume = model["volumes"][0]
        self.assertEqual(volume["target"], "/recovery")
        self.assertTrue(volume["read_only"])
        self.assertFalse(volume["bind"].get("create_host_path", False))

    @unittest.skipUnless(shutil.which("docker"), "Docker CLI not installed")
    def test_native_compose_has_builds_and_startup_dependencies_without_installer_state(self):
        self.configured()
        self.assertFalse((self.root / "config/installer.json").exists())
        for filename in ("compose.yaml", "compose.release.yaml"):
            shutil.copyfile(install.ROOT / filename, self.root / filename)
            with self.subTest(filename=filename):
                services = self.compose_model(filename)["services"]
                for name in ("gen-suite-backend", "gen-suite-worker", "gen-suite-migrate"):
                    if filename == "compose.yaml":
                        self.assertEqual(services[name]["build"], services["gen-suite-backend"]["build"])
                        self.assertEqual(services[name]["build"]["dockerfile"], "docker/backend.Dockerfile")
                    else:
                        self.assertNotIn("build", services[name])
                for name in ("gen-suite-backend", "gen-suite-worker"):
                    dependencies = services[name]["depends_on"]
                    self.assertEqual(dependencies["gen-suite-migrate"]["condition"], "service_completed_successfully")
                    self.assertEqual(dependencies["gen-suite-minio"]["condition"], "service_healthy")
                    self.assertFalse(dependencies["gen-suite-minio"]["required"])
                self.assertEqual(services["gen-suite-migrate"]["depends_on"]["gen-suite-db"]["condition"], "service_healthy")

    @unittest.skipUnless(shutil.which("docker"), "Docker CLI not installed")
    def test_compose_omits_bundled_storage_when_profile_is_disabled(self):
        self.configured(COMPOSE_PROFILES="", MINIO_ENDPOINT="s3.example.org")
        for filename in ("compose.yaml", "compose.release.yaml"):
            shutil.copyfile(install.ROOT / filename, self.root / filename)
            with self.subTest(filename=filename):
                self.assertNotIn("gen-suite-minio", self.compose_model(filename)["services"])

    @unittest.skipUnless(shutil.which("docker"), "Docker CLI not installed")
    def test_optional_importer_has_read_only_sources_and_no_database_or_s3_credentials(self):
        self.configured()
        for filename in ("compose.yaml", "compose.import.yaml"):
            shutil.copyfile(install.ROOT / filename, self.root / filename)
        original = self.root / "originals"
        original.mkdir()
        environment = {**install.command_environment(self.root), "GEN_SUITE_IMPORT_PATH": str(original),
                       "GEN_SUITE_IMPORT_TOKEN": "test-only-token"}
        result = subprocess.run(["docker", "compose", "--env-file", str(self.root / "config/.env"),
            "-f", str(self.root / "compose.yaml"), "-f", str(self.root / "compose.import.yaml"),
            "--profile", "import", "config", "--format", "json"], capture_output=True, text=True,
            env=environment, timeout=30)
        self.assertEqual(result.returncode, 0, "El overlay opcional debe ser válido")
        service = json.loads(result.stdout)["services"]["gen-suite-import"]
        self.assertEqual(set(service["environment"]), {"GEN_SUITE_IMPORT_TOKEN"})
        self.assertTrue(service["volumes"][0]["read_only"])
        # Compose versions may omit an explicit false from the normalized model.
        self.assertFalse(service["volumes"][0]["bind"].get("create_host_path", False))
        self.assertNotIn("--upload", service["command"])


if __name__ == "__main__":
    unittest.main()
