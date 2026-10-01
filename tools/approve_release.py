"""Approve a tested upstream release of a catalog app, for every Mu3Lab install.

    make check-updates                       # which apps have a newer upstream release
    make approve APP=mealie VERSION=v3.23.0  # pin it, digest and all

``approve`` downloads the release's images, pins each to its digest in
``apps/<app>/docker-compose.yml``, and records the version as
``update.approved_version`` in ``services.yaml``. Nothing is committed: test
the update from your own dashboard (Apps -> the app -> Advanced -> Updates),
then commit and push. Each install offers the release once it pulls Mu3Lab.

Only the app's own images move, matched by the approved version in their
tag. Databases and helpers keep their pins; move one deliberately with
``IMAGES="service=registry/name:tag"`` (repeatable, space-separated), which
also covers apps whose image tags do not follow their release numbers.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ctl import actions  # noqa: E402
from ctl.lifecycle import app_releases  # noqa: E402
from ctl.registry import Service, load  # noqa: E402

_RELEASE_TAG = re.compile(r"^[A-Za-z0-9._+-]{1,80}$")
_DIGEST = re.compile(r"@sha256:[0-9a-f]{64}")


def _version_pattern(version: str) -> re.Pattern[str]:
    # Whole version only: 3.2.2 must not match inside 13.2.2 or 3.2.21.
    return re.compile(r"(?<![0-9.])" + re.escape(version) + r"(?![0-9]|\.[0-9])")


def plan(service: Service, version: str, overrides: dict[str, str]) -> dict[str, str]:
    """New image references (no digest yet) by Compose service, for ``version``."""
    current = app_releases.approved(service, ROOT)
    old = current.version.strip().removeprefix("v")
    new = version.strip().removeprefix("v")
    pattern = _version_pattern(old)
    moved = {}
    for name, image in current.images.items():
        repository, tag = app_releases.split_image(image)
        if name in overrides:
            moved[name] = overrides[name]
        elif old and tag and pattern.search(tag):
            moved[name] = f"{repository}:{pattern.sub(new, tag)}"
    unknown = set(overrides) - set(current.images)
    if unknown:
        raise SystemExit(f"{service.id} has no Compose service named {', '.join(sorted(unknown))}.")
    return moved


def pull_and_pin(reference: str) -> str:
    print(f"  pulling {reference}")
    # Mu3Lab's own Docker runner works before a fresh login picks up the docker group.
    rc, output = actions.docker_cmd(["docker", "pull", reference], lambda _line: None, timeout=3600)
    if rc:
        raise SystemExit(f"{reference} could not be downloaded; is the tag published?\n{output[-400:]}")
    rc, output = actions.docker_cmd(
        ["docker", "image", "inspect", "--format", "{{json .RepoDigests}}", reference], lambda _line: None, timeout=30
    )
    repository = app_releases.split_image(reference)[0]
    # Docker Hub images are recorded without their implicit registry prefix.
    short = repository.removeprefix("docker.io/").removeprefix("library/")
    for entry in (json.loads(output) if not rc else None) or []:
        name, _, digest = str(entry).partition("@")
        if name.removeprefix("docker.io/").removeprefix("library/") == short and digest.startswith("sha256:"):
            return f"{reference}@{digest}"
    raise SystemExit(f"{reference} has no registry digest; is it a locally built image?")


def _service_block(text: str, service_id: str) -> tuple[int, int]:
    start = re.search(rf"^  - id: {re.escape(service_id)}\s*$", text, re.MULTILINE)
    if not start:
        raise SystemExit(f"services.yaml has no entry for {service_id}.")
    following = re.compile(r"^  - id: ", re.MULTILINE).search(text, start.end())
    return start.start(), following.start() if following else len(text)


def rewrite(service: Service, version: str, pinned: dict[str, str]) -> None:
    current = app_releases.approved(service, ROOT)
    compose = service.compose_path(ROOT) / "docker-compose.yml"
    text = compose.read_text(encoding="utf-8")
    for name, image in pinned.items():
        text = re.sub(rf"(^\s*image:\s*){re.escape(current.images[name])}\s*$", rf"\g<1>{image}", text, flags=re.M)
    compose.write_text(text, encoding="utf-8")

    registry = ROOT / "services.yaml"
    text = registry.read_text(encoding="utf-8")
    start, end = _service_block(text, service.id)
    block = text[start:end]
    block, count = re.subn(r'(update:\s*\{[^}]*approved_version:\s*)"?[^,}"]+"?', rf'\g<1>"{version}"', block, count=1)
    if not count:
        raise SystemExit(f"services.yaml has no update.approved_version for {service.id}.")
    # The registry's image list mirrors the Compose file, pinned or not.
    for name, image in pinned.items():
        old = current.images[name].split("@", 1)[0]
        new_reference = image.split("@", 1)[0]
        block = re.sub(
            re.escape(old) + r"(" + _DIGEST.pattern + r")?",
            lambda match, image=image, new=new_reference: image if match.group(1) else new,
            block,
        )
    registry.write_text(text[:start] + block + text[end:], encoding="utf-8")


def approve(app: str, version: str, overrides: dict[str, str]) -> None:
    if not _RELEASE_TAG.fullmatch(version):
        raise SystemExit(f"{version!r} is not a release tag.")
    service = load().get(app)
    if service.stage != "optional":
        raise SystemExit(f"{service.name} is part of Mu3Lab itself; edit its pins by hand.")
    current = app_releases.approved(service, ROOT)
    if not overrides and not app_releases.is_newer(version, current.version):
        raise SystemExit(f"{service.name} {current.version} is already approved; {version} is not newer.")
    moved = plan(service, version, overrides)
    if not moved:
        raise SystemExit(
            f"None of {service.name}'s image tags contain {current.version}, so Mu3Lab can't tell which "
            'images make up the release. Name them: IMAGES="service=registry/name:tag".'
        )
    print(f"Approving {service.name} {version} (was {current.version}):")
    # Several containers often share one image (SurfSense's backend and workers).
    resolved = {reference: pull_and_pin(reference) for reference in dict.fromkeys(moved.values())}
    pinned = {name: resolved[reference] for name, reference in moved.items()}
    rewrite(service, version, pinned)
    for name, image in pinned.items():
        print(f"  {name}: {image}")
    print(
        "\nNext: open your dashboard, go to Apps > "
        f"{service.name} > Advanced and run the update. If it works, commit and push:\n"
        f'  git commit -am "Approve {service.name} {version}" && git push'
    )


def check() -> None:
    from ctl.releases import latest

    for service in load().services:
        repository = str(service.update.get("repository", ""))
        if service.stage != "optional" or not repository:
            continue
        approved = str(service.update.get("approved_version", ""))
        try:
            upstream = str(latest(repository)["latest_version"])
        except (ValueError, RuntimeError) as exc:
            print(f"{service.name:<16} {approved:<14} upstream unavailable ({exc})")
            continue
        note = "newer upstream release" if app_releases.is_newer(upstream, approved) else "up to date"
        print(f"{service.name:<16} {approved:<14} {upstream:<14} {note}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="compare each approved release with upstream's latest")
    approving = commands.add_parser("approve", help="approve a tested release of one app")
    approving.add_argument("app")
    approving.add_argument("version")
    approving.add_argument("--image", action="append", default=[], metavar="SERVICE=REFERENCE")
    args = parser.parse_args(argv)
    if args.command == "check":
        check()
        return
    overrides = {}
    for item in args.image:
        name, _, reference = item.partition("=")
        if not name or not reference or "@" in reference:
            raise SystemExit(f"--image {item!r}: use SERVICE=registry/name:tag (the digest is added for you).")
        overrides[name] = reference
    approve(args.app, args.version, overrides)


if __name__ == "__main__":
    main()
