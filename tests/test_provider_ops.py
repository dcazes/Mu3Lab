"""Provider verification asks FreeLLMAPI, which keeps the free-model knowledge."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from ctl.provider_ops import _reconcile
from ctl.registry import load


class ReconcileRestartTests(unittest.TestCase):
    """Only a gateway whose rendered files changed is recreated."""

    def _reconcile(self, projects: Path, new_key: str) -> dict[str, bool]:
        def fake_configure(_paths, _catalog):
            (projects / "freellmapi" / "freellmapi.config.json").write_text(new_key)
            return {"freellmapi_config": "f", "litellm_config": "l"}

        recreated: dict[str, bool] = {}

        def fake_compose_up(compose, _log, *, recreate, wait_seconds):
            recreated[compose.directory.name] = recreate
            return 0, ""

        registry = load()
        paths = type("P", (), {"projects": projects, "data": projects})()
        with (
            patch("ctl.provider_ops.RuntimePaths", return_value=paths),
            patch("ctl.provider_ops.configure", side_effect=fake_configure),
            patch("ctl.provider_ops.load", return_value=registry),
            patch("ctl.provider_ops.Compose.up", autospec=True, side_effect=fake_compose_up),
            patch("ctl.provider_ops.read_runtime_env", return_value={}),
            patch("ctl.provider_ops._forget_removed_keys", return_value=(True, "")),
        ):
            _reconcile(Path("."), lambda _line: None)
        return recreated

    def test_a_new_key_recreates_only_freellmapi(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            projects = Path(tmp)
            for sid in ("freellmapi", "litellm"):
                (projects / sid).mkdir()
                (projects / sid / "docker-compose.yml").write_text("services: {}")
            self.assertEqual(self._reconcile(projects, "key-1"), {"freellmapi": True, "litellm": False})
            # Rendering the same key again (e.g. after a pass) restarts nothing.
            self.assertEqual(self._reconcile(projects, "key-1"), {"freellmapi": False, "litellm": False})


class ProviderRemovalTests(unittest.TestCase):
    def _remove(self, reconcile_result):
        import tempfile
        from unittest.mock import MagicMock

        from ctl import provider_ops
        from ctl.control_state import ControlState

        with tempfile.TemporaryDirectory() as tmp:
            state = ControlState(Path(tmp) / "control.sqlite3")
            state.set_provider("groq", "Groq", state="verified")
            store = MagicMock()
            job = {"id": "job-1", "service_id": "provider:groq", "action": "remove", "actor": "owner"}
            with (
                patch("ctl.provider_ops.ControlState.runtime", return_value=state),
                patch("ctl.provider_ops.delete_secret") as delete_secret,
                patch("ctl.provider_ops._reconcile", return_value=reconcile_result),
            ):
                provider_ops.execute_claimed(store, job, "worker", Path("."))
            return state.provider("groq"), store.transition.call_args, delete_secret

    def test_removal_fails_visibly_when_the_gateway_keeps_the_key(self):
        record, transition, _ = self._remove((False, "FreeLLMAPI still holds a removed key (Groq).", []))
        self.assertEqual(transition.args[1], "failed")
        self.assertEqual(transition.kwargs["error_code"], "provider_removal_incomplete")
        self.assertEqual(record["state"], "removing")

    def test_removal_completes_only_after_the_gateway_confirms(self):
        record, transition, delete_secret = self._remove((True, "ok", []))
        self.assertEqual(transition.args[1], "succeeded")
        self.assertIsNone(record)
        delete_secret.assert_called_once_with("groq")


if __name__ == "__main__":
    unittest.main()
