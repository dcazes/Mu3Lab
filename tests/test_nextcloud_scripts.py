"""Run the app-owned setup scripts with fake container commands, never Docker."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Every command available to the script is a fixture. No host users, PHP,
# filesystem layout, clock or container can affect the results.
COMMAND = r"""
import fcntl, json, os, pathlib, sys
root = pathlib.Path(os.environ["FIXTURE_ROOT"])
name = pathlib.Path(sys.argv[0]).name
payload = json.loads(sys.stdin.read()) if name == "php" else {}
lock = (root / "fixture.lock").open("a")
fcntl.flock(lock, fcntl.LOCK_EX)
state = json.loads((root / "state.json").read_text())
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
state["calls"].append([name, *args])
rc, output = 0, ""
if name == "date":
    state["clock"] += 1
    output = str(1000 + state["clock"] * 10)
elif name == "flock":
    rc = int(state["locks"] > 0)
    state["locks"] = max(0, state["locks"] - 1)
    if not rc and state.get("entrypoint_installs"):
        state["installed"] = True
elif name == "runuser":
    command = args[args.index("occ") + 1:]
    if command[0] == "status":
        output = json.dumps({"installed": state["installed"]})
    elif command[0] == "maintenance:install":
        state["installed"] = True
    elif command[0] == "app:install":
        rc = 1  # already installed, at a compatible app-store version
    elif command[0] == "app:enable":
        rc = 1 if state.get("enable_fails") else 0
    elif command[0] == "user_oidc:providers":
        output = json.dumps({"identifier": "mu3lab", "id": 17})
elif name == "php":
    if "installed" in args[-1]:
        rc = 0 if payload.get("installed") else 1
    else:
        output = str(payload["id"])
elif name != "sleep":
    rc = 98
(root / "state.json").write_text(json.dumps(state))
if output:
    print(output)
sys.exit(rc)
"""


class NextcloudScriptTests(unittest.TestCase):
    def run_script(self, name, *, installed=False, locks=0, entrypoint_installs=False, enable_fails=False, admin=True):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = {
                "installed": installed,
                "locks": locks,
                "entrypoint_installs": entrypoint_installs,
                "enable_fails": enable_fails,
                "clock": 0,
                "calls": [],
            }
            (root / "state.json").write_text(json.dumps(state))
            for command in ("runuser", "php", "flock", "date", "sleep"):
                path = root / command
                path.write_text(f"#!{sys.executable}\n" + COMMAND)
                os.chmod(path, 0o700)
            lock = root / "nextcloud-init-sync.lock"
            lock.touch()
            script = (
                (ROOT / "apps/nextcloud/scripts" / name)
                .read_text()
                .replace("/var/www/html/nextcloud-init-sync.lock", str(lock))
            )
            env = {
                "PATH": str(root),
                "FIXTURE_ROOT": str(root),
                "POSTGRES_PASSWORD": "fixture-db-password",
                "MU3LAB_OIDC_CLIENT_ID": "fixture-client",
                "MU3LAB_OIDC_CLIENT_SECRET": "fixture-secret",
                "MU3LAB_OIDC_DISCOVERY_URL": "https://fixture.test/discovery",
            }
            if admin:
                env.update(NEXTCLOUD_ADMIN_USER="owner", NEXTCLOUD_ADMIN_PASSWORD="fixture-admin-password")
            result = subprocess.run(["/bin/sh"], input=script, text=True, capture_output=True, env=env, timeout=5)
            return result, json.loads((root / "state.json").read_text())["calls"]

    def test_waits_for_entrypoint_lock_and_keeps_its_completed_installation(self):
        result, calls = self.run_script("install.sh", locks=2, entrypoint_installs=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(sum(call[0] == "sleep" for call in calls), 2)
        self.assertFalse(any("maintenance:install" in call for call in calls))

    def test_leftover_unheld_lock_does_not_delay_database_installation(self):
        result, calls = self.run_script("install.sh")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(any(call[0] == "sleep" for call in calls))
        self.assertEqual(sum("maintenance:install" in call for call in calls), 1)

    def test_existing_installation_never_gets_another_administrator(self):
        result, calls = self.run_script("install.sh", installed=True, admin=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(any("maintenance:install" in call for call in calls))

    def test_incomplete_installation_requires_first_start_administrator(self):
        result, calls = self.run_script("install.sh", admin=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no first-start administrator", result.stdout)
        self.assertFalse(any("maintenance:install" in call for call in calls))

    def test_compatible_existing_apps_are_enabled_and_provider_id_is_returned(self):
        result, _ = self.run_script("configure.sh")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("MU3LAB_OUTPUT NEXTCLOUD_OIDC_PROVIDER_ID=17", result.stdout)
        self.assertNotIn("fixture-secret", result.stdout + result.stderr)

    def test_failed_app_enable_stops_before_registering_authentik(self):
        result, calls = self.run_script("configure.sh", enable_fails=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("could not enable", result.stdout)
        self.assertFalse(any("user_oidc:provider" in call for call in calls))
