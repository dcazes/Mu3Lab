"""Approve a tested upstream release of any app, for every Mu3Lab install.

    make check-updates                       # which apps have a newer upstream release
    make approve APP=mealie VERSION=v3.23.0  # pin it, digest and all

``approve`` looks up the release's images in their registries (metadata
only, nothing is downloaded), pins each to its digest in
``apps/<app>/docker-compose.yml``, and records the version as ``version`` in
``apps/<app>/app.yaml``. Nothing is committed: test
the update from your own dashboard (Apps -> the app -> Advanced -> Updates),
then commit and push. Each install offers the release once it pulls Mu3Lab.
Catalog and core apps update from the dashboard; foundation apps (sign-in,
passwords, the web front door) move when ./install.sh runs.

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

_RELEASE_TAG = re.compile(r"^[A-Za-z0-9._+@-]{1,80}$")


def _bare(version: str) -> str:
    """The release number alone: ``n8n@2.48.5`` and ``v2.48.5`` are both ``2.48.5``."""
    return version.strip().rpartition("@")[2].removeprefix("v")


def _version_pattern(version: str) -> re.Pattern[str]:
    # Whole version only: 3.2.2 must not match inside 13.2.2 or 3.2.21.
    return re.compile(r"(?<![0-9.])" + re.escape(version) + r"(?![0-9]|\.[0-9])")


def plan(service: Service, version: str, overrides: dict[str, str]) -> dict[str, str]:
    """New image references (no digest yet) by Compose service, for ``version``."""
    current = app_releases.approved(service, ROOT)
    old = _bare(current.version)
    new = _bare(version)
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


def overlay_plan(service: Service, version: str, moved: dict[str, str]) -> dict[Path, dict[str, str]]:
    """New references for override files' images (GPU builds, options) of the repositories that move.

    ``docker-compose.nvidia.yml`` swaps in a CUDA build of the same image; it must move with
    the main one, even when it already lags behind (``v3.3.5-cuda`` beside ``v3.3.6``).
    """
    current = app_releases.approved(service, ROOT)
    old = _bare(current.version)
    new = _bare(version)
    pattern = _version_pattern(old)
    repositories = {app_releases.split_image(reference)[0] for reference in moved.values()}
    plans: dict[Path, dict[str, str]] = {}
    for overlay in sorted(service.compose_path(ROOT).glob("docker-compose.*.yml")):
        for image in app_releases.compose_images(overlay).values():
            repository, tag = app_releases.split_image(image)
            if repository not in repositories or not tag:
                continue
            if old and pattern.search(tag):
                tag = pattern.sub(new, tag)
            else:
                tag, count = re.subn(r"^(v?)\d+(?:\.\d+)+", lambda m: m.group(1) + new, tag, count=1)
                if not count:
                    raise SystemExit(f"{overlay.name}: can't tell which release {image} is; edit it by hand.")
            plans.setdefault(overlay, {})[image] = f"{repository}:{tag}"
    return plans


def resolve_digest(reference: str) -> str:
    """Pin ``reference`` to the digest its tag names now, reading only registry metadata.

    The digest is the multi-platform index's, the same one ``docker pull``
    records, so every machine gets the image for its own CPU.
    """
    print(f"  resolving {reference}")
    # Mu3Lab's own Docker runner works before a fresh login picks up the docker group.
    rc, output = actions.docker_cmd(
        ["docker", "buildx", "imagetools", "inspect", "--format", "{{json .Manifest}}", reference],
        lambda _line: None,
        timeout=120,
    )
    try:
        digest = str(json.loads(output).get("digest", "")) if not rc else ""
    except ValueError:
        digest = ""
    if not digest.startswith("sha256:"):
        raise SystemExit(f"{reference} could not be found in its registry; is the tag published?\n{output[-400:]}")
    repository, tag = app_releases.split_image(reference)
    # "latest" moves; the registry refuses it even beside a digest, which is the real pin.
    return f"{repository}@{digest}" if tag == "latest" else f"{reference}@{digest}"


def rewrite(service: Service, version: str, pinned: dict[str, str]) -> None:
    current = app_releases.approved(service, ROOT)
    compose = service.compose_path(ROOT) / "docker-compose.yml"
    text = compose.read_text(encoding="utf-8")
    for name, image in pinned.items():
        text = re.sub(rf"(^\s*image:\s*){re.escape(current.images[name])}\s*$", rf"\g<1>{image}", text, flags=re.M)
    compose.write_text(text, encoding="utf-8")

    manifest = service.compose_path(ROOT) / "app.yaml"
    text, count = re.subn(
        r"^version:.*$", f'version: "{version}"', manifest.read_text(encoding="utf-8"), count=1, flags=re.M
    )
    if not count:
        raise SystemExit(f"{manifest.relative_to(ROOT)} has no version line.")
    manifest.write_text(text, encoding="utf-8")


def approve(app: str, version: str, overrides: dict[str, str]) -> None:
    if not _RELEASE_TAG.fullmatch(version):
        raise SystemExit(f"{version!r} is not a release tag.")
    service = load().get(app)
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
    # Several containers often share one image (Nextcloud's app and its cron job).
    resolved = {reference: resolve_digest(reference) for reference in dict.fromkeys(moved.values())}
    pinned = {name: resolved[reference] for name, reference in moved.items()}
    overlays = overlay_plan(service, version, moved)
    for references in overlays.values():
        for reference in references.values():
            if reference not in resolved:
                resolved[reference] = resolve_digest(reference)
    rewrite(service, version, pinned)
    for name, image in pinned.items():
        print(f"  {name}: {image}")
    for overlay, references in overlays.items():
        text = overlay.read_text(encoding="utf-8")
        for old_image, reference in references.items():
            text = re.sub(
                rf"(^\s*image:\s*){re.escape(old_image)}\s*$", rf"\g<1>{resolved[reference]}", text, flags=re.M
            )
            print(f"  {overlay.name}: {resolved[reference]}")
        overlay.write_text(text, encoding="utf-8")
    where = (
        "run ./install.sh (foundation apps move only with the installer)"
        if not app_releases.updatable(service)
        else f"open your dashboard, go to Apps > {service.name} > Advanced and run the update"
    )
    print(
        f"\nNext: {where}. If it works, commit and push:\n"
        f'  git commit -am "Approve {service.name} {version}" && git push'
    )


def check() -> None:
    from ctl.releases import latest

    for service in load().services:
        repository = str(service.update.get("repository", ""))
        if not repository:
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
