"""Every image Mu3Lab deploys or builds on is pinned to an immutable digest."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


class ImagePinningTests(unittest.TestCase):
    def test_every_compose_image_is_pinned(self):
        unpinned = []
        for path in sorted([*ROOT.glob("core/**/docker-compose.yml"), *ROOT.glob("apps/**/docker-compose.yml")]):
            services = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("services") or {}
            for name, service in services.items():
                image = str((service or {}).get("image", ""))
                # Locally built images are covered by the pinned base in their Dockerfile.
                if image and "build" not in service and "@sha256:" not in image:
                    unpinned.append(f"{path.relative_to(ROOT)}: {name} uses {image}")
        self.assertEqual(unpinned, [])

    def test_every_dockerfile_base_is_pinned(self):
        unpinned = [
            f"{path.relative_to(ROOT)}: {line}"
            for path in sorted([*ROOT.glob("apps/**/Dockerfile"), *ROOT.glob("core/**/Dockerfile")])
            for line in path.read_text(encoding="utf-8").splitlines()
            if re.match(r"FROM\s", line) and "@sha256:" not in line
        ]
        self.assertEqual(unpinned, [])


if __name__ == "__main__":
    unittest.main()
