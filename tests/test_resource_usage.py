"""Memory and disk per app for the Home and System charts."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ctl import resource_usage
from ctl.registry import load
from ctl.runtime import RuntimePaths

ROOT = Path(__file__).resolve().parents[1]
PATHS = RuntimePaths(Path("/srv/mu3lab"))


def result(stdout: str, returncode: int = 0) -> SimpleNamespace:
    return SimpleNamespace(stdout=stdout, returncode=returncode)


def cgroup(folder: Path, current: int, inactive: int) -> None:
    folder.mkdir(parents=True)
    (folder / "memory.current").write_text(f"{current}\n")
    (folder / "memory.stat").write_text(f"anon {current}\ninactive_file {inactive}\nactive_file 0\n")


class OwnerTests(unittest.TestCase):
    def test_projects_belong_to_their_app_core_or_removed(self):
        owner = resource_usage.owners(load(), ROOT, PATHS)
        self.assertEqual(owner("/srv/mu3lab/projects/immich"), "immich")
        # A connector's project belongs to its app.
        self.assertEqual(owner("/srv/mu3lab/projects/mcp-grocy-community"), "grocy")
        # Sign-in, the proxy and the vault, plus the tool gateway and relay, are Mu3Lab's core.
        self.assertEqual(owner(str(ROOT / "apps" / "authentik")), "core")
        self.assertEqual(owner("/srv/mu3lab/projects/tailnet-relay"), "core")
        self.assertEqual(owner(str(ROOT / "platform" / "tailnet-relay")), "core")
        # Apps no longer in the catalog.
        self.assertEqual(owner("/srv/mu3lab/projects/unsloth"), "removed")
        # The owner's own containers are not Mu3Lab's to count.
        self.assertEqual(owner("/home/me/my-stack"), "")
        self.assertEqual(owner(""), "")


class MemoryTests(unittest.TestCase):
    def test_reads_each_container_cgroup_less_inactive_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            cgroup(base / "system.slice" / "docker-aaa.scope", 300, 100)
            # Docker's own cgroupfs layout is read too.
            cgroup(base / "docker" / "bbb", 50, 0)
            listing = (
                "aaa\tmu3lab-immich-server-1\t/srv/mu3lab/projects/immich\n"
                f"bbb\tmu3lab-ingress-caddy-1\t{ROOT / 'apps' / 'ingress'}\n"
                "ccc\tgone\t/srv/mu3lab/projects/immich\n"
                "ddd\tmine\t/home/me/my-stack\n"
            )
            cgroup(base / "system.slice" / "docker-ddd.scope", 999, 0)
            with patch("ctl.resource_usage._own_services", return_value=7):
                by_name, by_owner = resource_usage.memory(
                    load(), ROOT, run=lambda *a, **k: result(listing), base=base, paths=PATHS
                )
        self.assertEqual(by_name, {"mu3lab-immich-server-1": 200, "mu3lab-ingress-caddy-1": 50, "mine": 999})
        # Mu3Lab's own services count as core.
        self.assertEqual(by_owner, {"immich": 200, "core": 57})

    def test_docker_unreachable_reports_nothing(self):
        with patch("ctl.resource_usage._own_services", return_value=0):
            self.assertEqual(
                resource_usage.memory(load(), ROOT, run=lambda *a, **k: result("", 1), paths=PATHS), ({}, {})
            )


class DiskTests(unittest.TestCase):
    def test_sizes_use_dockers_decimal_units(self):
        self.assertEqual(resource_usage._size("1.5GB"), 1_500_000_000)
        self.assertEqual(resource_usage._size("2B"), 2)
        self.assertEqual(resource_usage._size("4.1kB"), 4100)
        self.assertEqual(resource_usage._size("N/A"), 0)

    def test_images_match_by_digest_and_shared_layers_count_once(self):
        owner = resource_usage.owners(load(), ROOT, PATHS)
        summary = [{"Type": "Images", "Size": "1.57GB"}, {"Type": "Containers", "Size": "0B"}]
        verbose = {
            "Images": [
                # Two apps' images share 400 MB; Docker's total counts it once.
                {
                    "ID": "sha256:aaa",
                    "Repository": "<none>",
                    "Tag": "<none>",
                    "UniqueSize": "600MB",
                    "SharedSize": "400MB",
                },
                {
                    "ID": "sha256:bbb",
                    "Repository": "outlinewiki/outline",
                    "Tag": "1",
                    "UniqueSize": "300MB",
                    "SharedSize": "400MB",
                },
                {"ID": "sha256:ccc", "Repository": "old", "Tag": "1", "UniqueSize": "200MB", "SharedSize": "0B"},
                # Not Mu3Lab's: the owner pulled it.
                {"ID": "sha256:ddd", "Repository": "mine", "Tag": "1", "UniqueSize": "70MB", "SharedSize": "0B"},
            ],
            "Containers": [
                {
                    "Image": "ghcr.io/immich-app/immich-server:v3@sha256:aaa",
                    "Labels": "com.docker.compose.project=mu3lab-immich,"
                    "com.docker.compose.project.working_dir=/srv/mu3lab/projects/immich",
                    "Size": "1MB (virtual 2GB)",
                },
                {
                    "Image": "docker.io/outlinewiki/outline:1",
                    "Labels": "com.docker.compose.project.working_dir=/srv/mu3lab/projects/outline,"
                    "com.docker.compose.project=mu3lab-outline",
                    "Size": "0B",
                },
            ],
            "Volumes": [{"Labels": "com.docker.compose.project=mu3lab-outline", "Size": "50MB"}],
        }

        def run(argv, **_kwargs):
            return result(json.dumps(verbose) if "-v" in argv else "\n".join(json.dumps(row) for row in summary))

        usage = resource_usage.docker_usage(owner, {"sha256:ccc"}, run)
        self.assertEqual(usage["immich"], {"data": 0, "program": 1_000_000 + 600_000_000 + 200_000_000})
        self.assertEqual(usage["outline"], {"data": 50_000_000, "program": 300_000_000 + 200_000_000})
        # Nothing runs this image.
        self.assertEqual(usage["unused"], {"data": 0, "program": 200_000_000})
        self.assertNotIn("", usage)

    def test_folders_go_to_their_app_and_unknown_data_to_removed(self):
        du = "1\tdata/immich\n2\tdata/unsloth-studio\n3\tmedia/roms\n4\tstate\n5\tdata/authentik\n"

        def run(argv, **_kwargs):
            return result(du if argv[1] == "run" else "", 0 if argv[1] == "run" else 1)

        with tempfile.TemporaryDirectory() as tmp:
            paths = RuntimePaths(Path(tmp))
            with (
                patch("ctl.lifecycle.uninstall._cleanup_image", return_value="caddy"),
                patch("ctl.disk_cleanup.reclaimable", return_value=set()),
            ):
                measured = resource_usage.measure_disk(load(), ROOT, paths, run=run)
        owners = measured["owners"]
        self.assertEqual(owners["immich"], {"data": 1024, "program": 0})
        self.assertEqual(owners["removed"], {"data": 2048, "program": 0})
        self.assertEqual(owners["romm"], {"data": 3072, "program": 0})
        self.assertEqual(owners["core"], {"data": 4096 + 5120, "program": 0})

    def test_installs_updates_and_removals_ask_for_a_new_measurement(self):
        self.assertTrue(resource_usage.touches_disk({"action": "install"}))
        self.assertTrue(resource_usage.touches_disk({"action": "uninstall_delete_data"}))
        self.assertFalse(resource_usage.touches_disk({"action": "start"}))


if __name__ == "__main__":
    unittest.main()
