"""Re-running the installer updates only what changed, and new steps behave."""

from __future__ import annotations

import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ctl import install
from ctl.bootstrap import stamps


def _checkout(tmp: str) -> Path:
    root = Path(tmp)
    (root / "ctl").mkdir()
    (root / "ctl" / "requirements.txt").write_text("fastapi==1\n", encoding="utf-8")
    (root / "ctl" / "app.py").write_text("app = 1\n", encoding="utf-8")
    (root / "dashboard" / "src").mkdir(parents=True)
    (root / "dashboard" / "src" / "main.tsx").write_text("console.log(1)\n", encoding="utf-8")
    (root / "dashboard" / "package-lock.json").write_text("{}\n", encoding="utf-8")
    (root / "dashboard" / "dist").mkdir()
    (root / "dashboard" / "dist" / "index.html").write_text("<html></html>", encoding="utf-8")
    return root


class StampTests(unittest.TestCase):
    def test_requirements_digest_matches_sha256sum_used_by_install_sh(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _checkout(tmp)
            expected = subprocess.run(
                ["sha256sum", str(root / "ctl" / "requirements.txt")], capture_output=True, text=True, check=True
            ).stdout.split()[0]
            self.assertEqual(stamps.requirements_digest(root), expected)

    def test_digests_change_only_with_their_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _checkout(tmp)
            dashboard, control = stamps.dashboard_digest(root), stamps.control_plane_digest(root)
            (root / "ctl" / "app.py").write_text("app = 2\n", encoding="utf-8")
            self.assertEqual(stamps.dashboard_digest(root), dashboard)
            self.assertNotEqual(stamps.control_plane_digest(root), control)
            (root / "dashboard" / "src" / "main.tsx").write_text("console.log(2)\n", encoding="utf-8")
            self.assertNotEqual(stamps.dashboard_digest(root), dashboard)

    def test_generated_files_do_not_affect_digests(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _checkout(tmp)
            before = stamps.control_plane_digest(root)
            (root / "ctl" / "__pycache__").mkdir()
            (root / "ctl" / "__pycache__" / "app.cpython-312.pyc").write_bytes(b"\0")
            self.assertEqual(stamps.control_plane_digest(root), before)

    def test_read_accepts_sha256sum_output_and_missing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stamp"
            self.assertEqual(stamps.read(path), "")
            path.write_text("abc  ctl/requirements.txt\n", encoding="utf-8")
            self.assertEqual(stamps.read(path), "abc")


class ChangeDetectionTests(unittest.TestCase):
    def test_changed_requirements_reinstall_packages(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _checkout(tmp)
            (root / ".venv" / "bin").mkdir(parents=True)
            (root / ".venv" / "bin" / "python").touch()
            stamps.write(root / stamps.REQUIREMENTS_STAMP, hashlib.sha256(b"old").hexdigest())
            check = install._pip_check(root)
            self.assertEqual(check["state"], "outdated")
            self.assertEqual(install.fix_for_state("pip_deps", "outdated"), "pip_install")

    def test_changed_dashboard_sources_rebuild(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _checkout(tmp)
            stamps.write(root / stamps.BUILD_STAMP, stamps.dashboard_digest(root))
            self.assertEqual(install._build_check(root)["state"], "ready")
            (root / "dashboard" / "src" / "main.tsx").write_text("changed\n", encoding="utf-8")
            self.assertEqual(install._build_check(root)["state"], "stale")


class NvidiaTests(unittest.TestCase):
    def test_no_gpu_needs_nothing(self):
        with patch.object(install, "_nvidia_gpu_ready", return_value=False):
            check = install._nvidia_toolkit_check({})
        self.assertEqual(check["state"], "not_needed")
        self.assertEqual(install.fix_for_state("nvidia_toolkit", "not_needed"), "skip")

    def test_gpu_without_toolkit_installs_it(self):
        with (
            patch.object(install, "_nvidia_gpu_ready", return_value=True),
            patch.object(install, "_dpkg_present", return_value=False),
        ):
            self.assertEqual(install._nvidia_toolkit_check({})["state"], "missing")

    def test_install_configures_docker_and_never_touches_drivers(self):
        commands: list[list[str]] = []

        def privileged(argv, log, **_kwargs):
            commands.append(argv)
            return {"ok": True}

        ok = {"ok": True, "log": []}
        with (
            patch.object(install.actions, "fetch_url", return_value=ok),
            patch.object(install.actions, "write_root_bytes", return_value=ok),
            patch.object(install.actions, "write_root_file", return_value=ok) as write_file,
            patch.object(install.actions, "apt_update", return_value=ok),
            patch.object(install.actions, "apt_install", return_value=ok) as apt_install,
            patch.object(install.actions.privilege, "run_privileged", side_effect=privileged),
            patch.object(Path, "read_bytes", return_value=b"key"),
        ):
            result = install.fix_nvidia_toolkit({}, {"log_fn": lambda _s: lambda _l: None})
        self.assertTrue(result["ok"])
        apt_install.assert_called_once_with(["nvidia-container-toolkit"], unittest.mock.ANY)
        self.assertIn("signed-by=/etc/apt/keyrings/nvidia-container-toolkit.asc", write_file.call_args.args[1])
        self.assertIn(["nvidia-ctk", "runtime", "configure", "--runtime=docker"], commands)
        self.assertFalse(any("driver" in " ".join(command) for command in commands))


class CoreImageTests(unittest.TestCase):
    def test_core_images_cover_the_always_on_platform_only(self):
        names = {name for name, _image in install.core_images()}
        self.assertTrue({"Authentik", "Vaultwarden", "Ollama", "LiteLLM", "FreeLLMAPI", "LobeChat"} <= names)
        self.assertNotIn("Immich", names)

    def test_check_lists_what_is_still_downloading(self):
        with patch.object(install, "_image_present", side_effect=lambda image: "ollama" not in image):
            check = install._core_images_check({"root": install.ROOT})
        self.assertEqual(check["state"], "missing")
        self.assertIn("Ollama", check["detail"])

    def test_failed_downloads_are_named(self):
        ctx = {"root": install.ROOT, "log_fn": lambda _s: lambda _l: None}
        with (
            patch.object(install, "_missing_images", return_value=[("Ollama", "ollama/ollama:1")]),
            patch.object(install.actions, "docker_cmd", return_value=(1, "network down")),
        ):
            result = install.fix_core_images({}, ctx)
        self.assertFalse(result["ok"])
        self.assertIn("ollama/ollama:1", result["error"])


class PhaseTests(unittest.TestCase):
    def test_every_step_belongs_to_exactly_one_phase_in_run_order(self):
        flat = [step_id for _title, ids in install.PHASES for step_id in ids]
        self.assertEqual(flat, [step["id"] for step in install.STEPS])


class TailscaleRerunTests(unittest.TestCase):
    ERROR = (
        "Error: changing settings via 'tailscale up' requires mentioning all\n"
        "non-default flags. To proceed, either re-run your command with --reset or\n"
        "use the command below to explicitly mention the current value of\n"
        "all non-default settings:\n\n"
        "\ttailscale up --hostname=mu3lab --timeout=2m0s --operator=me --exit-node=100.64.0.1\n"
    )

    def test_suggested_command_keeps_the_users_settings(self):
        self.assertEqual(
            install._tailscale_suggested_up(self.ERROR),
            ["tailscale", "up", "--hostname=mu3lab", "--timeout=2m0s", "--operator=me", "--exit-node=100.64.0.1"],
        )

    def test_no_suggestion_for_other_output(self):
        self.assertEqual(install._tailscale_suggested_up("To authenticate, visit: https://login.tailscale.com/a/x"), [])

    def test_join_restates_the_operator_and_retries_with_the_suggestion(self):
        calls: list[list[str]] = []

        def privileged(argv, log, **_kwargs):
            calls.append(argv)
            if len(calls) == 1:
                return {"ok": False, "output": self.ERROR}
            return {"ok": True, "output": ""}

        ctx = {"log_fn": lambda _s: lambda _l: None, "stopped": lambda: False}
        with (
            patch.object(install.privilege, "run_privileged", side_effect=privileged),
            patch.object(install.getpass, "getuser", return_value="me"),
            patch.object(install, "_tailscale_auth_url", return_value=""),
        ):
            result = install.fix_tailscale_join({}, ctx)
            ctx["_tailscale_join_state"]["thread"].join(2)
            result = install.fix_tailscale_join({}, ctx)
        self.assertIn("--operator=me", calls[0])
        self.assertIn("--exit-node=100.64.0.1", calls[1])
        self.assertTrue(result["ok"])


class AuthentikSetupScanTests(unittest.TestCase):
    def test_admin_account_is_not_reported_done_before_authentik_runs(self):
        with patch.object(install, "_authentik_check", return_value={"status": "missing"}):
            check = install.check_authentik_setup({})
        self.assertEqual(check["state"], "needs_user")
        self.assertIn("not running yet", check["detail"])


class ExpiredSessionTests(unittest.TestCase):
    def test_run_stops_before_any_step_without_administrator_access(self):
        job = install.new_job()
        events: list[dict] = []
        with patch.object(install.privilege, "has_fresh_sudo", return_value=False):
            install.run_job(job, {"emit": events.append, "stopped": lambda: False})
        self.assertEqual(job["status"], "failed")
        self.assertIn("./install.sh", job["error"])
        self.assertTrue(all(step["status"] == "pending" for step in job["steps"]))


class HostBaseTests(unittest.TestCase):
    def test_host_packages_never_delete_package_sources(self):
        ok = {"ok": True, "log": []}
        with (
            patch.object(install, "_dpkg_present", return_value=False),
            patch.object(install, "_repair_managed_apt_keys", return_value={"ok": True}),
            patch.object(install.actions, "remove_root_file") as remove,
            patch.object(install.actions, "apt_update", return_value=ok),
            patch.object(install.actions, "apt_install", return_value=ok),
        ):
            install.fix_host_base({}, {"log_fn": lambda _s: lambda _l: None})
        remove.assert_not_called()


if __name__ == "__main__":
    unittest.main()
