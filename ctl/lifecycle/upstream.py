"""Newer releases straight from each app's makers, offered once Mu3Lab's own checks pass.

Every installed app follows the release Mu3Lab approves (``ctl.lifecycle.app_releases``). Between
two Mu3Lab releases its makers often publish more, so each server also looks for itself, about
once a day, and offers a newer release without anyone at Mu3Lab reviewing it first, when:

* it is the project's latest release on GitHub (never a draft or a pre-release) and newer than
  both what this server runs and what Mu3Lab approves;
* it is not a new major version (for 0.x projects, a new minor one);
* none of its release notes, or those of the releases skipped on the way, warn about breaking
  changes, required steps or settings to change;
* the project did not change its own main Compose or example settings files between the two;
* its images exist for this computer and still run as the same user, with the same ports,
  volumes and entrypoint.

Only the app's own images move, the way ``make approve`` moves them: databases and helpers keep
Mu3Lab's pins. A release that passes is offered like any other update and goes through the same
backed-up update, which also checks sign-in, chat connections and Home widgets afterwards and puts
everything back if one that worked before stopped working (``ctl.lifecycle.update_checks``). A
release that fails there is never offered again on this server.

Nothing here downloads an image, changes an app or writes outside Mu3Lab's own records: it reads
GitHub and the image registries, and remembers what it found (``records`` scope ``SCOPE``).
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from ctl.image_fetch import FetchError, Registry, parse_ref, plan_image
from ctl.lifecycle import app_releases
from ctl.lifecycle.app_releases import Release
from ctl.registry import Service
from ctl.runtime import RuntimePaths
from ctl.store import records

Log = Callable[[str], None]
SCOPE = "upstream-releases"
# Each app is looked at about once a day; the worker asks hourly, so a check GitHub refused
# (its limit is 60 requests an hour without a token) is simply tried again an hour later.
CHECK_SECONDS = 24 * 3600
READY, HELD, CURRENT, UNAVAILABLE = "ready", "held", "current", "unavailable"

# Release notes that ask something of whoever upgrades, or warn that something changed.
RISK = re.compile(
    r"breaking changes?|\bbreaking:|(?-i:BREAKING)|\[!(warning|important|caution)\]|:warning:|⚠|action required|"
    r"upgrade notes?|(before|when) upgrading|migration (guide|steps?|required)|"
    r"manual(ly)? (step|migration|intervention|action)|"
    r"(need|needs|have|has) to (adjust|change|update|set|add|remove|migrate)|"
    r"must (now )?(set|change|update|add|remove|migrate)|no longer (supported|works|accepts|reads|starts)|"
    r"(is|are|has been|have been|were) removed|(renamed|replaced) (the )?(environment|env|setting|variable|option)|"
    r"back ?up (your|the) (data|database)",
    re.I,
)
# Lines some projects put in every release's notes, which ask nothing new of Mu3Lab.
BOILERPLATE = re.compile(
    r"^.*(update the container image|within an existing docker container|change the branch to \*?\*?master).*$",
    re.I | re.M,
)
# The project's own main deployment files (not its variants for other setups), whose changes
# Mu3Lab's Compose file may need to follow.
DEPLOYMENT_FILE = re.compile(r"(^|/)((docker-)?compose\.ya?ml|\.env\.(example|sample|template)|example\.env)$", re.I)
# Copies for developing, testing or the project's website, not for running it.
NOT_DEPLOYMENT = re.compile(
    r"(^|/)(\.github|\.devcontainer|tests?|e2e|docs?|examples?|charts?|helm|k8s|kubernetes|dev|scripts|www|"
    r"website|site|landing)/",
    re.I,
)
# Image settings that Mu3Lab's Compose file and helpers rely on.
IMAGE_SETTINGS = (
    ("User", "the user it runs as"),
    ("ExposedPorts", "its ports"),
    ("Volumes", "its data folders"),
    ("Entrypoint", "how it starts"),
)
GPU_BUILDS = frozenset({"nvidia", "amd", "intel"})


class CheckFailed(RuntimeError):
    """Upstream could not be read this time; nothing is recorded, and the next pass tries again."""


# --- Versions --------------------------------------------------------------------------------


def _split(version: str) -> tuple[str, str]:
    """(prefix, number): ``n8n@2.48.5`` is (``n8n@``, ``2.48.5``), ``release-5.3.1`` (``release-``, ``5.3.1``)."""
    name, at, rest = version.strip().rpartition("@")
    match = re.match(r"^([^0-9]*)(.*)$", rest)
    lead, number = (match.group(1), match.group(2)) if match else ("", rest)
    return f"{name}{at}{lead}", number


def bare(version: str) -> str:
    """The release number alone: ``n8n@2.48.5``, ``release-5.3.1`` and ``v2.48.5`` all lose their prefix."""
    return _split(version)[1]


def _prefix(version: str) -> str:
    """What comes before the number: ``v``, ``n8n@``, ``release-`` or nothing."""
    return _split(version)[0]


def label(approved_version: str, number: str) -> str:
    """Name a release the way Mu3Lab names this app's releases (``v5.3.1``, not ``release-5.3.1``)."""
    return _prefix(approved_version) + number


def _parts(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", bare(version)))


def major(version: str) -> tuple[int, ...]:
    """What a new major version changes: the first number, or the first two while it is 0."""
    parts = _parts(version)
    return parts[:2] if parts[:1] == (0,) else parts[:1]


def _version_pattern(number: str) -> re.Pattern[str]:
    # Whole version only: 3.2.2 must not match inside 13.2.2 or 3.2.21.
    return re.compile(r"(?<![0-9.])" + re.escape(number) + r"(?![0-9]|\.[0-9])")


# --- Images ----------------------------------------------------------------------------------


def _client() -> httpx.Client:
    return httpx.Client(
        timeout=httpx.Timeout(30.0, connect=15.0),
        follow_redirects=False,
        headers={"User-Agent": "Mu3Lab release check"},
    )


def _registry_tags(registry: Registry) -> list[str]:
    tags: list[str] = []
    url = f"{registry.base}/tags/list?n=1000"
    for _page in range(50):
        response = registry._get(url)
        if response.status_code != 200:
            raise FetchError(f"{registry.ref.repository}'s tags returned HTTP {response.status_code}")
        tags += [str(tag) for tag in (response.json().get("tags") or [])]
        found = re.search(r'<([^>]+)>;\s*rel="?next"?', response.headers.get("link", ""))
        if not found:
            return tags
        url = urllib.parse.urljoin(f"https://{registry.ref.registry}/", found.group(1))
    return tags


def new_tag(client: httpx.Client, image: str, rule: str, old: str, new: str) -> str:
    """The tag of ``image``'s repository for release ``new`` (numbers only), or "" when this image stays.

    Without a rule, the image moves when its tag holds the approved release's number, which is
    replaced (``v3.3.6-cuda`` becomes ``v3.4.2-cuda``). A rule from the manifest
    (``upstream_tags``) names the tag itself: ``{version}`` is the new number and ``*`` a build
    counter, the highest of which is taken (LinuxServer's ``3.1.0.5799-ls170``).
    """
    repository, tag = app_releases.split_image(image)
    if rule:
        if "*" not in rule:
            return rule.replace("{version}", new)
        wanted = re.compile(
            "^"
            + re.escape(rule).replace(re.escape("{version}"), re.escape(new)).replace(re.escape("*"), r"(\d+)")
            + "$"
        )
        builds = [
            (int(found.group(1)), candidate)
            for candidate in _registry_tags(Registry(client, parse_ref(f"{repository}:latest")))
            if (found := wanted.match(candidate))
        ]
        if not builds:
            raise CheckFailed(f"{repository} has no image for {new} yet.")
        return max(builds)[1]
    pattern = _version_pattern(old)
    return pattern.sub(new, tag) if old and tag and pattern.search(tag) else ""


def _resolve(client: httpx.Client, reference: str) -> tuple[str, dict[str, Any]]:
    """``reference`` pinned to its digest, and its image settings for this computer."""
    ref = parse_ref(reference)
    registry = Registry(client, ref)
    _body, _media, digest = registry.manifest(ref.reference)
    plan = plan_image(registry, parse_ref(f"{reference.split('@')[0]}@{digest}"))
    settings = json.loads(plan.config).get("config") or {}
    repository, tag = app_releases.split_image(reference)
    return f"{repository}:{tag}@{digest}", settings


def _settings(client: httpx.Client, image: str) -> dict[str, Any]:
    ref = parse_ref(image)
    return json.loads(plan_image(Registry(client, ref), ref).config).get("config") or {}


def _shape(value: Any) -> Any:
    """Compare ports and volumes by name; the rest as they are."""
    return sorted(value) if isinstance(value, dict) else value or None


# --- GitHub ----------------------------------------------------------------------------------


def _github(path: str) -> Any:
    from ctl.releases import _fetch

    try:
        return _fetch(f"https://api.github.com/repos/{path}")
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 429):
            raise CheckFailed("GitHub asked Mu3Lab to wait; it tries again within the hour.") from exc
        raise CheckFailed(f"GitHub answered HTTP {exc.code}.") from exc
    except (OSError, ValueError) as exc:
        raise CheckFailed("GitHub could not be reached.") from exc


def _raw(repository: str, tag: str, path: str) -> bytes | None:
    """A file of the project at ``tag`` (None when it has no such file); not counted against GitHub's limit."""
    url = f"https://raw.githubusercontent.com/{repository}/{urllib.parse.quote(tag)}/{urllib.parse.quote(path)}"
    request = urllib.request.Request(url, headers={"User-Agent": "Mu3Lab release check"})
    try:
        with urllib.request.urlopen(request, timeout=20) as reply:
            return bytes(reply.read(1_000_001))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise CheckFailed(f"GitHub's files answered HTTP {exc.code}.") from exc
    except OSError as exc:
        raise CheckFailed("GitHub's files could not be reached.") from exc


def _notes_between(repository: str, base: str, tag: str) -> list[tuple[str, str]]:
    """(tag, notes) of every final release after ``base`` up to ``tag``, newest first."""
    found: list[tuple[str, str]] = []
    reached = False
    base_parts, tag_parts = _parts(base), _parts(tag)
    for release in _github(f"{repository}/releases?per_page=100") or []:
        if not isinstance(release, dict) or release.get("draft") or release.get("prerelease"):
            continue
        name = str(release.get("tag_name") or "")
        parts = _parts(name)
        if parts == base_parts:
            reached = True
            continue
        # Projects that maintain older series publish those too (LiteLLM 1.109.5 after 1.111.0).
        if base_parts < parts <= tag_parts:
            found.append((name, str(release.get("body") or "")))
    if not reached and not found:
        raise CheckFailed("GitHub did not list the releases in between.")
    return found


def _risky_notes(notes: list[tuple[str, str]]) -> list[str]:
    reasons = []
    for name, body in notes:
        body = BOILERPLATE.sub("", body)
        hit = RISK.search(body)
        if hit:
            start = body.rfind("\n", 0, hit.start()) + 1
            end = body.find("\n", hit.end())
            line = re.sub(r"[*_`#>\[\]!]+", "", body[start : end if end != -1 else None]).strip()
            reasons.append(f"The {name} release notes say: “{line[:160]}”")
    return reasons


def _deployment_changes(repository: str, base: str, tag: str) -> list[str]:
    tree = _github(f"{repository}/git/trees/{urllib.parse.quote(tag)}?recursive=1") or {}
    paths = [
        str(item.get("path"))
        for item in tree.get("tree") or []
        if item.get("type") == "blob"
        and DEPLOYMENT_FILE.search(str(item.get("path")))
        and not NOT_DEPLOYMENT.search(str(item.get("path")))
    ]
    changed = [path for path in paths[:12] if _raw(repository, tag, path) != _raw(repository, base, path)]
    return [f"{name} changed in {tag}; Mu3Lab's setup may need to follow it." for name in changed]


# --- Discovery -------------------------------------------------------------------------------


def eligible(service: Service) -> bool:
    """Apps the dashboard updates, with a GitHub project to read releases from."""
    return app_releases.updatable(service) and bool(service.update.get("repository"))


def record(service_id: str, paths: RuntimePaths | None = None) -> dict[str, Any]:
    return records.get(SCOPE, service_id, paths or RuntimePaths())


def _variants(service: Service, root: Path) -> list[str]:
    """The CPU build, then each GPU build the app has an override for."""
    folder = service.compose_path(root)
    builds = sorted(
        path.name.split(".")[1] for path in folder.glob("docker-compose.*.yml") if path.name.split(".")[1] in GPU_BUILDS
    )
    return ["cpu", *builds]


def _unavailable(service: Service, failed: dict[str, str], paths: RuntimePaths) -> dict[str, Any]:
    # SearXNG publishes images but no GitHub releases: it stays on Mu3Lab's pins.
    result = {
        "checked_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "state": UNAVAILABLE,
        "tag": "",
        "version": "",
        "release_url": "",
        "failed": failed,
        "reasons": [f"{service.name} publishes no releases on GitHub, so it follows Mu3Lab's updates only."],
        "images": {},
    }
    records.put(SCOPE, service.id, result, paths)
    return result


def check(
    service: Service, root: Path, log: Log, paths: RuntimePaths | None = None, *, force: bool = False
) -> dict[str, Any]:
    """Look for a newer release of ``service`` and screen it; the record the dashboard shows.

    Raises ``CheckFailed`` when GitHub or a registry could not answer; the earlier record stays.
    """
    from ctl.releases import latest

    paths = paths or RuntimePaths()
    kept = record(service.id, paths)
    failed = dict(kept.get("failed") or {})
    repository = str(service.update["repository"])
    installed = app_releases.installed(service, root, paths)
    approved = app_releases.approved(service, root)
    try:
        found = latest(repository)
    except (RuntimeError, ValueError) as exc:
        if "no final release" in str(exc):
            return _unavailable(service, failed, paths)
        raise CheckFailed(str(exc)) from exc
    tag = str(found["latest_version"])
    number = bare(tag)
    version = label(approved.version, number)
    now = datetime.now(UTC).isoformat(timespec="seconds")
    base = installed.version if installed else approved.version
    note: dict[str, Any] = {
        "checked_at": now,
        "tag": tag,
        "version": version,
        "release_url": str(found.get("release_url") or ""),
        "failed": failed,
    }
    if not app_releases.is_newer(version, base) or not app_releases.is_newer(version, approved.version):
        result = {**note, "state": CURRENT, "reasons": [], "images": {}}
        records.put(SCOPE, service.id, result, paths)
        return result
    if not force and kept.get("tag") == tag and kept.get("state") in (READY, HELD):
        # Already screened; images keep the digests checked then.
        result = {**kept, "checked_at": now, "failed": failed}
        records.put(SCOPE, service.id, result, paths)
        return result
    if version in failed:
        result = {**note, "state": HELD, "reasons": [failed[version]], "images": {}}
        records.put(SCOPE, service.id, result, paths)
        return result
    log(f"{service.name} {version} is out (this server runs {base}); checking it.")
    reasons: list[str] = []
    if major(version) != major(base):
        reasons.append(f"{version} is a new major version, which often needs changes; Mu3Lab reviews those first.")
    base_tag = _prefix(tag) + bare(base)
    reasons += _risky_notes(_notes_between(repository, base_tag, tag))
    reasons += _deployment_changes(repository, base_tag, tag)
    images = _images(service, root, installed, approved, number, reasons)
    result = {**note, "state": HELD if reasons else READY, "reasons": list(dict.fromkeys(reasons)), "images": images}
    records.put(SCOPE, service.id, result, paths)
    log(
        f"{service.name} {version} passed Mu3Lab's checks; its update is ready."
        if not reasons
        else f"{service.name} {version} waits for Mu3Lab's review: {reasons[0]}"
    )
    return result


def _images(
    service: Service, root: Path, installed: Release | None, approved: Release, number: str, reasons: list[str]
) -> dict[str, dict[str, str]]:
    """The new release's images for each build, pinned by digest; adds to ``reasons`` what speaks against them."""
    rules = dict(service.manifest.upstream_tags)
    old = bare(approved.version)
    images: dict[str, dict[str, str]] = {}
    moving: set[str] = set()  # repositories whose images move, from the CPU build (checked first)
    with _client() as client:
        for variant in _variants(service, root):
            pinned = app_releases.approved(service, root, variant)
            moved: dict[str, str] = {}
            for name, image in pinned.images.items():
                repository, tag_was = app_releases.split_image(image)
                try:
                    tag_now = new_tag(client, image, rules.get(name, ""), old, number)
                except FetchError as exc:
                    raise CheckFailed(f"{service.name}'s images could not be listed: {exc}") from exc
                if not tag_now and repository in moving and tag_was:
                    # A GPU build of a moving image whose tag fell behind (v3.3.5-cuda beside v3.3.6).
                    tag_now = re.sub(r"^(v?)\d+(?:\.\d+)+", lambda m: m.group(1) + number, tag_was, count=1)
                    tag_now = "" if tag_now == tag_was else tag_now
                if not tag_now:
                    continue
                if variant == "cpu":
                    moving.add(repository)
                try:
                    reference, settings = _resolve(client, f"{repository}:{tag_now}")
                except FetchError as exc:
                    reasons.append(f"{repository}:{tag_now} is not published for this computer ({exc}).")
                    continue
                # Compared with what this server runs now, else Mu3Lab's pin.
                current = (installed.images.get(name) if installed and installed.variant == variant else "") or image
                try:
                    before = _settings(client, current)
                except FetchError:
                    before = {}
                for key, what in IMAGE_SETTINGS:
                    if before and _shape(before.get(key)) != _shape(settings.get(key)):
                        reasons.append(f"{name}'s new image changes {what}.")
                moved[name] = reference
            if not moved and variant == "cpu":
                reasons.append(f"None of {service.name}'s images could be matched to {number}.")
            if moved:
                images[variant] = {**pinned.images, **moved}
    return images


def check_due(registry, root: Path, log: Log, paths: RuntimePaths | None = None) -> int:
    """Check each installed app not looked at for a day; the number checked. Stops when GitHub asks to wait."""
    paths = paths or RuntimePaths()
    checked = 0
    for service in registry.services:
        if not eligible(service) or app_releases.installed(service, root, paths) is None:
            continue
        last = str(record(service.id, paths).get("checked_at") or "")
        try:
            age = time.time() - datetime.fromisoformat(last).timestamp() if last else CHECK_SECONDS
        except ValueError:
            age = CHECK_SECONDS
        if age < CHECK_SECONDS:
            continue
        try:
            check(service, root, log, paths)
            checked += 1
        except CheckFailed as exc:
            log(f"Mu3Lab could not check {service.name} for a newer release: {exc}")
            if "wait" in str(exc):
                break
    return checked


def candidate(service: Service, root: Path, variant: str, paths: RuntimePaths | None = None) -> Release | None:
    """The screened upstream release this server may move to for ``variant``, if any."""
    if not eligible(service):
        return None
    found = record(service.id, paths)
    images = (found.get("images") or {}).get(variant)
    version = str(found.get("version") or "")
    if found.get("state") != READY or not images or version in (found.get("failed") or {}):
        return None
    approved = app_releases.approved(service, root, variant)
    if not app_releases.is_newer(version, approved.version):
        return None  # Mu3Lab has caught up; its own pins win.
    return Release(version, dict(images), variant, approved.definition)


def mark_failed(service_id: str, version: str, reason: str, paths: RuntimePaths | None = None) -> None:
    """A release that did not pass on this server is never offered here again."""
    paths = paths or RuntimePaths()
    found = record(service_id, paths)
    failed = {**(found.get("failed") or {}), version: reason[:300]}
    same = found.get("version") == version
    records.put(
        SCOPE,
        service_id,
        {
            **found,
            "failed": failed,
            "state": HELD if same else found.get("state", ""),
            "reasons": [reason[:300]] if same else found.get("reasons", []),
        },
        paths,
    )
