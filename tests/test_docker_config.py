"""Docker needs a larger address pool than its default, or a full catalog cannot install."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ctl import docker_config, install, service_ops

GPU = {"runtimes": {"nvidia": {"args": [], "path": "nvidia-container-runtime"}}}


class PoolTests(unittest.TestCase):
    def test_default_docker_is_not_enough(self):
        self.assertFalse(docker_config.pools_sufficient(""))
        self.assertFalse(docker_config.pools_sufficient(json.dumps(GPU)))

    def test_added_pool_keeps_other_settings_and_is_enough(self):
        updated = docker_config.with_pools(json.dumps(GPU))
        self.assertEqual(json.loads(updated)["runtimes"], GPU["runtimes"])
        self.assertTrue(docker_config.pools_sufficient(updated))
        self.assertEqual(docker_config.with_pools(updated), updated)

    def test_a_large_pool_the_owner_chose_is_left_alone(self):
        own = json.dumps({"default-address-pools": [{"base": "10.50.0.0/16", "size": 24}]})
        self.assertTrue(docker_config.pools_sufficient(own))

    def test_a_small_pool_is_replaced(self):
        small = json.dumps({"default-address-pools": [{"base": "10.50.0.0/24", "size": 28}]})
        self.assertFalse(docker_config.pools_sufficient(small))

    def test_broken_settings_are_never_overwritten(self):
        with self.assertRaises(ValueError):
            docker_config.with_pools("{not json")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "daemon.json"
            path.write_text("{not json", encoding="utf-8")
            with (
                patch.object(docker_config, "DAEMON_JSON", str(path)),
                patch("ctl.install.actions.write_root_file") as write,
            ):
                result = install.fix_address_pools({}, {"log_fn": lambda _s: lambda _l: None})
            self.assertFalse(result["ok"])
            write.assert_not_called()


class StepTests(unittest.TestCase):
    def test_check_then_fix_writes_and_restarts_docker(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "daemon.json"
            path.write_text(json.dumps(GPU), encoding="utf-8")
            ctx = {"log_fn": lambda _s: lambda _l: None}
            written: list[str] = []
            with (
                patch.object(docker_config, "DAEMON_JSON", str(path)),
                patch("ctl.install.actions.privilege.run_privileged", return_value={"ok": True}),
                patch(
                    "ctl.install.actions.write_root_file",
                    side_effect=lambda p, c, log: written.append(c) or {"ok": True},
                ),
                patch("ctl.install.actions.systemctl_restart", return_value={"ok": True}) as restart,
            ):
                self.assertEqual(install._address_pools_check(ctx)["state"], "missing")
                self.assertTrue(install.fix_address_pools({}, ctx)["ok"])
                path.write_text(written[0], encoding="utf-8")
                self.assertEqual(install._address_pools_check(ctx)["state"], "ready")
            restart.assert_called_once()
            self.assertIn("nvidia", written[0])


class FailureMessageTests(unittest.TestCase):
    OUTPUT = "Error response from daemon: all predefined address pools have been fully subnetted"

    def test_pool_exhaustion_is_named_and_explained(self):
        self.assertEqual(
            service_ops._failure_code("compose_start_failed", self.OUTPUT), "docker_network_space_exhausted"
        )
        self.assertIn("install.sh", service_ops._start_failure_message(self.OUTPUT))


if __name__ == "__main__":
    unittest.main()
