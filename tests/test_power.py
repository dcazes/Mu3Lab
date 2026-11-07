"""Turning all of Mu3Lab off and on (ctl/power.py) and the wake page (ctl/power_wake.py)."""

from __future__ import annotations

import json
import re
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ctl import power, power_wake
from ctl.runtime import RuntimePaths


def _service(tier: str, capabilities: tuple[str, ...] = ()) -> SimpleNamespace:
    return SimpleNamespace(manifest=SimpleNamespace(tier=tier, capabilities=capabilities))


def _item(name: str, project: str, rank: int, level: int = 0) -> dict:
    return {"name": name, "project": project, "rank": rank, "level": level}


class OrderTests(unittest.TestCase):
    def test_a_database_stops_after_the_app_that_needs_it(self):
        levels = power._levels({"server": {"redis", "database"}, "redis": set(), "database": set(), "ml": set()})
        self.assertEqual(levels, {"server": 0, "ml": 0, "redis": 1, "database": 1})

    def test_a_chain_of_needs_stops_from_the_top(self):
        self.assertEqual(power._levels({"a": {"b"}, "b": {"c"}, "c": set()}), {"a": 0, "b": 1, "c": 2})

    def test_a_loop_of_needs_still_ends(self):
        self.assertEqual(set(power._levels({"a": {"b"}, "b": {"a"}})), {"a", "b"})

    def test_sign_in_and_caddy_stop_last_connectors_first(self):
        services = {
            "mu3lab-photos": _service("optional"),
            "mu3lab-chat": _service("core"),
            "mu3lab-vault": _service("foundation"),
            "mu3lab-login": _service("foundation", ("identity_provider",)),
            "mu3lab-proxy": _service("foundation", ("private_proxy",)),
        }
        ranks = {project: power._rank(project, services) for project in [*services, "mu3lab-mcp-photos"]}
        self.assertEqual(
            sorted(ranks, key=ranks.__getitem__),
            ["mu3lab-mcp-photos", "mu3lab-photos", "mu3lab-chat", "mu3lab-vault", "mu3lab-login", "mu3lab-proxy"],
        )

    def test_batches_group_one_rank_and_level_at_a_time(self):
        containers = [
            _item("app", "p", power.APPS),
            _item("db", "p", power.APPS, 1),
            _item("other", "q", power.APPS),
            _item("caddy", "proxy", power.PROXY),
        ]
        self.assertEqual(
            power._batches(containers),
            [(power.APPS, ["app", "other"]), (power.APPS, ["db"]), (power.PROXY, ["caddy"])],
        )

    def test_the_public_status_never_shows_containers(self):
        record = {"state": "off", "containers": [_item("secret-name", "p", 1)], "ports": [1], "error": ""}
        self.assertEqual(power.public(record), {"state": "off"})
        self.assertEqual(power.public(None), {"state": "on"})

    def test_the_wake_page_shows_the_same_fields_as_the_dashboard(self):
        self.assertEqual(power_wake.RECORD, power.RECORD)
        self.assertEqual(power_wake.DASHBOARD_PORT, power.DASHBOARD_CADDY_PORT)
        self.assertEqual(power_wake.RUN_UNIT, power.RUN_UNITS["on"])
        record = {"state": "turning_on", "step": "Starting", "done": 1, "total": 2, "since": "x", "containers": []}
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / power.RECORD).write_text(json.dumps(record))
            shown = power_wake.Wake(Path("/unused"), Path(tmp)).status()
        self.assertEqual(shown, power.public(record) | {"wake": True})


class TurningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.paths = RuntimePaths(Path(self.tmp.name))
        self.paths.state.mkdir()
        self.calls: list[tuple[str, ...]] = []

    def tearDown(self):
        self.tmp.cleanup()

    def _systemctl(self, *argv: str, log) -> bool:
        self.calls.append(("systemctl", *argv))
        return True

    def _docker(self, argv, log, timeout=0):
        names = [item for item in argv[2:] if not item.startswith("-") and not item.isdigit()]
        self.calls.append(("docker", argv[1], *names))
        return 0, ""

    def test_turning_off_saves_the_list_first_and_stops_the_dashboard_before_sign_in(self):
        containers = [
            _item("mcp", "mu3lab-mcp-x", power.CONNECTORS),
            _item("app", "mu3lab-x", power.APPS),
            _item("login", "mu3lab-login", power.IDENTITY),
            _item("caddy", "mu3lab-proxy", power.PROXY),
        ]
        saved: list[list[str]] = []

        def docker(argv, log, timeout=0):
            saved.append([item["name"] for item in power.read(self.paths)["containers"]])
            return self._docker(argv, log, timeout)

        with (
            patch.object(power, "running_containers", return_value=containers),
            patch.object(power, "blockers", return_value=[]),
            patch.object(power, "_caddy_ports", return_value=[19460, 19461]),
            patch.object(power, "_systemctl", side_effect=self._systemctl),
            patch.object(power.actions, "docker_cmd", side_effect=docker),
        ):
            power.turn_off(lambda _line: None, self.paths)
        self.assertEqual(saved[0], ["mcp", "app", "login", "caddy"])
        stops = [call for call in self.calls if call[0] == "docker"]
        self.assertEqual([call[2:] for call in stops], [("mcp",), ("app",), ("login",), ("caddy",)])
        order = [" ".join(call) for call in self.calls]
        self.assertEqual(order[0], f"systemctl stop {power.WORKER_UNIT}")
        self.assertLess(
            order.index("systemctl disable --now mu3lab-ctl.service mu3lab-worker.service"),
            order.index("docker stop login"),
        )
        self.assertGreater(
            order.index("systemctl disable --now mu3lab-ctl.service mu3lab-worker.service"),
            order.index("docker stop app"),
        )
        self.assertEqual(order[-2:], [f"systemctl enable {power.WAKE_UNIT}", f"systemctl restart {power.WAKE_UNIT}"])
        record = power.read(self.paths)
        self.assertEqual(record["state"], "off")
        self.assertEqual(record["ports"], [19460, 19461])

    def test_turning_off_again_keeps_what_an_interrupted_turn_off_already_stopped(self):
        earlier = {"state": "turning_off", "since": "then", "containers": [_item("stopped", "mu3lab-x", power.APPS)]}
        power._write(earlier, self.paths)
        with (
            patch.object(power, "running_containers", return_value=[_item("still", "mu3lab-y", power.APPS)]),
            patch.object(power, "blockers", return_value=[]),
            patch.object(power, "_caddy_ports", return_value=[19460]),
            patch.object(power, "_systemctl", side_effect=self._systemctl),
            patch.object(power.actions, "docker_cmd", side_effect=self._docker),
        ):
            power.turn_off(lambda _line: None, self.paths)
        record = power.read(self.paths)
        self.assertEqual(sorted(item["name"] for item in record["containers"]), ["still", "stopped"])
        self.assertEqual(record["since"], "then")

    def test_turning_off_is_refused_while_a_task_runs(self):
        with (
            patch.object(power, "blockers", return_value=["1 task is still running."]),
            self.assertRaisesRegex(power.PowerError, "task"),
        ):
            power.turn_off(lambda _line: None, self.paths)
        self.assertIsNone(power.read(self.paths))

    def test_turning_on_starts_sign_in_first_and_caddy_after_the_wake_page_lets_go(self):
        record = {
            "state": "off",
            "containers": [
                _item("mcp", "mu3lab-mcp-x", power.CONNECTORS),
                _item("app", "mu3lab-x", power.APPS),
                _item("db", "mu3lab-x", power.APPS, 1),
                _item("login", "mu3lab-login", power.IDENTITY),
                _item("login-db", "mu3lab-login", power.IDENTITY, 1),
                _item("caddy", "mu3lab-proxy", power.PROXY),
            ],
        }
        power._write(record, self.paths)
        with (
            patch("ctl.registry.load", return_value=SimpleNamespace(services=())),
            patch.object(power, "_systemctl", side_effect=self._systemctl),
            patch.object(power.actions, "docker_cmd", side_effect=self._docker),
            patch.object(power, "_wait_until", return_value=True),
            patch.object(power.time, "sleep"),
        ):
            power.turn_on(lambda _line: None, self.paths)
        order = [" ".join(call) for call in self.calls]
        self.assertEqual(
            order,
            [
                "docker start login-db",
                "docker start login",
                "docker start db",
                "docker start app",
                "docker start mcp",
                "systemctl enable mu3lab-ctl.service mu3lab-worker.service",
                "systemctl start mu3lab-ctl.service mu3lab-worker.service",
                f"systemctl disable --now {power.WAKE_UNIT}",
                "docker start caddy",
            ],
        )
        self.assertIsNone(power.read(self.paths))


class WakePageTests(unittest.TestCase):
    """The real wake page, on a free loopback port."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name)
        (self.state / power.RECORD).write_text(
            json.dumps({"state": "off", "since": "2026-11-07T12:00:00+00:00", "containers": [_item("hidden", "p", 1)]})
        )
        self.wake = power_wake.Wake(Path("/opt/mu3lab"), self.state)
        self.server = power_wake.serve(self.wake, [0])[0]
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def _open(self, path: str, method: str = "GET", headers: dict[str, str] | None = None):
        request = urllib.request.Request(
            self.base + path, method=method, headers=headers or {}, data=b"" if method == "POST" else None
        )
        try:
            return urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as error:
            return error

    def test_the_page_offers_one_button_with_a_locked_down_policy(self):
        response = self._open("/")
        body = response.read().decode()
        self.assertEqual(response.status, 200)
        self.assertIn("Turn on Mu3Lab", body)
        nonce = re.search(r"script-src 'nonce-([^']+)'", response.headers["Content-Security-Policy"]).group(1)
        self.assertIn(f'<script nonce="{nonce}">', body)
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_an_app_link_says_mu3lab_is_off(self):
        response = self._open("/photos/album/1")
        self.assertEqual(response.status, 503)
        self.assertIn("Mu3Lab is off", response.read().decode())

    def test_the_status_says_off_and_names_no_container(self):
        body = json.loads(self._open(power_wake.POWER_PATH).read())
        self.assertEqual(body, {"state": "off", "since": "2026-11-07T12:00:00+00:00", "wake": True})

    def test_turning_on_needs_the_page_s_own_header(self):
        self.assertEqual(self._open(f"{power_wake.POWER_PATH}/on", "POST").status, 403)
        self.assertEqual(self._open(f"{power_wake.POWER_PATH}/on", "OPTIONS").status, 405)

    def test_the_button_starts_turning_on_as_its_own_unit(self):
        with patch.object(
            power_wake.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="", stderr="")
        ) as run:
            response = self._open(f"{power_wake.POWER_PATH}/on", "POST", {power_wake.WAKE_HEADER: "on"})
        self.assertEqual(response.status, 202)
        argv = run.call_args.args[0]
        self.assertEqual(argv[:4], ["systemd-run", "--user", "--collect", f"--unit={power.RUN_UNITS['on']}"])
        self.assertEqual(argv[-3:], ["-m", "ctl.power", "run-on"])
        self.assertIn(f"--setenv=MU3LAB_RUNTIME_ROOT={self.state.parent}", argv)


if __name__ == "__main__":
    unittest.main()
