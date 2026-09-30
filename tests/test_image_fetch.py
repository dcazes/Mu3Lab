"""Mu3Lab's verified parallel image downloader, against a fake registry and CDN."""

from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from ctl import image_fetch
from ctl.image_fetch import FetchError, Paused, fetch_images, parse_ref

CHUNK = 1024


def digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


class FakeRegistry:
    """One repository on reg.test whose blobs redirect to a ranged CDN."""

    def __init__(self, layers: list[bytes], diff_ids: list[str] | None = None) -> None:
        self.layers = layers
        self.config = json.dumps(
            {"rootfs": {"type": "layers", "diff_ids": diff_ids or [digest(b"diff" + layer) for layer in layers]}}
        ).encode()
        self.manifest = json.dumps(
            {
                "schemaVersion": 2,
                "mediaType": "application/vnd.oci.image.manifest.v1+json",
                "config": {"digest": digest(self.config), "size": len(self.config)},
                "layers": [{"digest": digest(layer), "size": len(layer)} for layer in layers],
            }
        ).encode()
        self.index = json.dumps(
            {
                "schemaVersion": 2,
                "mediaType": "application/vnd.oci.image.index.v1+json",
                "manifests": [
                    {"digest": digest(b"arm"), "size": 3, "platform": {"os": "linux", "architecture": "arm64"}},
                    {
                        "digest": digest(self.manifest),
                        "size": len(self.manifest),
                        "platform": {"os": "linux", "architecture": "amd64"},
                    },
                ],
            }
        ).encode()
        self.blobs = {digest(data): data for data in [self.config, *layers]}
        self.ranges: list[str] = []
        self.corrupt: set[str] = set()
        # A pin the registry answers with different content, as a tampered mirror would.
        self.lying_pin = "sha256:" + "f" * 64
        self.lock = threading.Lock()

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        if url.host == "auth.test":
            return httpx.Response(200, json={"token": "t"})
        if url.path == "/v2/":
            return httpx.Response(
                401, headers={"www-authenticate": 'Bearer realm="https://auth.test/token",service="reg.test"'}
            )
        if url.host == "reg.test" and request.headers.get("authorization") != "Bearer t":
            return httpx.Response(401)
        if "/manifests/" in url.path:
            reference = url.path.rsplit("/", 1)[1]
            if reference in {"v1", digest(self.index), self.lying_pin}:
                return httpx.Response(
                    200, content=self.index, headers={"content-type": "application/vnd.oci.image.index.v1+json"}
                )
            if reference == digest(self.manifest):
                return httpx.Response(
                    200, content=self.manifest, headers={"content-type": "application/vnd.oci.image.manifest.v1+json"}
                )
            return httpx.Response(404)
        if "/blobs/" in url.path:
            return httpx.Response(307, headers={"location": f"https://cdn.test/{url.path.rsplit('/', 1)[1]}"})
        if url.host == "cdn.test":
            data = self.blobs[url.path.lstrip("/")]
            if url.path.lstrip("/") in self.corrupt:
                data = b"x" * len(data)
            header = request.headers.get("range")
            if not header:
                return httpx.Response(200, content=data)
            start, end = (int(part) for part in header.removeprefix("bytes=").split("-"))
            with self.lock:
                self.ranges.append(f"{url.path.lstrip('/').removeprefix('sha256:')[:12]}:{start}")
            return httpx.Response(206, content=data[start : end + 1])
        return httpx.Response(404)


class FakeDocker:
    def __init__(self, present: set[str] | None = None, local: list[dict] | None = None) -> None:
        self.present = set(present or ())
        self.local = local or []
        self.archives: list[dict[str, bytes]] = []
        self.unpack_error = False
        self.removed: list[str] = []

    def load(self, write, _log):
        buffer = io.BytesIO()
        write(buffer)
        buffer.seek(0)
        with tarfile.open(fileobj=buffer, mode="r") as archive:
            files = {member.name: archive.extractfile(member).read() for member in archive.getmembers()}
        self.archives.append(files)
        name = json.loads(files["index.json"])["manifests"][0]["annotations"]["io.containerd.image.name"]
        if self.unpack_error:
            self.unpack_error = False
            return 0, f"Loaded image: {name}\nError unpacking image {name}: content digest not found"
        self.present.add(name)
        return 0, f"Loaded image: {name}"

    def patches(self):
        return (
            patch("ctl.image_fetch.actions.docker_image_present", side_effect=self.is_present),
            patch("ctl.image_fetch.actions.docker_local_images", return_value=self.local),
            patch("ctl.image_fetch.actions.docker_load_stream", side_effect=self.load),
            patch(
                "ctl.image_fetch.actions.docker_image_remove", side_effect=lambda name, _log: self.removed.append(name)
            ),
        )

    def is_present(self, image: str) -> bool:
        ref = parse_ref(image)
        return image in self.present or ref.name in self.present


class FetchTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = Path(self.tmp.name)
        for target, value in (
            ("ctl.image_fetch.CHUNK", CHUNK),
            ("ctl.image_fetch.host_platform", lambda: ("linux", "amd64")),
            ("ctl.image_fetch._check_space", lambda _cache, _needed: None),
            ("ctl.image_fetch.time.sleep", lambda _seconds: None),
        ):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def fetch(self, registry: FakeRegistry, docker: FakeDocker, images: list[str], **kwargs):
        client = httpx.Client(transport=httpx.MockTransport(registry.handler))
        self.addCleanup(client.close)
        patches = docker.patches()
        for item in patches:
            item.start()
        try:
            return fetch_images(images, self.cache, lambda _line: None, client=client, **kwargs)
        finally:
            for item in patches:
                item.stop()


class ReferenceTests(unittest.TestCase):
    def test_normalises_references_like_docker(self):
        cases = {
            "redis:8-alpine": ("registry-1.docker.io", "library/redis", "8-alpine", "docker.io/library/redis:8-alpine"),
            "pgvector/pgvector:pg17": (
                "registry-1.docker.io",
                "pgvector/pgvector",
                "pg17",
                "docker.io/pgvector/pgvector:pg17",
            ),
            "ghcr.io/a/b": ("ghcr.io", "a/b", "latest", "ghcr.io/a/b:latest"),
            "localhost:5000/app:1": ("localhost:5000", "app", "1", "localhost:5000/app:1"),
        }
        for value, (registry, repository, tag, name) in cases.items():
            with self.subTest(value=value):
                ref = parse_ref(value)
                self.assertEqual((ref.registry, ref.repository, ref.tag, ref.name), (registry, repository, tag, name))

    def test_keeps_the_pin_and_names_digest_only_images_by_digest(self):
        pin = "sha256:" + "a" * 64
        self.assertEqual(parse_ref(f"ghcr.io/a/b:1.0@{pin}").reference, pin)
        self.assertEqual(parse_ref(f"ghcr.io/a/b@{pin}").name, f"ghcr.io/a/b@{pin}")
        with self.assertRaises(FetchError):
            parse_ref("ghcr.io/a/b@md5:abc")


class DownloadTests(FetchTestCase):
    def test_downloads_in_ranges_verifies_and_loads_a_pinned_image(self):
        registry = FakeRegistry([os.urandom(5000), os.urandom(300)])
        docker = FakeDocker()
        reports: list[dict] = []
        image = f"reg.test/team/app:v1@{digest(registry.index)}"
        result = self.fetch(registry, docker, [image], report=reports.append)
        self.assertEqual(result.images, 1)
        self.assertEqual(result.downloaded_bytes, 5300)
        files = docker.archives[0]
        index = json.loads(files["index.json"])["manifests"][0]
        self.assertEqual(index["digest"], digest(registry.index))
        self.assertEqual(index["annotations"]["io.containerd.image.name"], "reg.test/team/app:v1")
        for layer in registry.layers:
            self.assertEqual(files[f"blobs/sha256/{digest(layer)[7:]}"], layer)
        # The 5000-byte layer came down as five 1 KiB ranges.
        self.assertEqual(sum(item.startswith(digest(registry.layers[0])[7:19]) for item in registry.ranges), 5)
        self.assertEqual(reports[-1]["state"], "done")
        self.assertEqual(reports[-1]["done_bytes"], 5300)
        self.assertEqual(list((self.cache / "blobs").iterdir()), [])

    def test_a_pin_that_does_not_match_the_registry_is_refused(self):
        registry = FakeRegistry([b"layer"])
        with self.assertRaises(FetchError) as caught:
            self.fetch(registry, FakeDocker(), [f"reg.test/team/app:v1@{registry.lying_pin}"])
        self.assertIn("did not match its pinned digest", str(caught.exception))
        self.assertEqual(registry.ranges, [])

    def test_images_docker_already_has_are_not_downloaded(self):
        registry = FakeRegistry([b"layer"])
        docker = FakeDocker(present={"reg.test/team/app:v1"})
        result = self.fetch(registry, docker, ["reg.test/team/app:v1"])
        self.assertEqual(result.images, 0)
        self.assertEqual(registry.ranges, [])

    def test_a_tampered_layer_is_discarded_and_reported(self):
        registry = FakeRegistry([os.urandom(3000)])
        registry.corrupt.add(digest(registry.layers[0]))
        docker = FakeDocker()
        with self.assertRaises(FetchError) as caught:
            self.fetch(registry, docker, ["reg.test/team/app:v1"])
        self.assertIn("failed verification", str(caught.exception))
        self.assertEqual(docker.archives, [])
        self.assertEqual(list((self.cache / "blobs").iterdir()), [])

    def test_pausing_keeps_finished_ranges_and_resuming_fetches_only_the_rest(self):
        registry = FakeRegistry([os.urandom(8 * CHUNK)])
        stop = threading.Event()
        original = registry.handler

        def pause_after_three(request: httpx.Request) -> httpx.Response:
            if request.url.host == "cdn.test" and len(registry.ranges) >= 3:
                stop.set()
            return original(request)

        registry.handler = pause_after_three
        with patch("ctl.image_fetch.NARROW_WIDTH", 1), self.assertRaises(Paused):
            self.fetch(registry, FakeDocker(), ["reg.test/team/app:v1"], stop=stop)
        first_attempt = len(registry.ranges)
        registry.handler = original
        docker = FakeDocker()
        self.fetch(registry, docker, ["reg.test/team/app:v1"])
        resumed = len(registry.ranges) - first_attempt
        self.assertLess(resumed, 8)
        self.assertEqual(docker.archives[0][f"blobs/sha256/{digest(registry.layers[0])[7:]}"], registry.layers[0])

    def test_slow_connections_widen_the_download(self):
        registry = FakeRegistry([os.urandom(40 * CHUNK)])
        logs: list[str] = []
        client = httpx.Client(transport=httpx.MockTransport(registry.handler))
        self.addCleanup(client.close)
        docker = FakeDocker()
        patches = docker.patches()
        for item in patches:
            item.start()
        self.addCleanup(lambda: [item.stop() for item in patches])
        with (
            patch("ctl.image_fetch.WIDEN_AFTER", 0.0),
            patch("ctl.image_fetch.FAST_CONNECTION", 10**12),
            patch.object(image_fetch.Progress, "rate", lambda _self: 1000.0),
            patch.object(image_fetch.Downloader, "_fetch_range", side_effect=_slow_range, autospec=True),
            patch.object(image_fetch.Downloader, "_finish", lambda _self, _blob: None),
            patch("ctl.image_fetch.load_image", return_value=True),
        ):
            fetch_images(["reg.test/team/app:v1"], self.cache, logs.append, client=client)
        self.assertTrue(any("widening to 16 connections" in line for line in logs), logs)


def _slow_range(self, blob, index):
    threading.Event().wait(0.02)
    return 1.0


class LayerReuseTests(FetchTestCase):
    def test_layers_docker_already_holds_are_skipped(self):
        shared, own = os.urandom(4000), os.urandom(500)
        registry = FakeRegistry([shared, own], diff_ids=["sha256:" + "1" * 64, "sha256:" + "2" * 64])
        local = [
            {"repo_digests": [f"reg.test/team/base@{digest(registry.manifest)}"], "layers": ["sha256:" + "1" * 64]}
        ]
        docker = FakeDocker(local=local)
        result = self.fetch(registry, docker, ["reg.test/team/app:v1"])
        self.assertEqual(result.downloaded_bytes, 500)
        self.assertEqual(result.reused_bytes, 4000)
        self.assertNotIn(f"blobs/sha256/{digest(shared)[7:]}", docker.archives[0])

    def test_a_layer_docker_turns_out_to_lack_is_downloaded_and_loaded_again(self):
        shared, own = os.urandom(4000), os.urandom(500)
        registry = FakeRegistry([shared, own], diff_ids=["sha256:" + "1" * 64, "sha256:" + "2" * 64])
        local = [
            {"repo_digests": [f"reg.test/team/base@{digest(registry.manifest)}"], "layers": ["sha256:" + "1" * 64]}
        ]
        docker = FakeDocker(local=local)
        docker.unpack_error = True
        self.fetch(registry, docker, ["reg.test/team/app:v1"])
        self.assertEqual(docker.removed, ["reg.test/team/app:v1"])
        self.assertEqual(len(docker.archives), 2)
        self.assertEqual(docker.archives[1][f"blobs/sha256/{digest(shared)[7:]}"], shared)
        self.assertIn("reg.test/team/app:v1", docker.present)


class InstallFallbackTests(unittest.TestCase):
    def test_install_uses_dockers_pull_when_the_fast_path_fails(self):
        from ctl import service_ops

        with (
            patch("ctl.service_ops.ImageDownloadStore.runtime", return_value=None),
            patch("ctl.service_ops.actions.compose_image_list", return_value=(0, ["ghcr.io/a/b:1"])),
            patch("ctl.service_ops.image_fetch.fetch_images", side_effect=FetchError("boom")),
            patch("ctl.service_ops.actions.compose_pull", return_value=(0, "pulled")) as pull,
        ):
            rc, _output = service_ops._download_images(Path("/unused"), "app", "job", lambda _line: None)
        self.assertEqual(rc, 0)
        pull.assert_called_once()

    def test_install_skips_dockers_pull_after_a_fast_download(self):
        from ctl import service_ops

        with (
            patch("ctl.service_ops.ImageDownloadStore.runtime", return_value=None),
            patch("ctl.service_ops.actions.compose_image_list", return_value=(0, ["ghcr.io/a/b:1"])),
            patch("ctl.service_ops.image_fetch.fetch_images") as fetch,
            patch("ctl.service_ops.actions.compose_pull") as pull,
        ):
            rc, _output = service_ops._download_images(Path("/unused"), "app", "job", lambda _line: None)
        self.assertEqual(rc, 0)
        fetch.assert_called_once()
        pull.assert_not_called()


if __name__ == "__main__":
    unittest.main()
