"""Setup hands off to the dashboard while the core apps download and install in the worker."""

from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ctl import core_setup, install

OWNER = {"owner_uid": "owner", "username": "owner@test", "email": "owner@test", "display_name": "Owner"}


def service(service_id: str) -> SimpleNamespace:
    return SimpleNamespace(id=service_id, name=service_id.title())


def provisioning(protected: bool) -> Mock:
    store = Mock()
    state = "verified" if protected else "pending"
    store.summary.return_value = {"phases": [{"phase_id": "dashboard_protection", "actual_state": state}]}
    return store


class SignInGateTests(unittest.TestCase):
    def ready(self, *, protected: bool, actor: str = "bootstrap", identity=None, owner=None) -> bool:
        with (
            patch.object(core_setup.ProvisioningStore, "runtime", return_value=provisioning(protected)),
            patch.object(core_setup.workflow_secrets, "job_identity", return_value=identity),
            patch.object(core_setup, "owner_identity", return_value=owner),
        ):
            return core_setup._sign_in_ready("job", actor)

    def test_waits_until_the_dashboard_is_protected(self):
        self.assertFalse(self.ready(protected=False, identity=OWNER))

    def test_setup_job_waits_for_the_owner_once_one_exists(self):
        self.assertFalse(self.ready(protected=True, identity=None, owner=OWNER))
        self.assertTrue(self.ready(protected=True, identity=OWNER, owner=OWNER))

    def test_dashboard_retries_never_wait_for_an_owner(self):
        self.assertTrue(self.ready(protected=True, actor="owner@test"))


class DownloadsAheadTests(unittest.TestCase):
    def test_downloads_every_app_in_order_and_survives_a_failure(self):
        fetched: list[list[str]] = []

        def fetch(images, *_args, **_kwargs):
            fetched.append(images)
            if images == ["b:1"]:
                raise core_setup.image_fetch.FetchError("registry down")

        lines: list[str] = []
        with (
            patch.object(core_setup, "_images", side_effect=lambda svc, _root: [f"{svc.id}:1"]),
            patch.object(core_setup.image_fetch, "fetch_images", side_effect=fetch),
            patch.object(core_setup.ImageDownloadStore, "runtime", return_value=None),
        ):
            ahead = core_setup._DownloadsAhead(
                [service("a"), service("b"), service("c")], Path("/x"), "job", lines.append
            )
            ahead.thread.start()
            ahead.thread.join(5)
        self.assertEqual(fetched, [["a:1"], ["b:1"], ["c:1"]])
        self.assertTrue(all(event.is_set() for event in ahead.ready.values()))
        self.assertIn("retried when it installs", " ".join(lines))


class CoreRunTests(unittest.TestCase):
    def test_installs_wait_for_sign_in_and_each_app_waits_for_its_download(self):
        order: list[str] = []
        gate = iter([False, False, True])

        def fetch(images, *_args, **_kwargs):
            order.append("download " + images[0])

        def install_app(_store, _control, _parent, svc, *_args, **_kwargs):
            order.append("install " + svc.id)
            return True

        store = Mock()
        store.heartbeat.return_value = True
        with (
            patch.object(core_setup.ProvisioningStore, "runtime", return_value=None),
            patch.object(core_setup, "plan", return_value={"ready": True}),
            patch.object(core_setup, "load"),
            patch.object(core_setup.ControlState, "runtime"),
            patch.object(core_setup, "core_services", return_value=[service("ollama"), service("litellm")]),
            patch.object(core_setup, "_images", side_effect=lambda svc, _root: [svc.id]),
            patch.object(core_setup.image_fetch, "fetch_images", side_effect=fetch),
            patch.object(core_setup.ImageDownloadStore, "runtime", return_value=None),
            patch.object(core_setup, "_sign_in_ready", side_effect=lambda *_a: next(gate)),
            patch.object(core_setup.time, "sleep"),
            patch.object(core_setup, "run_install", side_effect=install_app),
            patch.object(core_setup, "configure_wiring", return_value={"chat_configured": True}),
            patch.object(core_setup, "_verify_platform", return_value=(True, "ok")),
        ):
            core_setup._run(store, "job", "bootstrap", Path("/x"), logger=lambda _line: None, worker_id="w")
        self.assertLess(order.index("download ollama"), order.index("install ollama"))
        self.assertLess(order.index("download litellm"), order.index("install litellm"))
        store.transition.assert_called_with("job", "succeeded", actor="bootstrap", detail=unittest.mock.ANY)


class AppProgressTests(unittest.TestCase):
    def progress(self, job_state: str, events: list[dict], downloads: dict) -> dict[str, str]:
        store = Mock()
        store.events.return_value = events
        store_downloads = Mock()
        store_downloads.for_job.return_value = downloads
        apps = [service("ollama"), service("litellm"), service("chat")]
        with patch.object(core_setup, "core_services", return_value=apps):
            rows = core_setup.app_progress(store, {"id": "job", "state": job_state}, store_downloads)
        return {row["service_id"]: row["state"] for row in rows}

    def test_each_app_says_where_it_is(self):
        events = [
            {"event": "step.started", "detail": "Installing Ollama."},
            {"event": "step.verified", "detail": "ollama"},
            {"event": "step.started", "detail": "Installing Litellm."},
        ]
        downloads = {"litellm": {"state": "done"}, "chat": {"state": "downloading"}}
        self.assertEqual(
            self.progress("running", events, downloads),
            {"ollama": "ready", "litellm": "installing", "chat": "downloading"},
        )

    def test_a_failed_install_is_named_and_downloads_stop(self):
        events = [{"event": "step.started", "detail": "Installing Ollama."}]
        states = self.progress("failed", events, {"chat": {"state": "downloading"}})
        self.assertEqual(states, {"ollama": "failed", "litellm": "waiting", "chat": "waiting"})


class LaunchTests(unittest.TestCase):
    def launch(self, jobs: list[dict], owner=None, identity=None) -> tuple[Mock, Mock, Mock]:
        store = Mock()
        store.jobs_for_service.return_value = jobs
        start = Mock(return_value={"id": "new"})
        save = Mock()
        with (
            patch("ctl.jobs.JobStore.runtime", return_value=store),
            patch("ctl.core_setup.start", start),
            patch("ctl.people.owner_identity", return_value=owner),
            patch("ctl.store.workflows.job_identity", return_value=identity),
            patch("ctl.store.workflows.save_job_identity", save),
        ):
            install.launch_core_setup({"root": Path("/x"), "emit": lambda _event: None})
        return store, start, save

    def test_early_launch_queues_the_job_before_any_owner_exists(self):
        _store, start, save = self.launch([])
        start.assert_called_once()
        save.assert_not_called()

    def test_later_launch_reuses_the_job_and_names_the_owner(self):
        _store, start, save = self.launch([{"id": "early", "state": "running"}], owner=OWNER)
        start.assert_not_called()
        self.assertEqual(save.call_args.args, ("early",))
        self.assertEqual(save.call_args.kwargs["email"], OWNER["email"])

    def test_a_failed_job_is_replaced(self):
        _store, start, _save = self.launch([{"id": "old", "state": "failed"}])
        start.assert_called_once()

    def test_core_launch_follows_authentik(self):
        ids = [step["id"] for step in install.STEPS]
        self.assertLess(ids.index(install.CORE_LAUNCH_AFTER), ids.index("authentik_setup"))


if __name__ == "__main__":
    unittest.main()
