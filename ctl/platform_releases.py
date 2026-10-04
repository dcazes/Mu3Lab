"""Verified assets from tagged releases; development branches build locally."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

import httpx
import yaml

from ctl.bootstrap import stamps

TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
REPOSITORY = "https://github.com/dcazes/Mu3Lab"


def exact_tag(root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), "tag", "--points-at", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    versions = [
        (tuple(int(n) for n in match.groups()), value)
        for value in result.stdout.splitlines()
        if (match := TAG.fullmatch(value))
    ]
    return max(versions)[1] if result.returncode == 0 and versions else ""


def _download(url: str, target: Path) -> None:
    with httpx.Client(follow_redirects=True, timeout=120) as client, client.stream("GET", url) as response:
        response.raise_for_status()
        with target.open("wb") as handle:
            for chunk in response.iter_bytes():
                handle.write(chunk)


def assets(root: Path, tag: str) -> Path:
    """Verify every consumed asset against that release's checksums."""
    if not TAG.fullmatch(tag):
        raise ValueError("Only normal versioned releases can be installed automatically.")
    folder = root / ".tools" / "releases" / tag
    folder.mkdir(parents=True, exist_ok=True)
    base = f"{REPOSITORY}/releases/download/{tag}"
    names = [f"mu3lab-dashboard-{tag}.tar.gz", "release-images.json"]
    _download(f"{base}/SHA256SUMS", folder / "SHA256SUMS")
    checksums = {}
    for line in (folder / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split(maxsplit=1)
        checksums[name.lstrip("*")] = digest
    for name in names:
        if name not in checksums:
            raise ValueError(f"The release is missing the checksum for {name}.")
        target = folder / name
        if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != checksums[name]:
            _download(f"{base}/{name}", target)
        if hashlib.sha256(target.read_bytes()).hexdigest() != checksums[name]:
            target.unlink(missing_ok=True)
            raise ValueError("Release verification failed; the downloaded files were not installed.")
    return folder


def install_dashboard(root: Path, tag: str) -> None:
    folder = assets(root, tag)
    with tempfile.TemporaryDirectory(dir=root / "dashboard", prefix=".release-") as temporary:
        stage = Path(temporary)
        with tarfile.open(folder / f"mu3lab-dashboard-{tag}.tar.gz") as archive:
            archive.extractall(stage, filter="data")
        source = stage / "dist"
        if not (source / "index.html").is_file() or not (source / "assets").is_dir():
            raise ValueError("The release does not contain a complete dashboard.")
        if stamps.read(source / ".mu3lab-build.sha256") != stamps.dashboard_digest(root):
            raise ValueError("The dashboard release does not match this checkout.")
        destination = root / "dashboard/dist"
        previous = root / "dashboard/.previous-dist"
        shutil.rmtree(previous, ignore_errors=True)
        if destination.exists():
            destination.rename(previous)
        try:
            source.rename(destination)
        except OSError:
            if previous.exists():
                previous.rename(destination)
            raise
        shutil.rmtree(previous, ignore_errors=True)


def pin_compose(root: Path, source: Path, target: Path) -> None:
    tag = exact_tag(root)
    if not tag:
        return
    manifest = root / ".tools/releases" / tag / "release-images.json"
    if not manifest.is_file():
        raise ValueError("This release's images are missing. Run the installer to verify its release assets.")
    images = json.loads(manifest.read_text())
    values = yaml.safe_load(target.read_text())
    for name, service in values.get("services", {}).items():
        if "build" not in service:
            continue
        key = f"{source.relative_to(root).as_posix()}#{name}"
        image = images.get(key, "")
        if not re.fullmatch(r"ghcr\.io/dcazes/mu3lab-[a-z0-9-]+@sha256:[0-9a-f]{64}", image):
            raise ValueError("The release lacks a verified image for this connector.")
        service.pop("build")
        service["image"] = image
    target.write_text(yaml.safe_dump(values, sort_keys=False))
