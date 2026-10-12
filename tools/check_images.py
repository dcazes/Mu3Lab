"""Check that every pinned image exists and runs on the platform Mu3Lab supports (reviews R25, R30).

Reads every digest-pinned image: in app, connector and platform Compose files,
in Dockerfile ``FROM`` lines, and in Mu3Lab's own code (Kopia, Node). For each
one it asks the registry, without pulling, which platforms the digest provides.
A digest that no longer resolves, or that has no ``linux/amd64`` image, fails.

Needs the network and ``docker buildx``. Run by ``make verify-security`` and in
CI. ARM coverage is reported, not required: Mu3Lab supports x86-64 only.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = "linux/amd64"
PINNED = re.compile(r"[a-z0-9][a-z0-9./_-]*(?::[A-Za-z0-9._-]+)?@sha256:[0-9a-f]{64}")
SOURCES = (
    "apps/*/docker-compose.yml",
    "apps/*/connectors/*/docker-compose.yml",
    "apps/*/connectors/*/Dockerfile",
    "platform/*/docker-compose.yml",
    "platform/*/Dockerfile",
    "ctl/**/*.py",
)


def pinned_images(root: Path = ROOT) -> dict[str, list[str]]:
    """Every pinned image, with the files that name it."""
    found: dict[str, list[str]] = {}
    for pattern in SOURCES:
        for path in sorted(root.glob(pattern)):
            for image in PINNED.findall(path.read_text(encoding="utf-8")):
                found.setdefault(image, []).append(path.relative_to(root).as_posix())
    return found


def platforms(image: str) -> set[str]:
    """The platforms a pinned image provides, read from its registry manifest. Raises RuntimeError."""
    raw = subprocess.run(
        ["docker", "buildx", "imagetools", "inspect", "--raw", image],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if raw.returncode:
        raise RuntimeError(raw.stderr.strip()[-300:] or "the registry did not answer")
    manifest = json.loads(raw.stdout)
    if "manifests" in manifest:
        return {
            f"{item['platform']['os']}/{item['platform']['architecture']}"
            for item in manifest["manifests"]
            if item.get("platform") and item["platform"].get("os") != "unknown"
        }
    # A single-platform image: its config says which.
    config = subprocess.run(
        ["docker", "buildx", "imagetools", "inspect", "--format", "{{json .Image}}", image],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if config.returncode:
        raise RuntimeError(config.stderr.strip()[-300:])
    details = json.loads(config.stdout)
    return {f"{details.get('os')}/{details.get('architecture')}"}


def main() -> int:
    images = pinned_images()
    failures: list[str] = []
    no_arm: list[str] = []

    def check(image: str) -> tuple[str, set[str] | str]:
        try:
            return image, platforms(image)
        except (RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            return image, str(exc)

    with ThreadPoolExecutor(max_workers=8) as pool:
        for image, result in pool.map(check, sorted(images)):
            where = ", ".join(images[image])
            if isinstance(result, str):
                failures.append(f"{image} ({where}) could not be read: {result}")
            elif REQUIRED not in result:
                failures.append(f"{image} ({where}) has no {REQUIRED} image; it has {sorted(result)}")
            elif "linux/arm64" not in result:
                no_arm.append(f"{image} ({where})")
    for failure in failures:
        print(f"FAIL {failure}")
    for item in no_arm:
        print(f"note: no linux/arm64 image (fine while Mu3Lab is x86-64 only): {item}")
    print(f"{len(images) - len(failures)} of {len(images)} pinned images provide {REQUIRED}.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
