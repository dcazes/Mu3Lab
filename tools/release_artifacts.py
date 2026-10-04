"""Release build inventory and checksummed dashboard/image assets."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tarfile
from pathlib import Path

import yaml

from ctl.bootstrap import stamps
from ctl.platform_releases import TAG

ROOT = Path(__file__).resolve().parents[1]


def inventory(root: Path = ROOT) -> list[dict[str, str]]:
    entries = []
    for path in sorted(
        [*root.glob("apps/*/connectors/*/docker-compose.yml"), *root.glob("platform/*/docker-compose.yml")]
    ):
        for name, service in yaml.safe_load(path.read_text())["services"].items():
            if "build" not in service:
                continue
            entries.append(
                {
                    "name": path.parent.name,
                    "service": name,
                    "context": path.parent.relative_to(root).as_posix(),
                    "compose": path.relative_to(root).as_posix(),
                    "image": "ghcr.io/dcazes/mu3lab-" + path.parent.name,
                }
            )
    if len({item["name"] for item in entries}) != len(entries):
        raise ValueError("Release image names must be unique.")
    return entries


def package(root: Path, tag: str, digests: Path, output: Path) -> None:
    if not TAG.fullmatch(tag):
        raise ValueError("Only normal vX.Y.Z releases are published.")
    images = {}
    for item in inventory(root):
        digest = (digests / (item["name"] + ".txt")).read_text().strip()
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise ValueError("An image digest is missing or invalid.")
        images[item["compose"] + "#" + item["service"]] = item["image"] + "@" + digest
    output.mkdir(parents=True, exist_ok=True)
    stamps.write(root / stamps.BUILD_STAMP, stamps.dashboard_digest(root))
    archive = output / f"mu3lab-dashboard-{tag}.tar.gz"
    with tarfile.open(archive, "w:gz") as handle:
        handle.add(root / "dashboard/dist", arcname="dist")
    manifest = output / "release-images.json"
    manifest.write_text(json.dumps(images, indent=2) + "\n")
    (output / "SHA256SUMS").write_text(
        "".join(hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.name + "\n" for path in [archive, manifest])
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task", choices=["matrix", "package"])
    parser.add_argument("--tag", default="")
    parser.add_argument("--digests", type=Path, default=ROOT / ".tools/release-digests")
    parser.add_argument("--output", type=Path, default=ROOT / ".tools/release-assets")
    args = parser.parse_args()
    if args.task == "matrix":
        print(json.dumps({"include": inventory()}))
    else:
        package(ROOT, args.tag, args.digests, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
