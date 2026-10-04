"""Fast, verified image downloads for application installs.

Docker downloads each layer over a single connection. Some registries —
notably GitHub's (ghcr.io), served through Fastly — can be capped at well
under 1 MB/s per connection on some ISPs while the line itself is fast, so a
2 GB layer takes most of an hour. This module fetches the image content
itself: each layer is split into ranges fetched in parallel, every blob is
checked against the digest in its manifest, and the finished image is handed
to Docker with ``docker load`` as an OCI archive. Docker's containerd image
store keeps the manifest digest, so pinned ``name:tag@sha256:…`` references
resolve exactly as if Docker had pulled them.

It adapts instead of always splitting: the first range is measured alone and
the download only widens to many connections when each connection is slow. A
process-wide cap bounds the total, so several app downloads at once cannot
swamp a home network. Anything unexpected raises ``FetchError`` and the caller
falls back to Docker's own pull, so an install is never worse off than before.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import re
import shutil
import tarfile
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

import httpx

from ctl import actions
from ctl.store import db

Log = Callable[[str], None]

CHUNK = 8 * 1024 * 1024
# Below this per-connection speed a line is being throttled and more
# connections help; above it Docker's own three are plenty and polite.
FAST_CONNECTION = 4 * 1024 * 1024
NARROW_WIDTH = 3
WIDE_WIDTH = 16
# Shared by every download in this process so parallel app installs share
# one connection budget.
MAX_CONNECTIONS = 16
_CONNECTIONS = threading.BoundedSemaphore(MAX_CONNECTIONS)
# How long the narrow start runs before its per-connection rate is judged.
WIDEN_AFTER = 6.0
RETRIES = 6

MANIFEST_TYPES = (
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
)
INDEX_TYPES = MANIFEST_TYPES[:2]
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


class FetchError(RuntimeError):
    """The fast path could not finish; the caller should use Docker's pull."""


class Paused(Exception):
    """The owner paused this download; completed ranges are kept for resume."""


# --- References ---------------------------------------------------------------------


@dataclass(frozen=True)
class ImageRef:
    registry: str  # host used for API calls, e.g. ghcr.io or registry-1.docker.io
    repository: str  # e.g. modsetter/surfsense-backend or library/redis
    tag: str
    digest: str
    original: str

    @property
    def display_host(self) -> str:
        return "docker.io" if self.registry == "registry-1.docker.io" else self.registry

    @property
    def name(self) -> str:
        """The name Docker's containerd store records for a pull of this reference."""
        base = f"{self.display_host}/{self.repository}"
        return f"{base}:{self.tag}" if self.tag else f"{base}@{self.digest}"

    @property
    def reference(self) -> str:
        return self.digest or self.tag


def parse_ref(value: str) -> ImageRef:
    """Normalise an image reference the way Docker does."""
    original = value.strip()
    if not original or any(char.isspace() for char in original):
        raise FetchError(f"invalid image reference: {value!r}")
    name, _, digest = original.partition("@")
    if digest and not DIGEST.match(digest):
        raise FetchError(f"unsupported image digest in {original}")
    tag = ""
    last = name.rsplit("/", 1)[-1]
    if ":" in last:
        name, tag = name.rsplit(":", 1)
    first, _, rest = name.partition("/")
    if rest and ("." in first or ":" in first or first == "localhost"):
        registry, repository = first, rest
    else:
        registry, repository = "registry-1.docker.io", name
    if registry in {"docker.io", "index.docker.io"}:
        registry = "registry-1.docker.io"
    if registry == "registry-1.docker.io" and "/" not in repository:
        repository = f"library/{repository}"
    if not tag and not digest:
        tag = "latest"
    return ImageRef(registry, repository, tag, digest, original)


def host_platform() -> tuple[str, str]:
    machine = platform.machine().lower()
    arch = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(machine, machine)
    return "linux", arch


# --- Registry client ----------------------------------------------------------------


class Registry:
    """Anonymous pull access to one repository on an OCI registry."""

    def __init__(self, client: httpx.Client, ref: ImageRef) -> None:
        self.client = client
        self.ref = ref
        self.base = f"https://{ref.registry}/v2/{ref.repository}"
        self._token = ""
        self._authenticated = False
        self._lock = threading.Lock()

    def _ensure_auth(self) -> None:
        with self._lock:
            if not self._authenticated:
                self._authenticate()

    def _reauthenticate(self) -> None:
        with self._lock:
            self._authenticate()

    def _authenticate(self) -> None:
        self._authenticated = True
        probe = self.client.get(f"https://{self.ref.registry}/v2/")
        challenge = probe.headers.get("www-authenticate", "")
        if probe.status_code != 401 or not challenge.lower().startswith("bearer"):
            self._token = ""
            return
        params = dict(re.findall(r'(\w+)="([^"]*)"', challenge))
        realm = params.pop("realm", "")
        if not realm:
            raise FetchError(f"{self.ref.registry} sent an unusable sign-in challenge")
        params["scope"] = f"repository:{self.ref.repository}:pull"
        response = self.client.get(realm, params=params)
        if response.status_code != 200:
            raise FetchError(f"{self.ref.registry} refused anonymous access (HTTP {response.status_code})")
        body = response.json()
        self._token = body.get("token") or body.get("access_token") or ""

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = dict(extra or {})
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _get(self, url: str, headers: dict[str, str] | None = None) -> httpx.Response:
        self._ensure_auth()
        response = self.client.get(url, headers=self._headers(headers))
        if response.status_code == 401:
            self._reauthenticate()
            response = self.client.get(url, headers=self._headers(headers))
        return response

    def manifest(self, reference: str) -> tuple[bytes, str, str]:
        """Return (bytes, media type, digest); a digest reference is verified."""
        response = self._get(f"{self.base}/manifests/{reference}", {"Accept": ",".join(MANIFEST_TYPES)})
        if response.status_code != 200:
            raise FetchError(f"manifest {self.ref.repository}:{reference} returned HTTP {response.status_code}")
        body = response.content
        digest = "sha256:" + hashlib.sha256(body).hexdigest()
        if reference.startswith("sha256:") and digest != reference:
            raise FetchError(f"manifest for {self.ref.original} did not match its pinned digest")
        media = response.headers.get("content-type", "").split(";")[0].strip()
        if not media:
            media = json.loads(body).get("mediaType", "")
        return body, media, digest

    def small_blob(self, digest: str) -> bytes:
        response = self._get(f"{self.base}/blobs/{digest}")
        if response.status_code in {301, 302, 303, 307, 308}:
            response = self.client.get(response.headers["location"])
        if response.status_code != 200:
            raise FetchError(f"blob {digest[:19]} returned HTTP {response.status_code}")
        if "sha256:" + hashlib.sha256(response.content).hexdigest() != digest:
            raise FetchError(f"blob {digest[:19]} failed verification")
        return response.content

    def blob_location(self, digest: str) -> tuple[str, dict[str, str]]:
        """Where a blob's bytes live: a signed CDN URL, or the registry itself."""
        url = f"{self.base}/blobs/{digest}"
        self._ensure_auth()

        def ask() -> tuple[int, str]:
            # A GET, not a HEAD: some registries answer HEAD themselves but
            # redirect the real download. The body is never read.
            with self.client.stream("GET", url, headers=self._headers({"Range": "bytes=0-0"})) as response:
                return response.status_code, response.headers.get("location", "")

        status, location = ask()
        if status == 401:
            self._reauthenticate()
            status, location = ask()
        if status in {301, 302, 303, 307, 308} and location:
            return str(httpx.URL(url).join(location)), {}
        if status in {200, 206}:
            return url, self._headers()
        raise FetchError(f"blob {digest[:19]} returned HTTP {status}")


# --- Planning -----------------------------------------------------------------------


@dataclass
class Blob:
    digest: str
    size: int
    registry: Registry


@dataclass
class ImagePlan:
    ref: ImageRef
    top: bytes
    top_type: str
    top_digest: str
    platform_manifest: bytes | None
    platform_digest: str
    config_digest: str
    config: bytes
    layers: list[dict[str, Any]]
    # Layer digests Docker already holds (confirmed through local manifests).
    reused: set[str] = field(default_factory=set)

    @property
    def missing_layers(self) -> list[dict[str, Any]]:
        return [layer for layer in self.layers if layer["digest"] not in self.reused]


def plan_image(registry: Registry, ref: ImageRef) -> ImagePlan:
    top, top_type, top_digest = registry.manifest(ref.reference)
    document = json.loads(top)
    platform_manifest: bytes | None = None
    platform_digest = ""
    if top_type in INDEX_TYPES or "manifests" in document:
        os_name, arch = host_platform()
        match = next(
            (
                item
                for item in document.get("manifests", [])
                if item.get("platform", {}).get("os") == os_name
                and item.get("platform", {}).get("architecture") == arch
            ),
            None,
        )
        if match is None:
            raise FetchError(f"{ref.original} has no {os_name}/{arch} image")
        platform_manifest, _type, platform_digest = registry.manifest(match["digest"])
        document = json.loads(platform_manifest)
    config_digest = document["config"]["digest"]
    config = registry.small_blob(config_digest)
    layers = [{"digest": layer["digest"], "size": int(layer["size"])} for layer in document["layers"]]
    for item in [config_digest, *(layer["digest"] for layer in layers)]:
        if not DIGEST.match(item):
            raise FetchError(f"{ref.original} uses an unsupported digest algorithm")
    return ImagePlan(ref, top, top_type, top_digest, platform_manifest, platform_digest, config_digest, config, layers)


def diff_ids(config: bytes) -> list[str]:
    return list(json.loads(config).get("rootfs", {}).get("diff_ids", []))


class LocalLayers:
    """Which compressed layers Docker already stores.

    Docker only reports uncompressed layer ids for local images, so a layer is
    counted as present only when a local image with the same uncompressed
    layer lists the identical compressed digest in its own manifest (fetched
    once per image and cached, since digests never change).
    """

    def __init__(self, client: httpx.Client, database: Path, local_images: list[dict[str, Any]]) -> None:
        self.client = client
        self.database = database
        self.images = local_images
        self._compressed: dict[str, set[str]] = {}

    def _manifest_layers(self, repo_digest: str) -> set[str]:
        if repo_digest in self._compressed:
            return self._compressed[repo_digest]
        layers: set[str] = set()
        try:
            ref = parse_ref(repo_digest)
            with db.connect(self.database) as connection:
                row = connection.execute(
                    "SELECT value_json FROM records WHERE scope='image-manifests' AND name=?", (repo_digest,)
                ).fetchone()
            if row:
                layers = set(json.loads(row[0]))
            else:
                registry = Registry(self.client, ref)
                plan = plan_image(registry, ref)
                layers = {layer["digest"] for layer in plan.layers}
                with db.connect(self.database) as connection:
                    connection.execute(
                        "INSERT INTO records VALUES (?, ?, ?, ?) ON CONFLICT(scope, name) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
                        ("image-manifests", repo_digest, json.dumps(sorted(layers)), time.time()),
                    )

        except (FetchError, httpx.HTTPError, ValueError, KeyError, OSError):
            layers = set()
        self._compressed[repo_digest] = layers
        return layers

    def reused(self, plan: ImagePlan) -> set[str]:
        return self.reused_layers(diff_ids(plan.config), [layer["digest"] for layer in plan.layers])

    def reused_layers(self, layer_diff_ids: list[str], layer_digests: list[str]) -> set[str]:
        """Compressed layer digests (paired in order with their uncompressed ids) Docker already holds."""
        wanted = dict(zip(layer_diff_ids, layer_digests, strict=False))
        present: set[str] = set()
        for image in self.images:
            shared = [wanted[item] for item in image.get("layers", []) if item in wanted]
            if not shared:
                continue
            for repo_digest in image.get("repo_digests", []):
                present.update(item for item in shared if item in self._manifest_layers(repo_digest))
        return present


# --- Progress -----------------------------------------------------------------------


class Progress:
    """Thread-safe byte counter with a smoothed rate, reported at most once a second."""

    def __init__(self, total: int, report: Callable[[dict[str, Any]], None] | None, images: int) -> None:
        self.total = total
        self.done = 0
        self.images = images
        self.images_done = 0
        self.connections = 0
        self._report = report
        self._lock = threading.Lock()
        self._samples: list[tuple[float, int]] = [(time.monotonic(), 0)]
        self._last = 0.0

    def add(self, amount: int) -> None:
        with self._lock:
            self.done += amount
        self.emit()

    def rate(self) -> float:
        now = time.monotonic()
        with self._lock:
            self._samples.append((now, self.done))
            self._samples = [sample for sample in self._samples if now - sample[0] <= 8] or [(now, self.done)]
            first = self._samples[0]
        elapsed = now - first[0]
        return max(0.0, (self.done - first[1]) / elapsed) if elapsed > 0.5 else 0.0

    def snapshot(self, state: str = "downloading") -> dict[str, Any]:
        return {
            "state": state,
            "total_bytes": self.total,
            "done_bytes": min(self.done, self.total),
            "rate_bps": int(self.rate()),
            "images_total": self.images,
            "images_done": self.images_done,
            "connections": self.connections,
        }

    def emit(self, state: str = "downloading", force: bool = False) -> None:
        now = time.monotonic()
        if self._report is None or (not force and now - self._last < 1.0):
            return
        self._last = now
        self._report(self.snapshot(state))


# --- Blob download ------------------------------------------------------------------


class BlobStore:
    """Content-addressed download cache with resumable ranges."""

    def __init__(self, root: Path) -> None:
        self.root = root / "blobs"
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, digest: str) -> Path:
        return self.root / digest.split(":")[1]

    def part(self, digest: str) -> Path:
        return self.root / (digest.split(":")[1] + ".part")

    def ranges_file(self, digest: str) -> Path:
        return self.root / (digest.split(":")[1] + ".ranges")

    def completed(self, digest: str) -> set[int]:
        try:
            return set(json.loads(self.ranges_file(digest).read_text()))
        except (OSError, ValueError):
            return set()

    def remove(self, digest: str) -> None:
        for path in (self.path(digest), self.part(digest), self.ranges_file(digest)):
            path.unlink(missing_ok=True)


class Downloader:
    def __init__(
        self,
        client: httpx.Client,
        store: BlobStore,
        progress: Progress,
        stop: threading.Event,
        log: Log,
    ) -> None:
        self.client = client
        self.store = store
        self.progress = progress
        self.stop = stop
        self.log = log
        self._lock = threading.Lock()
        self._locations: dict[str, tuple[str, dict[str, str]]] = {}
        self._done_ranges: dict[str, set[int]] = {}
        self._error: BaseException | None = None
        self.width = NARROW_WIDTH

    def _location(self, blob: Blob, refresh: bool = False) -> tuple[str, dict[str, str]]:
        with self._lock:
            cached = self._locations.get(blob.digest)
        if cached and not refresh:
            return cached
        location = blob.registry.blob_location(blob.digest)
        with self._lock:
            self._locations[blob.digest] = location
        return location

    def _fetch_range(self, blob: Blob, index: int) -> float:
        """Download one range into the part file; returns bytes/second for this connection."""
        start = index * CHUNK
        end = min(blob.size, start + CHUNK) - 1
        for attempt in range(RETRIES):
            if self.stop.is_set():
                raise Paused
            received = 0
            began = time.monotonic()
            try:
                url, headers = self._location(blob, refresh=attempt > 0)
                with (
                    _CONNECTIONS,
                    self.client.stream("GET", url, headers={**headers, "Range": f"bytes={start}-{end}"}) as resp,
                ):
                    if resp.status_code == 200 and blob.size > CHUNK:
                        raise FetchError(f"{blob.registry.ref.registry} does not support partial downloads")
                    if resp.status_code not in {200, 206}:
                        raise httpx.HTTPStatusError(f"HTTP {resp.status_code}", request=resp.request, response=resp)
                    with open(self.store.part(blob.digest), "r+b") as handle:
                        handle.seek(start)
                        for data in resp.iter_bytes(256 * 1024):
                            if self.stop.is_set():
                                raise Paused
                            handle.write(data)
                            received += len(data)
                            self.progress.add(len(data))
                if received != end - start + 1:
                    raise httpx.TransportError(f"short read ({received} of {end - start + 1} bytes)")
                self._mark(blob, index)
                return received / max(time.monotonic() - began, 0.001)
            except Paused:
                self.progress.add(-received)
                raise
            except FetchError:
                self.progress.add(-received)
                raise
            except (httpx.HTTPError, OSError) as exc:
                self.progress.add(-received)
                if attempt == RETRIES - 1:
                    raise FetchError(f"download of {blob.digest[:19]} kept failing: {exc}") from exc
                time.sleep(min(2**attempt, 20))
        raise FetchError("unreachable")

    def _mark(self, blob: Blob, index: int) -> None:
        with self._lock:
            done = self._done_ranges.setdefault(blob.digest, set())
            done.add(index)
            tmp = self.store.ranges_file(blob.digest).with_suffix(".tmp")
            tmp.write_text(json.dumps(sorted(done)))
            os.replace(tmp, self.store.ranges_file(blob.digest))

    def _prepare(self, blob: Blob) -> list[int]:
        """Resume what an earlier attempt finished; return the ranges still needed."""
        if self.store.path(blob.digest).is_file():
            self.progress.add(blob.size)
            return []
        part = self.store.part(blob.digest)
        count = -(-blob.size // CHUNK)
        done = {index for index in self.store.completed(blob.digest) if index < count} if part.is_file() else set()
        if not part.is_file():
            self.store.ranges_file(blob.digest).unlink(missing_ok=True)
            with open(part, "wb") as handle:
                handle.truncate(blob.size)
        self._done_ranges[blob.digest] = done
        self.progress.add(sum(min(CHUNK, blob.size - index * CHUNK) for index in done))
        return [index for index in range(count) if index not in done]

    def _finish(self, blob: Blob) -> None:
        digest = hashlib.sha256()
        with open(self.store.part(blob.digest), "rb") as handle:
            for data in iter(lambda: handle.read(4 * 1024 * 1024), b""):
                digest.update(data)
        if "sha256:" + digest.hexdigest() != blob.digest:
            self.store.remove(blob.digest)
            raise FetchError(f"layer {blob.digest[:19]} failed verification and was discarded")
        os.replace(self.store.part(blob.digest), self.store.path(blob.digest))
        self.store.ranges_file(blob.digest).unlink(missing_ok=True)

    def run(self, blobs: Iterable[Blob]) -> None:
        """Download every blob, widening only while each connection is slow."""
        blobs = sorted(blobs, key=lambda blob: -blob.size)
        tasks: list[tuple[Blob, int]] = []
        for blob in blobs:
            tasks.extend((blob, index) for index in self._prepare(blob))
        if tasks:
            self._parallel(tasks)
        for blob in blobs:
            if not self.store.path(blob.digest).is_file():
                self._finish(blob)

    def _parallel(self, tasks: list[tuple[Blob, int]]) -> None:
        """Start narrow like Docker; widen when each connection proves slow.

        Some CDNs let a connection burst before throttling it, so the rate is
        judged continuously rather than from the first range alone.
        """
        queue = list(reversed(tasks))
        lock = threading.Lock()

        def worker() -> None:
            while True:
                with lock:
                    if not queue or self._error is not None:
                        return
                    task = queue.pop()
                try:
                    self._fetch_range(*task)
                except BaseException as exc:  # surfaced on the calling thread below
                    with lock:
                        if self._error is None:
                            self._error = exc
                    return

        threads: list[threading.Thread] = []

        def grow(width: int) -> None:
            self.width = width
            while len(threads) < min(width, len(tasks)):
                thread = threading.Thread(target=worker, daemon=True)
                threads.append(thread)
                thread.start()

        grow(NARROW_WIDTH)
        began = time.monotonic()
        while any(thread.is_alive() for thread in threads):
            time.sleep(0.5)
            alive = sum(thread.is_alive() for thread in threads)
            self.progress.connections = alive
            with lock:
                waiting = len(queue)
            if self.width < WIDE_WIDTH and waiting and time.monotonic() - began >= WIDEN_AFTER and alive:
                per_connection = self.progress.rate() / alive
                if per_connection < FAST_CONNECTION:
                    self.log(
                        f"Each connection is only getting {per_connection / 1e6:.1f} MB/s; "
                        f"widening to {WIDE_WIDTH} connections."
                    )
                    grow(WIDE_WIDTH)
        self.progress.connections = 0
        if self._error is not None:
            raise self._error


# --- Loading into Docker ------------------------------------------------------------


def _write_archive(plan: ImagePlan, store: BlobStore, stream: IO[bytes]) -> None:
    index = {
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.index.v1+json",
        "manifests": [
            {
                "mediaType": plan.top_type,
                "digest": plan.top_digest,
                "size": len(plan.top),
                "annotations": {
                    "io.containerd.image.name": plan.ref.name,
                    **({"org.opencontainers.image.ref.name": plan.ref.tag} if plan.ref.tag else {}),
                },
            }
        ],
    }
    with tarfile.open(fileobj=stream, mode="w|") as archive:

        def add_bytes(name: str, data: bytes) -> None:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))

        add_bytes("oci-layout", b'{"imageLayoutVersion":"1.0.0"}')
        add_bytes("index.json", json.dumps(index).encode())
        add_bytes(f"blobs/sha256/{plan.top_digest[7:]}", plan.top)
        if plan.platform_manifest is not None:
            add_bytes(f"blobs/sha256/{plan.platform_digest[7:]}", plan.platform_manifest)
        add_bytes(f"blobs/sha256/{plan.config_digest[7:]}", plan.config)
        for layer in plan.missing_layers:
            path = store.path(layer["digest"])
            info = tarfile.TarInfo(f"blobs/sha256/{layer['digest'][7:]}")
            info.size = path.stat().st_size
            with open(path, "rb") as handle:
                archive.addfile(info, handle)


def load_image(plan: ImagePlan, store: BlobStore, log: Log) -> bool:
    """Load one image; False means Docker lacked a layer we assumed it had."""
    rc, output = actions.docker_load_stream(lambda stream: _write_archive(plan, store, stream), log)
    broken = "error unpacking" in output.lower() or "content digest" in output.lower()
    if rc != 0 or broken:
        actions.docker_image_remove(plan.ref.name, log)
        if broken and plan.reused:
            return False
        raise FetchError(f"Docker could not load {plan.ref.original}: {output[-400:]}")
    if not actions.docker_image_present(plan.ref.original):
        raise FetchError(f"Docker did not register {plan.ref.original} after loading it")
    return True


# --- Entry point --------------------------------------------------------------------


@dataclass
class FetchResult:
    images: int
    downloaded_bytes: int
    reused_bytes: int
    seconds: float


def fetch_images(
    images: list[str],
    cache: Path,
    log: Log,
    *,
    report: Callable[[dict[str, Any]], None] | None = None,
    stop: threading.Event | None = None,
    client: httpx.Client | None = None,
) -> FetchResult:
    """Make every image present in Docker, downloading only what is missing.

    Raises ``Paused`` when ``stop`` is set (finished ranges stay cached for a
    later call) and ``FetchError`` when the fast path cannot finish.
    """
    stop = stop or threading.Event()
    began = time.monotonic()
    own_client = client is None
    http = client or httpx.Client(
        timeout=httpx.Timeout(60.0, connect=20.0),
        follow_redirects=False,
        limits=httpx.Limits(max_connections=MAX_CONNECTIONS * 2, max_keepalive_connections=MAX_CONNECTIONS),
        headers={"User-Agent": "Mu3Lab image fetch"},
    )
    store = BlobStore(cache)
    try:
        refs = []
        for image in dict.fromkeys(images):
            if actions.docker_image_present(image):
                continue
            refs.append(parse_ref(image))
        if not refs:
            if report:
                report({**Progress(0, None, len(images)).snapshot("done"), "images_done": len(images)})
            return FetchResult(0, 0, 0, time.monotonic() - began)
        registries = {(ref.registry, ref.repository): Registry(http, ref) for ref in refs}
        plans = [plan_image(registries[(ref.registry, ref.repository)], ref) for ref in refs]
        local = LocalLayers(http, cache.parent / "mu3lab.db", actions.docker_local_images())
        for plan in plans:
            plan.reused = local.reused(plan)
        blobs: dict[str, Blob] = {}
        for plan in plans:
            for layer in plan.missing_layers:
                blobs.setdefault(
                    layer["digest"],
                    Blob(layer["digest"], layer["size"], registries[(plan.ref.registry, plan.ref.repository)]),
                )
        reused_bytes = sum(layer["size"] for plan in plans for layer in plan.layers if layer["digest"] in plan.reused)
        total = sum(blob.size for blob in blobs.values())
        _check_space(cache, total)
        progress = Progress(total, report, len(plans))
        log(
            f"Downloading {len(plans)} image(s): {total / 1e6:.0f} MB"
            + (f", reusing {reused_bytes / 1e6:.0f} MB already on this server." if reused_bytes else ".")
        )
        progress.emit(force=True)
        Downloader(http, store, progress, stop, log).run(blobs.values())
        progress.emit("loading", force=True)
        for plan in plans:
            log(f"Loading {plan.ref.original} into Docker.")
            if not load_image(plan, store, log):
                # A layer we believed was present was not: fetch it and load again.
                log(f"Docker lacked a shared layer for {plan.ref.original}; downloading it too.")
                extra = [
                    Blob(layer["digest"], layer["size"], registries[(plan.ref.registry, plan.ref.repository)])
                    for layer in plan.layers
                    if layer["digest"] in plan.reused
                ]
                plan.reused = set()
                progress.total += sum(blob.size for blob in extra)
                Downloader(http, store, progress, stop, log).run(extra)
                if not load_image(plan, store, log):
                    raise FetchError(f"Docker could not load {plan.ref.original}")
            progress.images_done += 1
            progress.emit("loading", force=True)
        for digest in blobs:
            store.remove(digest)
        progress.emit("done", force=True)
        return FetchResult(len(plans), total, reused_bytes, time.monotonic() - began)
    except httpx.HTTPError as exc:
        raise FetchError(f"network error: {exc}") from exc
    finally:
        if own_client:
            http.close()


def _check_space(cache: Path, needed: int) -> None:
    # The download is stored once here and again inside Docker while loading.
    free = shutil.disk_usage(cache).free
    if free < needed * 2 + 1024**3:
        raise FetchError(
            f"Not enough free disk space: {needed * 2 / 1e9:.1f} GB needed while loading, {free / 1e9:.1f} GB free."
        )
