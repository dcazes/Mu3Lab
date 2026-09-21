"""Cross-layer contracts for the dashboard-driven MVP completion work."""

from __future__ import annotations

import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ctl import workflow_secrets
from ctl.control_state import ControlState
from ctl.install_batches import InstallBatchStore
from ctl.jobs import JobStore
from ctl.registry import load
from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env
from ctl.service_ops import _materialize, reset_failed_application


class WorkflowSecretTests(unittest.TestCase):
    def test_generated_password_meets_fixed_contract(self):
        password = workflow_secrets.generate_password()
        self.assertEqual(len(password), 20)
        self.assertTrue(any(char.isupper() for char in password))
        self.assertTrue(any(char.islower() for char in password))
        self.assertTrue(any(char.isdigit() for char in password))
        self.assertTrue(any(char in "-_!@#%" for char in password))
        self.assertFalse(set(password).intersection("0O1lI'\"`$\\: \n\r"))

    def test_handoff_is_encrypted_owner_scoped_and_deletable(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            result = workflow_secrets.create_handoff(
                service_id="paperless-ngx", job_id="job", owner_uid="owner-a",
                username="operator", email="operator@example.test",
                password="NeverPlaintext123!", login_url="https://private.example", paths=paths)
            encrypted = (paths.runtime / "workflow-secrets.enc").read_bytes()
            self.assertNotIn(b"NeverPlaintext123!", encrypted)
            self.assertIsNone(workflow_secrets.reveal(result["id"], "owner-b", paths))
            self.assertEqual(workflow_secrets.reveal(result["id"], "owner-a", paths)["password"],
                             "NeverPlaintext123!")
            self.assertTrue(workflow_secrets.delete(result["id"], "owner-a", paths))
            self.assertIsNone(workflow_secrets.reveal(result["id"], "owner-a", paths))
            self.assertEqual(stat.S_IMODE((paths.runtime / "workflow-secrets.key").stat().st_mode), 0o600)


class RegistryAccountTests(unittest.TestCase):
    def test_managed_account_fields_are_not_browser_visible(self):
        services = {service.id: service.public() for service in load().services}
        self.assertEqual(services["paperless-ngx"]["account"]["mode"], "environment_bootstrap")
        self.assertEqual(services["adventurelog"]["configuration"], [])
        self.assertEqual(services["actual-budget"]["account"]["mode"], "oidc_first_login")
        self.assertEqual(services["surfsense"]["account"]["mode"], "local_account_manual")

    def test_surfsense_materialization_includes_internal_embedding_contract(self):
        with tempfile.TemporaryDirectory() as source_tmp, tempfile.TemporaryDirectory() as runtime_tmp:
            source = Path(source_tmp)
            project = source / "apps" / "surfsense"
            project.mkdir(parents=True)
            (project / "docker-compose.yml").write_text("services: {app: {image: example:test}}\n",
                                                         encoding="utf-8")
            paths = RuntimePaths(Path(runtime_tmp))
            with patch("ctl.service_ops.RuntimePaths", return_value=paths), \
                 patch("ctl.service_state.tailnet_dns_name", return_value="mu3lab.example.ts.net"):
                target = _materialize(load().get("surfsense"), source)
            values = read_runtime_env(target / ".env")
            self.assertEqual(values["EMBEDDING_MODEL"], "litellm://ollama/nomic-embed-text")
            self.assertEqual(values["EMBEDDING_BASE_URL"], "http://ollama:11434")
            self.assertEqual(values["SANDBOX_ENABLED"], "FALSE")


class BatchPersistenceTests(unittest.TestCase):
    def test_batch_advances_one_child_at_a_time_and_survives_reopen(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            paths.runtime.mkdir(parents=True)
            control = ControlState(paths.runtime / "control-plane.sqlite3")
            jobs = JobStore(paths.runtime / "control-plane.sqlite3")
            batches = InstallBatchStore(paths.runtime / "control-plane.sqlite3")
            identity = {"owner_uid": "uid", "email": "operator@example.test",
                        "username": "operator", "display_name": "Operator"}
            with patch("ctl.install_batches.workflow_secrets.save_job_identity"), \
                 patch("ctl.install_batches.workflow_secrets.job_identity", return_value=identity):
                batch = batches.create(load(), ["paperless-ngx", "adventurelog"],
                                       actor="operator", owner_uid="uid", identity=identity,
                                       idempotency_key="request-1", jobs=jobs, control=control)
                self.assertEqual([item["state"] for item in batch["items"]], ["queued", "pending"])
                first = jobs.get(batch["items"][0]["job_id"])
                jobs.transition(first["id"], "running", actor="worker")
                jobs.transition(first["id"], "succeeded", actor="worker")
                batches.advance_for_job(first["id"], jobs)
                batches.advance_for_job(first["id"], jobs)
                reopened = InstallBatchStore(paths.runtime / "control-plane.sqlite3").get(batch["id"])
            self.assertEqual([item["state"] for item in reopened["items"]], ["succeeded", "queued"])
            self.assertEqual(reopened["state"], "running")
            self.assertEqual(len([job for job in jobs.jobs() if job["state"] == "queued"]), 1)

    def test_duplicate_and_empty_batch_selection_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            control = ControlState(Path(tmp) / "control.sqlite3")
            batches = InstallBatchStore(Path(tmp) / "control.sqlite3")
            with self.assertRaises(ValueError):
                batches.plan(load(), [], control)
            with self.assertRaises(ValueError):
                batches.plan(load(), ["paperless-ngx", "paperless-ngx"], control)

    def test_terminal_batch_reset_clears_batch_without_queuing_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            paths.runtime.mkdir(parents=True)
            control = ControlState(paths.runtime / "control-plane.sqlite3")
            jobs = JobStore(paths.runtime / "control-plane.sqlite3")
            batches = InstallBatchStore(paths.runtime / "control-plane.sqlite3")
            identity = {"owner_uid": "uid", "email": "operator@example.test",
                        "username": "operator", "display_name": "Operator"}
            with patch("ctl.install_batches.workflow_secrets.save_job_identity"), \
                 patch("ctl.install_batches.workflow_secrets.job_identity", return_value=identity):
                batch = batches.create(load(), ["paperless-ngx"],
                                       actor="operator", owner_uid="uid", identity=identity,
                                       idempotency_key="request-reset", jobs=jobs, control=control)
                failed_job = jobs.get(batch["items"][0]["job_id"])
                jobs.transition(failed_job["id"], "failed", actor="worker",
                                detail="temporary start failure", error_code="compose_failed")
                batches.advance_for_job(failed_job["id"], jobs)
                reset = batches.reset(batch["id"], jobs)
                latest = batches.latest("uid")
            self.assertEqual(reset["state"], "reset")
            self.assertEqual(reset["items"][0]["state"], "reset")
            self.assertEqual(reset["items"][0]["job_id"], "")
            self.assertIsNone(latest)
            self.assertEqual(len([job for job in jobs.jobs() if job["state"] == "queued"]), 0)

    def test_worker_owned_reset_is_durable_and_does_not_run_docker_in_the_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp)); paths.runtime.mkdir(parents=True)
            control = ControlState(paths.runtime / "control-plane.sqlite3")
            jobs = JobStore(paths.runtime / "control-plane.sqlite3")
            batches = InstallBatchStore(paths.runtime / "control-plane.sqlite3")
            identity = {"owner_uid": "uid", "email": "operator@example.test",
                        "username": "operator", "display_name": "Operator"}
            with patch("ctl.install_batches.workflow_secrets.save_job_identity"), \
                 patch("ctl.install_batches.workflow_secrets.job_identity", return_value=identity):
                batch = batches.create(load(), ["paperless-ngx"], actor="operator", owner_uid="uid",
                                       identity=identity, idempotency_key="async-reset", jobs=jobs, control=control)
                child = jobs.get(batch["items"][0]["job_id"])
                jobs.transition(child["id"], "failed", actor="worker", detail="failed")
                batches.advance_for_job(child["id"], jobs)
                pending = batches.begin_reset(batch["id"], jobs)
                self.assertEqual(pending["state"], "resetting")
                self.assertEqual(pending["items"][0]["state"], "resetting")
                reset_job = jobs.get(pending["items"][0]["job_id"])
                self.assertEqual(reset_job["action"], "reset")
                self.assertEqual(reset_job["state"], "queued")
                jobs.transition(reset_job["id"], "running", actor="worker")
                jobs.transition(reset_job["id"], "succeeded", actor="worker")
                batches.advance_for_job(reset_job["id"], jobs)
            self.assertEqual(batches.get(batch["id"])["state"], "reset")

    def test_unrelated_batch_items_continue_after_a_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp)); paths.runtime.mkdir(parents=True)
            control = ControlState(paths.runtime / "control-plane.sqlite3")
            jobs = JobStore(paths.runtime / "control-plane.sqlite3")
            batches = InstallBatchStore(paths.runtime / "control-plane.sqlite3")
            identity = {"owner_uid": "uid", "email": "operator@example.test",
                        "username": "operator", "display_name": "Operator"}
            with patch("ctl.install_batches.workflow_secrets.save_job_identity"), \
                 patch("ctl.install_batches.workflow_secrets.job_identity", return_value=identity):
                batch = batches.create(load(), ["adventurelog", "paperless-ngx"], actor="operator", owner_uid="uid",
                                       identity=identity, idempotency_key="continue-independent", jobs=jobs, control=control)
                first = jobs.get(batch["items"][0]["job_id"])
                jobs.transition(first["id"], "failed", actor="worker", detail="failed")
                batches.advance_for_job(first["id"], jobs)
            after = batches.get(batch["id"])
            self.assertEqual(after["items"][0]["state"], "failed")
            self.assertEqual(after["items"][1]["state"], "queued")
            self.assertEqual(after["state"], "running")


class FailedApplicationResetTests(unittest.TestCase):
    def test_cleanup_removes_transient_files_but_preserves_env_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "nextcloud"
            project.mkdir()
            (project / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
            (project / "docker-compose.digest.yml").write_text("temporary\n", encoding="utf-8")
            (project / "docker-compose.bootstrap.yml").write_text("temporary\n", encoding="utf-8")
            (project / ".env").write_text(
                "NEXTCLOUD_DB_PASSWORD=keep-me\n"
                "MU3LAB_BOOTSTRAP_USERNAME=remove-me\n"
                "MU3LAB_BOOTSTRAP_PASSWORD=remove-me\n", encoding="utf-8")
            service = load().get("nextcloud")
            with patch("ctl.service_ops.project_path", return_value=project), \
                 patch("ctl.service_ops.actions.compose_down", return_value=(0, "removed")) as down:
                ok, detail = reset_failed_application(service, Path(tmp), lambda _line: None)
            self.assertTrue(ok)
            self.assertIn("persistent data preserved", detail)
            down.assert_called_once()
            self.assertFalse((project / "docker-compose.digest.yml").exists())
            self.assertFalse((project / "docker-compose.bootstrap.yml").exists())
            env = read_runtime_env(project / ".env")
            self.assertEqual(env["NEXTCLOUD_DB_PASSWORD"], "keep-me")
            self.assertNotIn("MU3LAB_BOOTSTRAP_USERNAME", env)
            self.assertNotIn("MU3LAB_BOOTSTRAP_PASSWORD", env)


if __name__ == "__main__":
    unittest.main()
