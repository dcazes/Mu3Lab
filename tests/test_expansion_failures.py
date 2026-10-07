"""Regressions for Dawarich HTTPS probes and Speech accelerator image downloads."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ctl import actions, image_fetch, service_state
from ctl.engine.compose import Compose
from ctl.engine.install import download_images
from ctl.lifecycle import app_releases, health
from ctl.registry import load
from ctl.runtime import RuntimePaths

ROOT = Path(__file__).resolve().parents[1]


class DawarichProbeTests(unittest.TestCase):
    def test_install_and_dashboard_probes_send_the_proxy_protocol(self):
        service = load().get("dawarich")
        response = Mock(status=200)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)

        def answer(request, **kwargs):
            self.assertEqual(request.full_url, "http://127.0.0.1:3006/api/v1/health")
            # Without this header Rails redirects to an HTTPS listener that does not exist.
            self.assertEqual(request.get_header("X-forwarded-proto"), "https")
            return response

        with patch("ctl.lifecycle.health.urllib.request.urlopen", side_effect=answer):
            self.assertEqual(health.wait_healthy(service), (True, "HTTP 200"))
            self.assertEqual(service_state._healthy(service), (True, "HTTP 200"))


class SpeechImagesTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.paths = RuntimePaths(Path(tmp.name))
        self.project = self.paths.projects / "speaches"
        self.project.mkdir(parents=True)
        for name in ("docker-compose.yml", "docker-compose.nvidia.yml"):
            shutil.copyfile(ROOT / "apps" / "speaches" / name, self.project / name)
        self.service = load().get("speaches")
        self.cpu = app_releases.compose_images(self.project / "docker-compose.yml")
        self.cuda = app_releases.compose_images(self.project / "docker-compose.nvidia.yml")

    def test_validation_download_fallback_and_start_use_the_same_files(self):
        for mode, expected in (("cpu", self.cpu), ("nvidia", self.cuda)):
            with self.subTest(mode=mode):
                compose = Compose(self.project, gpu_mode=mode)
                seen = []

                def command(argv, *args, seen=seen, **kwargs):
                    seen.append(argv)
                    if "--format" in argv:
                        images = self.cuda if str(self.project / "docker-compose.nvidia.yml") in argv else self.cpu
                        return 0, json.dumps({"services": {name: {"image": image} for name, image in images.items()}})
                    return 0, ""

                with (
                    patch("ctl.actions.docker_cmd", side_effect=command),
                    patch("ctl.actions.docker_cmd_stream", side_effect=command),
                    patch("ctl.engine.install.ImageDownloadStore.runtime", return_value=None),
                    patch("ctl.engine.install.RuntimePaths", return_value=self.paths),
                    patch(
                        "ctl.engine.install.image_fetch.fetch_images", side_effect=image_fetch.FetchError("offline")
                    ) as fetch,
                ):
                    compose.validate(lambda _: None)
                    self.assertEqual(
                        download_images(self.project, "speaches", "job", lambda _: None, compose=compose), (0, "")
                    )
                    compose.up(lambda _: None, wait_seconds=300)
                self.assertEqual(fetch.call_args.args[0], list(dict.fromkeys(expected.values())))
                file_sets = [[argv[i + 1] for i, value in enumerate(argv) if value == "-f"] for argv in seen]
                self.assertEqual(len(file_sets), 4)
                self.assertTrue(all(files == [str(path) for path in compose.files()] for files in file_sets))

    def test_a_new_gpu_install_records_the_images_it_downloads_and_starts(self):
        pinned = app_releases.pin(self.service, ROOT, lambda _: None, self.paths, gpu_mode="nvidia")
        self.assertEqual(pinned, self.cuda)
        self.assertEqual(app_releases.installed(self.service, ROOT, self.paths).images, self.cuda)
        # A retry keeps the pinned record; it never needs to delete models or app data.
        self.assertEqual(app_releases.pin(self.service, ROOT, lambda _: None, self.paths, gpu_mode="nvidia"), pinned)

    def test_download_uses_retained_release_record(self):
        old = {name: image.replace("0.8.3-cpu", "0.8.2-cpu") for name, image in self.cpu.items()}
        app_releases.write("speaches", app_releases.Release("v0.8.2", old), self.paths)
        with patch("ctl.actions.docker_cmd", return_value=(0, json.dumps({"services": {}}))) as command:
            actions.compose_image_list(self.project, lambda _: None)
        self.assertIn(str(self.project / "docker-compose.digest.yml"), command.call_args.args[0])
