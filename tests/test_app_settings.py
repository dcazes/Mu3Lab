"""R16: settings live in one canonical store; ``.env`` is only rendered from it."""

from __future__ import annotations

import stat
import tempfile
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from ctl import resource_locks, service_config
from ctl.app_settings import AppSettings
from ctl.control_state import ControlState, RevisionConflict
from ctl.engine import project as project_module
from ctl.engine.project import Facts, render
from ctl.manifest.catalog import load as load_catalog
from ctl.registry import load
from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env, runtime_env_text


class _Grocy(unittest.TestCase):
    """Grocy: one operator field (currency) and one managed, templated setting (public URL)."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paths = RuntimePaths(Path(self.temp.name))
        self.paths.state.mkdir(parents=True)
        self.catalog = load_catalog()
        self.app = self.catalog.get("grocy")
        self.facts = Facts("mu3lab.example.ts.net", self.paths, self.catalog)
        self.env_path = self.paths.projects / "grocy" / ".env"
        self.settings = AppSettings("grocy", self.paths)

    def render(self, facts: Facts | None = None) -> dict[str, str]:
        render(self.app, facts or self.facts)
        return read_runtime_env(self.env_path)


class ImportAndRenderTests(_Grocy):
    def test_an_existing_env_is_imported_once_without_losing_anything(self):
        self.env_path.parent.mkdir(parents=True)
        legacy = {"GROCY_CURRENCY": "EUR", "LEGACY_GENERATED": "keep-me", "TZ": "Old/Zone"}
        self.env_path.write_text(runtime_env_text(legacy), encoding="utf-8")
        env = self.render()
        self.assertEqual(self.settings.config(), {"currency": "EUR"})
        self.assertEqual(self.settings.generated()["LEGACY_GENERATED"], "keep-me")
        # Facts recomputed on every render are not frozen in the store.
        self.assertNotIn("TZ", self.settings.generated())
        self.assertEqual((env["GROCY_CURRENCY"], env["LEGACY_GENERATED"]), ("EUR", "keep-me"))
        self.assertTrue(self.settings.imported())
        self.assertEqual(self.settings.ensure_imported(self.app.manifest, self.env_path), 0)

    def test_deleting_the_env_and_rendering_again_reproduces_it(self):
        self.settings.set_config({"currency": "EUR"})
        self.render()
        first = self.env_path.read_text()
        self.env_path.unlink()
        self.render()
        self.assertEqual(self.env_path.read_text(), first)

    def test_the_env_is_never_read_back_as_input(self):
        self.render()
        env = read_runtime_env(self.env_path)
        env["GROCY_CURRENCY"] = "XXX"
        self.env_path.write_text(runtime_env_text(env), encoding="utf-8")
        self.assertNotEqual(self.render()["GROCY_CURRENCY"], "XXX")

    def test_managed_values_override_the_operator_and_the_operator_overrides_defaults(self):
        self.settings.set_config({"currency": "EUR"})
        env = self.render()
        self.assertEqual(env["GROCY_CURRENCY"], "EUR")
        self.assertEqual(env["GROCY_PUBLIC_URL"], "https://mu3lab.example.ts.net:8459")

    def test_a_briefly_unknown_fact_keeps_the_last_rendered_setting(self):
        self.render()
        unknown = Facts("", self.paths, self.catalog)
        self.assertEqual(self.render(unknown)["GROCY_PUBLIC_URL"], "https://mu3lab.example.ts.net:8459")

    def test_outputs_are_private_from_their_first_byte_and_leave_no_temporary_files(self):
        self.render()
        self.assertEqual(stat.S_IMODE(self.env_path.stat().st_mode), 0o600)
        self.assertEqual([path.name for path in self.env_path.parent.glob("*.tmp")], [])

    def test_a_failed_render_publishes_nothing(self):
        self.render()
        before = self.env_path.read_text()
        self.settings.set_config({"currency": "EUR"})
        with (
            patch.object(project_module.template, "render", side_effect=RuntimeError("disk full")),
            self.assertRaises(RuntimeError),
        ):
            render(self.app, self.facts)
        self.assertEqual(self.env_path.read_text(), before)


class ConfigurationWriteTests(_Grocy):
    def setUp(self):
        super().setUp()
        self.service = load().get("grocy")
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch("ctl.service_config.RuntimePaths", return_value=self.paths))
        self.paths.runtime.mkdir(parents=True, exist_ok=True)
        state = ControlState.runtime(self.paths)
        assert state is not None
        self.state = state
        self.stack.enter_context(patch("ctl.service_config.ControlState.runtime", return_value=self.state))

    def test_a_stale_form_cannot_overwrite_a_newer_change(self):
        first = service_config.revision(self.service)
        service_config.write(self.service, {"currency": "EUR"}, expected_revision=first)
        with self.assertRaises(RevisionConflict):
            service_config.write(self.service, {"currency": "USD"}, expected_revision=first)
        self.assertEqual(self.settings.config()["currency"], "EUR")
        service_config.write(self.service, {"currency": "USD"}, expected_revision=service_config.revision(self.service))
        self.assertEqual(self.settings.config()["currency"], "USD")

    def test_a_saved_value_survives_the_next_render(self):
        self.render()
        service_config.write(self.service, {"currency": "JPY"})
        self.assertEqual(read_runtime_env(self.env_path)["GROCY_CURRENCY"], "JPY")
        self.assertEqual(self.render()["GROCY_CURRENCY"], "JPY")

    def test_a_change_waits_for_a_job_that_holds_the_app(self):
        inside, release = threading.Event(), threading.Event()

        def job():
            with resource_locks.hold("app:grocy", paths=self.paths):
                inside.set()
                release.wait(5)

        thread = threading.Thread(target=job)
        thread.start()
        inside.wait(5)
        try:
            with (
                patch("ctl.service_config.LOCK_SECONDS", 0.3),
                self.assertRaises(service_config.ConfigurationBusy),
            ):
                service_config.write(self.service, {"currency": "EUR"})
        finally:
            release.set()
            thread.join()
        self.assertNotIn("currency", self.settings.config())

    def test_a_field_mu3lab_manages_is_not_editable(self):
        with patch("ctl.service_config.managed_names", return_value={"GROCY_CURRENCY"}):
            self.assertEqual(service_config.managed_keys(self.service), {"currency"})
            with self.assertRaisesRegex(ValueError, "Mu3Lab manages currency"):
                service_config.write(self.service, {"currency": "EUR"})


if __name__ == "__main__":
    unittest.main()
