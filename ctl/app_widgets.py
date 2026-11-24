"""Home board widgets that apps offer (manifest ``widgets``), and the figures they show.

Each app reads its own figures in ``apps/<id>/widgets.py``: one function per widget, named after the
widget's id (dashes as underscores), taking a ``WidgetContext`` and returning a dict shaped like
``Figures``. Mu3Lab does the rest, the same way for every app:

* who may add a widget: everyone, or administrators for server-wide figures and administrators-only apps;
* whether the app can answer now: not installed, starting, stopped (with why), or reachable;
* how long an answer is kept (the widget's ``refresh_seconds``), so several screens ask the app once;
* the last figures read, kept in the database, shown (marked, with their age) while the app is stopped
  or cannot answer, so a widget still says something useful;
* trimming what comes back to the shape the dashboard draws, so one app's mistake cannot break the
  board, and never passing on an error's details, which could hold an address or a key.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any, Literal

import httpx

from ctl.control_state import ControlState
from ctl.manifest.catalog import App, cached
from ctl.manifest.models import HomeWidgetSpec
from ctl.mcp_registry import credential_path
from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env
from ctl.store import records

LOG = logging.getLogger(__name__)
SEPARATOR = ":"
# A failed read is retried this soon at the earliest, so a broken app is not asked on every refresh.
ERROR_SECONDS = 15
TONES = ("", "good", "warn", "bad")
MAX_STATS = 8
MAX_ITEMS = 50

State = Literal["ok", "not_installed", "starting", "stopped", "setup", "error"]
# Keys people paste into a widget's settings (manifest ``key``), per app and person.
KEYS = "widget-keys"
# The last figures each widget read (records scope), per widget and whose figures they are.
SAVED = "home-widget-saved"
# Unchanged figures are written again at most this often, so their "as of" time stays near the truth.
SAVE_SECONDS = 600


class WidgetError(RuntimeError):
    """The app did not give the figures; the message is for the person and holds no secrets."""


class NeedsSetup(WidgetError):
    """This person's figures need something only they can give: opening the app once, or a key."""


@dataclass(frozen=True)
class Offered:
    app: App
    spec: HomeWidgetSpec

    @property
    def kind(self) -> str:
        return kind_name(self.app.id, self.spec.id)

    @property
    def admin_only(self) -> bool:
        route = self.app.manifest.route
        return self.spec.audience == "operators" or bool(route and route.audience == "operators")


def _key_name(app_id: str, uid: str) -> str:
    return f"{app_id}:{uid or 'household'}"


@dataclass
class WidgetContext:
    """What an app's widget reader gets: the app, who is looking, and helpers for its API."""

    app: App
    is_admin: bool
    paths: RuntimePaths = field(default_factory=RuntimePaths)
    uid: str = ""
    email: str = ""
    personal: bool = False

    @property
    def name(self) -> str:
        return self.app.manifest.name

    @property
    def base(self) -> str:
        """The app's API on this server's loopback port."""
        return f"http://127.0.0.1:{self.app.manifest.service.local_port}"

    def web(self, path: str = "/") -> str:
        """The app's private web address with ``path``, for "open this in the app" links ("" when unknown)."""
        origin = _origin(self.app.id)
        return f"{origin}{path if path.startswith('/') else '/' + path}" if origin else ""

    def env(self) -> dict[str, str]:
        """The app's own runtime settings and secrets (its project ``.env``)."""
        return read_runtime_env(self.paths.projects / self.app.id / ".env")

    def credentials(self, connector: str) -> dict[str, str]:
        """The credential Mu3Lab made for one of the app's chat connectors (the installing owner's)."""
        return read_runtime_env(credential_path(connector))

    def own_credentials(self, connector: str) -> dict[str, str]:
        """The viewer's own credential for a per-person connector (``per_person: true``).

        The installing owner's is the connector's own; everyone else's copy is made once they have
        opened the app (``ctl.person_connectors``). Raises ``NeedsSetup`` until it exists.
        """
        from ctl import person_connectors
        from ctl.store.secrets import SecretStore

        if self.email and self.email.lower() == person_connectors.owner_email(self.app.id):
            found = self.credentials(connector)
        else:
            found = SecretStore(self.paths).get("connector", person_connectors.instance_id(connector, self.uid)) or {}
        if not found:
            raise NeedsSetup(
                f"Open {self.name} once so Mu3Lab can show your own figures here; they appear within a few minutes."
            )
        return {str(key): str(value) for key, value in found.items()}

    def key(self) -> str:
        """The key pasted into this widget's settings (manifest ``key``); ``NeedsSetup`` until there is one."""
        from ctl.store.secrets import SecretStore

        saved = SecretStore(self.paths).get(KEYS, _key_name(self.app.id, self.uid if self.personal else ""))
        if not saved:
            raise NeedsSetup(f"Add your {self.name} key in this widget's settings to show it here.")
        return str(saved)

    def get(self, path: str, *, port: int | None = None, **kwargs: Any) -> Any:
        """GET from the app's API on this server's loopback port; the parsed JSON reply."""
        return self.request("GET", path, port=port, **kwargs)

    def request(self, method: str, path: str, *, port: int | None = None, **kwargs: Any) -> Any:
        """Call the app's API on this server's loopback port; the parsed JSON reply (None when empty)."""
        url = f"http://127.0.0.1:{port or self.app.manifest.service.local_port}{path}"
        try:
            response = httpx.request(method, url, timeout=15, **kwargs)
        except httpx.HTTPError:
            raise WidgetError(f"{self.name} did not answer.") from None
        if response.status_code in (401, 403):
            raise WidgetError(f"{self.name} refused Mu3Lab's sign-in (HTTP {response.status_code}).")
        if not response.is_success:
            raise WidgetError(f"{self.name} could not give its figures (HTTP {response.status_code}).")
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            raise WidgetError(f"{self.name} answered with something Mu3Lab could not read.") from None


def _moment(when: str | float | datetime) -> datetime | None:
    if isinstance(when, datetime):
        return when if when.tzinfo else when.replace(tzinfo=UTC)
    if isinstance(when, int | float):
        return datetime.fromtimestamp(when, UTC)
    try:
        found = datetime.fromisoformat(str(when).replace("Z", "+00:00"))
    except ValueError:
        return None
    return found if found.tzinfo else found.replace(tzinfo=UTC)


def _span(seconds: float) -> str:
    for size, unit in ((86400, "day"), (3600, "hour"), (60, "minute")):
        if seconds >= size:
            count = int(seconds // size)
            return f"{count} {unit}{'' if count == 1 else 's'}"
    return "a moment"


def ago(when: str | float | datetime) -> str:
    """\"3 days ago\", for a time in the past (an ISO date, seconds since 1970, or a datetime)."""
    moment = _moment(when)
    if moment is None:
        return ""
    seconds = (datetime.now(UTC) - moment).total_seconds()
    return "just now" if seconds < 60 else f"{_span(seconds)} ago"


def until(when: str | float | datetime) -> str:
    """\"in 5 hours\", for a time to come."""
    moment = _moment(when)
    if moment is None:
        return ""
    seconds = (moment - datetime.now(UTC)).total_seconds()
    return "any moment" if seconds < 60 else f"in {_span(seconds)}"


_names: dict[str, tuple[float, str]] = {}
NAME_SECONDS = 600


def _origin(app_id: str) -> str:
    """https://<this server's tailnet name>:<the app's private port>, kept a few minutes."""
    from ctl.registry import load
    from ctl.service_state import tailnet_dns_name

    now = time.monotonic()
    known = _names.get("dns")
    if known is None or now - known[0] > NAME_SECONDS:
        try:
            known = _names["dns"] = (now, tailnet_dns_name())
        except Exception:  # Tailscale not answering: links are left out, figures still show
            known = (now, "")
    try:
        service = load().get(app_id)
    except Exception:
        return ""
    port = service.private_https_port or service.https_port
    if not known[1] or not port:
        return ""
    return f"https://{known[1]}" + ("" if port == 443 else f":{port}")


def kind_name(app_id: str, widget_id: str) -> str:
    return f"{app_id}{SEPARATOR}{widget_id}"


def offered(*, is_admin: bool, state: ControlState | None = None) -> list[Offered]:
    """The widgets installed apps offer this person, in catalog order."""
    state = state if state is not None else ControlState.runtime()
    found = []
    for app in cached().apps:
        if not app.manifest.widgets or state is None:
            continue
        installation = state.app_installation(app.id)
        if not installation or installation.get("state") == "not_installed":
            continue
        for spec in app.manifest.widgets:
            widget = Offered(app, spec)
            if is_admin or not widget.admin_only:
                found.append(widget)
    return found


def find(kind: str, *, is_admin: bool) -> Offered | None:
    """The widget behind a board kind, if this person may see it; installed or not."""
    app_id, _, widget_id = kind.partition(SEPARATOR)
    catalog = cached()
    if not widget_id or app_id not in catalog:
        return None
    app = catalog.get(app_id)
    spec = next((item for item in app.manifest.widgets if item.id == widget_id), None)
    if spec is None:
        return None
    widget = Offered(app, spec)
    return widget if is_admin or not widget.admin_only else None


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value if value is not None else "").split())[:limit]


def _tone(value: Any) -> str:
    return value if value in TONES else ""


def _url(value: Any) -> str:
    text = str(value or "")
    return text if text.startswith(("https://", "/")) and len(text) <= 2000 else ""


def figures(data: Any) -> dict[str, Any]:
    """What a reader returned, cut to the shape the dashboard draws; anything else is dropped."""
    data = data if isinstance(data, dict) else {}
    stats = []
    for item in list(data.get("stats") or [])[:MAX_STATS]:
        if isinstance(item, dict) and _text(item.get("label"), 40):
            stats.append(
                {
                    "label": _text(item.get("label"), 40),
                    "value": _text(item.get("value"), 40),
                    "tone": _tone(item.get("tone")),
                }
            )
    items = []
    for item in list(data.get("items") or [])[:MAX_ITEMS]:
        if not isinstance(item, dict) or not _text(item.get("title"), 120):
            continue
        progress = item.get("progress")
        items.append(
            {
                "title": _text(item.get("title"), 120),
                "subtitle": _text(item.get("subtitle"), 160),
                "meta": _text(item.get("meta"), 60),
                "progress": min(max(float(progress), 0.0), 1.0) if isinstance(progress, int | float) else None,
                "tone": _tone(item.get("tone")),
                "url": _url(item.get("url")),
                # A tick box: done or not, and what ticking it asks the app (``<widget>_action``).
                "check": item.get("check") if isinstance(item.get("check"), bool) else None,
                "action": _text(item.get("action"), 120) if isinstance(item.get("check"), bool) else "",
            }
        )
    return {
        "stats": stats,
        "items": items,
        "items_title": _text(data.get("items_title"), 60),
        "empty": _text(data.get("empty"), 120),
        "note": _text(data.get("note"), 200),
    }


_modules: dict[str, tuple[float, ModuleType]] = {}
_cache: dict[tuple[str, str], tuple[float, float, dict[str, Any]]] = {}
_lock = threading.Lock()


def _reader(app: App, spec: HomeWidgetSpec, suffix: str = ""):
    path: Path = app.folder / "widgets.py"
    if not path.is_file():
        raise WidgetError(f"{app.manifest.name}'s widgets are missing from this Mu3Lab.")
    stamp = path.stat().st_mtime
    known = _modules.get(app.id)
    if known is None or known[0] != stamp:
        loader = importlib.util.spec_from_file_location(f"mu3lab_app_widgets_{app.id.replace('-', '_')}", path)
        if loader is None or loader.loader is None:
            raise WidgetError(f"{app.manifest.name}'s widgets could not be loaded.")
        module = importlib.util.module_from_spec(loader)
        loader.loader.exec_module(module)
        known = _modules[app.id] = (stamp, module)
    function = getattr(known[1], spec.id.replace("-", "_") + suffix, None)
    if not callable(function):
        raise WidgetError(f"{app.manifest.name} has no reader for this widget.")
    return function


def _reachable(widget: Offered, service_state: str) -> tuple[State, str]:
    name = widget.app.manifest.name
    if service_state in ("not_installed", ""):
        return "not_installed", f"{name} isn't installed."
    if not widget.spec.needs_running or service_state == "ready":
        return "ok", ""
    if service_state == "starting":
        return "starting", f"{name} is starting."
    if service_state == "needs_setup":
        return "stopped", f"{name} needs attention before it can show its figures."
    return "stopped", f"{name} is stopped."


def _context(widget: Offered, *, is_admin: bool, uid: str, email: str, paths: RuntimePaths | None) -> WidgetContext:
    return WidgetContext(
        widget.app, is_admin, paths or RuntimePaths(), uid=uid, email=email, personal=widget.spec.personal
    )


def read(
    widget: Offered,
    *,
    service_state: str,
    is_admin: bool,
    uid: str = "",
    email: str = "",
    paths: RuntimePaths | None = None,
) -> dict[str, Any]:
    """One widget's figures and state, from the cache while they are fresh."""
    app = widget.app
    reply: dict[str, Any] = {"app_id": app.id, "app_name": app.manifest.name, "updated_at": 0.0}
    state, detail = _reachable(widget, service_state)
    key = _key(widget, is_admin=is_admin, uid=uid)
    if state in ("stopped", "starting"):
        return _with_saved(reply | {"state": state, "detail": detail}, key, paths)
    if state != "ok":
        return reply | {"state": state, "detail": detail}
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now < hit[1]:
            return hit[2]
    try:
        raw = _reader(app, widget.spec)(_context(widget, is_admin=is_admin, uid=uid, email=email, paths=paths))
        result = reply | {"state": "ok", "detail": "", "updated_at": now} | figures(raw)
        keep = max(5.0, widget.spec.refresh_seconds * 0.8)
        _remember(key, result, paths)
    except NeedsSetup as exc:
        result = reply | {"state": "setup", "detail": str(exc), "updated_at": now}
        keep = ERROR_SECONDS
    except WidgetError as exc:
        result = _with_saved(reply | {"state": "error", "detail": str(exc), "updated_at": now}, key, paths)
        keep = ERROR_SECONDS
    except Exception:  # a reader's own bug: logged here, never shown with its details
        LOG.exception("Home widget %s failed", widget.kind)
        detail = f"Mu3Lab could not read {app.manifest.name}'s figures."
        result = _with_saved(reply | {"state": "error", "detail": detail}, key, paths)
        keep = ERROR_SECONDS
    with _lock:
        _cache[key] = (now, now + keep, result)
    return result


def _key(widget: Offered, *, is_admin: bool, uid: str) -> tuple[str, str]:
    # Personal figures are each person's; the rest are the same for every administrator, or everyone.
    return (widget.kind, uid if widget.spec.personal else "admin" if is_admin else "member")


def _saved_name(key: tuple[str, str]) -> str:
    return f"{key[0]}|{key[1]}"


# What was last written for each widget: the figures (as JSON) and when, so unchanged ones are not
# written on every refresh; and what was last read back, so a stopped app's widget is not a database read.
_written: dict[tuple[str, str], tuple[str, float]] = {}
_saved: dict[tuple[str, str], dict[str, Any]] = {}
FIELDS = ("stats", "items", "items_title", "empty", "note")


def _remember(key: tuple[str, str], result: dict[str, Any], paths: RuntimePaths | None) -> None:
    """Keep these figures as the last known, for when the app stops."""
    body = {name: result[name] for name in FIELDS}
    text = json.dumps(body, sort_keys=True)
    now = float(result["updated_at"])
    with _lock:
        last = _written.get(key)
        _saved[key] = body | {"updated_at": now}
        if last and last[0] == text and now - last[1] < SAVE_SECONDS:
            return
        _written[key] = (text, now)
    try:
        records.put(SAVED, _saved_name(key), body | {"updated_at": now}, paths or RuntimePaths())
    except Exception:  # a full disk or a locked database must not cost the person their figures
        LOG.warning("Could not keep home widget %s's figures", key[0], exc_info=True)


def _with_saved(result: dict[str, Any], key: tuple[str, str], paths: RuntimePaths | None) -> dict[str, Any]:
    """The last known figures, with their age, beside why there are no new ones; ``result`` if none."""
    with _lock:
        saved = _saved.get(key)
    if saved is None:
        try:
            saved = records.get(SAVED, _saved_name(key), paths or RuntimePaths())
        except Exception:
            LOG.warning("Could not read home widget %s's kept figures", key[0], exc_info=True)
            saved = {}
        with _lock:
            _saved[key] = saved
    if not saved:
        return result
    return result | figures(saved) | {"updated_at": float(saved.get("updated_at") or 0), "saved": True}


def drop_saved(app_id: str, paths: RuntimePaths | None = None) -> None:
    """Forget an app's kept figures (its data was deleted): they are a copy of what it held."""
    prefix = f"{app_id}{SEPARATOR}"
    with _lock:
        for store in (_written, _saved, _cache):
            for key in [key for key in store if key[0].startswith(prefix)]:
                store.pop(key, None)
    for name in records.names(SAVED, paths or RuntimePaths()):
        if name.startswith(prefix):
            records.delete(SAVED, name, paths or RuntimePaths())


def forget(kind: str) -> None:
    """Drop kept figures, so the next read asks the app (the widget's Refresh)."""
    with _lock:
        for key in [key for key in _cache if key[0] == kind]:
            _cache.pop(key, None)


def act(
    widget: Offered, action: str, *, is_admin: bool, uid: str = "", email: str = "", paths: RuntimePaths | None = None
) -> None:
    """Do what a ticked box asks (``<widget>_action`` in the app's ``widgets.py``), then read afresh."""
    try:
        _reader(widget.app, widget.spec, "_action")(
            _context(widget, is_admin=is_admin, uid=uid, email=email, paths=paths), action
        )
    except WidgetError:
        raise
    except Exception:  # the reader's own bug: logged, never shown with its details
        LOG.exception("Home widget action %s failed", widget.kind)
        raise WidgetError(f"{widget.app.manifest.name} did not take the change.") from None
    finally:
        forget(widget.kind)


def save_key(widget: Offered, key: str, *, uid: str, paths: RuntimePaths | None = None) -> None:
    """Keep the key pasted into a widget's settings (empty removes it); per person for personal widgets."""
    from ctl.store.secrets import SecretStore

    store = SecretStore(paths or RuntimePaths())
    name = _key_name(widget.app.id, uid if widget.spec.personal else "")
    if key.strip():
        store.put(KEYS, name, key.strip())
    else:
        store.delete(KEYS, name)
    forget(widget.kind)


def has_key(widget: Offered, *, uid: str, paths: RuntimePaths | None = None) -> bool:
    from ctl.store.secrets import SecretStore

    name = _key_name(widget.app.id, uid if widget.spec.personal else "")
    return bool(SecretStore(paths or RuntimePaths()).get(KEYS, name))


def run(coroutine: Any) -> Any:
    """Run an async client package's call from a reader (readers run in a worker thread)."""
    import asyncio

    return asyncio.run(coroutine)
