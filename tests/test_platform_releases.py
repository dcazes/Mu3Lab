"""Release verification protects the installed UI and pins every built image."""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from ctl import dashboard_build, platform_releases
from ctl.bootstrap import stamps
from tools.release_artifacts import inventory


class ReleaseAssetsTests(unittest.TestCase):
    def test_development_uses_pinned_container_and_private_preview(self):
        argv = dashboard_build.command(Path("/development"), "build")
        self.assertIn(dashboard_build.NODE_IMAGE, argv)
        self.assertIn("--user", argv)
        self.assertIn("npm ci && npm run build", argv)
        preview = dashboard_build.command(Path("/development"), "dev")
        self.assertIn("--network", preview)
        self.assertIn("npm ci && npm run dev -- --host 127.0.0.1", preview)

    def test_a_bad_checksum_never_replaces_the_installed_dashboard(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "dashboard/dist").mkdir(parents=True)
            (root / "dashboard/dist/index.html").write_text("existing")

            def download(url, target):
                if url.endswith("SHA256SUMS"):
                    target.write_text(
                        "0" * 64 + "  mu3lab-dashboard-v1.2.3.tar.gz\n" + "0" * 64 + "  release-images.json\n"
                    )
                else:
                    target.write_bytes(b"invalid")

            with patch.object(platform_releases, "_download", side_effect=download), self.assertRaises(ValueError):
                platform_releases.install_dashboard(root, "v1.2.3")
            self.assertEqual((root / "dashboard/dist/index.html").read_text(), "existing")

    def test_verified_dashboard_is_installed_with_source_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "dashboard").mkdir()
            stamp = stamps.dashboard_digest(root)
            stream = io.BytesIO()
            with tarfile.open(fileobj=stream, mode="w:gz") as archive:
                for name, content in [
                    ("dist/index.html", b"new"),
                    ("dist/assets/app.js", b"code"),
                    ("dist/.mu3lab-build.sha256", stamp.encode()),
                ]:
                    item = tarfile.TarInfo(name)
                    item.size = len(content)
                    archive.addfile(item, io.BytesIO(content))
            values = {"mu3lab-dashboard-v1.2.3.tar.gz": stream.getvalue(), "release-images.json": b"{}"}
            values["SHA256SUMS"] = "".join(
                hashlib.sha256(value).hexdigest() + "  " + name + "\n" for name, value in values.items()
            ).encode()
            with patch.object(
                platform_releases,
                "_download",
                side_effect=lambda url, target: target.write_bytes(values[url.rsplit("/", 1)[1]]),
            ):
                platform_releases.install_dashboard(root, "v1.2.3")
            self.assertEqual((root / "dashboard/dist/index.html").read_text(), "new")

    def test_release_runtime_uses_digest_and_never_builds_locally(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "platform/example/docker-compose.yml"
            source.parent.mkdir(parents=True)
            source.write_text("services:\n  gateway:\n    build: .\n    image: development\n")
            target = root / "runtime.yml"
            target.write_text(source.read_text())
            image = "ghcr.io/dcazes/mu3lab-example@sha256:" + "a" * 64
            manifest = root / ".tools/releases/v1.2.3/release-images.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({"platform/example/docker-compose.yml#gateway": image}))
            with patch.object(platform_releases, "exact_tag", return_value="v1.2.3"):
                platform_releases.pin_compose(root, source, target)
            service = yaml.safe_load(target.read_text())["services"]["gateway"]
            self.assertEqual(service["image"], image)
            self.assertNotIn("build", service)

    def test_every_local_image_is_in_the_release_inventory(self):
        entries = inventory()
        self.assertEqual(len(entries), 8)
        self.assertEqual(len({item["name"] for item in entries}), 8)
