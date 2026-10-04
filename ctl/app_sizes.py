"""Download and disk sizes of each app, shown before installing.

Registries report each layer's compressed (download) size. The unpacked size
is read from the last four bytes of each gzip layer, which record it, so one
tiny request per layer gives an exact figure without downloading anything.
Docker's image store keeps both the compressed layer and its unpacked copy,
so the space an app uses is close to the sum of the two.

The worker measures sizes in the background (the web process never talks to
Docker) and stores, per app, each layer and whether this server already has
it; the dashboard adds up any selection from that without double counting
layers that apps share.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import struct
import time
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from ctl import actions
from ctl.image_fetch import FetchError, LocalLayers, Registry, parse_ref, plan_image
from ctl.runtime import RuntimePaths
from ctl.store import db

Log = Callable[[str], None]
# A tag can be moved to a new image; a digest never changes.
TAG_MAX_AGE = 24 * 3600


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def unpacked_size(client: httpx.Client, registry: Registry, digest: str, size: int) -> int:
    """Uncompressed size of a gzip layer from its trailer; estimated if not gzip."""
    if size < 20:
        return size
    url, headers = registry.blob_location(digest)
    response = client.get(url, headers={**headers, "Range": f"bytes={size - 4}-{size - 1}"})
    if response.status_code != 206 or len(response.content) != 4:
        return size * 3
    value = int(struct.unpack("<I", response.content)[0])
    # The trailer holds the size modulo 4 GiB; layers can be bigger.
    while value < size * 0.9:
        value += 2**32
    return value


def measure_image(client: httpx.Client, image: str, path: Path) -> dict[str, Any]:
    """Layers of one image with download and unpacked sizes, cached on disk."""
    ref = parse_ref(image)
    name = hashlib.sha256(image.encode()).hexdigest()
    try:
        with db.connect(path) as connection:
            row = connection.execute(
                "SELECT value_json FROM records WHERE scope='image-sizes' AND name=?", (name,)
            ).fetchone()
        cached = json.loads(row[0]) if row else {}
        if cached and (ref.digest or time.time() - float(cached.get("measured", 0)) < TAG_MAX_AGE):
            return cached
    except (OSError, ValueError):
        pass
    registry = Registry(client, ref)
    plan = plan_image(registry, ref)
    layers = [
        {
            "digest": layer["digest"],
            "size": int(layer["size"]),
            "unpacked": unpacked_size(client, registry, layer["digest"], int(layer["size"])),
        }
        for layer in plan.layers
    ]
    result = {
        "image": image,
        "layers": layers,
        "diff_ids": json.loads(plan.config).get("rootfs", {}).get("diff_ids", []),
        "measured": time.time(),
    }
    with db.connect(path) as connection:
        connection.execute(
            "INSERT INTO records VALUES (?, ?, ?, ?) ON CONFLICT(scope, name) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
            ("image-sizes", name, json.dumps(result), time.time()),
        )
    return result


class AppSizeStore:
    def __init__(self, database: Path) -> None:
        self.database = database

    @classmethod
    def runtime(cls, paths: RuntimePaths = RuntimePaths()) -> AppSizeStore | None:
        return cls(db.database(paths)) if paths.runtime.is_dir() else None

    def _connect(self) -> sqlite3.Connection:
        return db.connect(self.database)

    def save(self, service_id: str, layers: list[dict[str, Any]], error: str = "") -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO app_sizes (service_id, layers_json, error, updated_at) VALUES (?, ?, ?, ?)
                   ON CONFLICT(service_id) DO UPDATE SET layers_json = excluded.layers_json,
                   error = excluded.error, updated_at = excluded.updated_at""",
                (service_id, json.dumps(layers), error[:300], _now()),
            )

    def all(self) -> dict[str, dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM app_sizes").fetchall()
        result = {}
        for row in rows:
            try:
                layers = json.loads(row["layers_json"])
            except ValueError:
                layers = []
            result[str(row["service_id"])] = {
                "layers": layers,
                "error": str(row["error"]),
                "updated_at": str(row["updated_at"]),
            }
        return result


def totals(layers: Iterable[dict[str, Any]]) -> dict[str, int]:
    """Sizes of a set of layers, each shared layer counted once."""
    unique: dict[str, dict[str, Any]] = {}
    for layer in layers:
        unique.setdefault(str(layer["digest"]), layer)
    missing = [layer for layer in unique.values() if not layer.get("present")]
    return {
        "download_bytes": sum(int(layer["size"]) for layer in unique.values()),
        "needed_bytes": sum(int(layer["size"]) for layer in missing),
        "disk_bytes": sum(int(layer["size"]) + int(layer["unpacked"]) for layer in unique.values()),
        "needed_disk_bytes": sum(int(layer["size"]) + int(layer["unpacked"]) for layer in missing),
    }


def summarize(sizes: dict[str, dict[str, Any]], service_ids: Iterable[str]) -> dict[str, int]:
    return totals(layer for service_id in service_ids for layer in sizes.get(service_id, {}).get("layers", []))


def refresh(
    services: dict[str, list[str]],
    store: AppSizeStore,
    cache: Path,
    log: Log,
    *,
    client: httpx.Client | None = None,
) -> None:
    """Measure every app's images and record which layers this server already has."""
    http = client or httpx.Client(timeout=httpx.Timeout(30.0, connect=15.0), follow_redirects=False)
    try:
        local = LocalLayers(http, store.database, actions.docker_local_images())
        present_images: dict[str, bool] = {}
        for service_id, images in services.items():
            layers: list[dict[str, Any]] = []
            error = ""
            for image in images:
                try:
                    measured = measure_image(http, image, store.database)
                except (FetchError, httpx.HTTPError, ValueError, KeyError) as exc:
                    error = f"{image}: {exc}"
                    continue
                if image not in present_images:
                    present_images[image] = actions.docker_image_present(image)
                reused: set[str] = set()
                if not present_images[image]:
                    reused = local.reused_layers(
                        measured["diff_ids"], [layer["digest"] for layer in measured["layers"]]
                    )
                layers.extend(
                    {**layer, "present": present_images[image] or layer["digest"] in reused}
                    for layer in measured["layers"]
                )
            store.save(service_id, layers, error)
    finally:
        if client is None:
            http.close()
