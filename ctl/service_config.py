"""Typed, registry-owned application configuration with write-only secrets.

The values live in the app's canonical settings (``ctl.app_settings``); the
project's ``.env`` only receives them. A write is checked against the
revision the form was loaded with, so two people editing at once cannot
silently overwrite each other, and it runs under the app's lock, so it never
interleaves with a job rendering or changing that app.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ctl import resource_locks
from ctl.app_settings import AppSettings, managed_names
from ctl.control_state import ControlState, RevisionConflict
from ctl.registry import Service
from ctl.runtime import RuntimePaths
from ctl.secret_file import write_atomic
from ctl.secrets import read_runtime_env, runtime_env_text

# A configuration change waits this long for a job working on the app.
LOCK_SECONDS = 10


class ConfigurationBusy(RuntimeError):
    """The app is busy with a job; nothing was changed."""


def _path(service: Service) -> Path:
    return RuntimePaths().projects / service.id / ".env"


def _settings(service: Service) -> AppSettings:
    settings = AppSettings(service.id, RuntimePaths())
    manifest = getattr(service, "manifest", None)
    if manifest is not None:
        settings.ensure_imported(manifest, _path(service))
    return settings


def managed_keys(service: Service) -> set[str]:
    """Fields whose variable Mu3Lab sets itself; they are not shown or accepted."""
    manifest = getattr(service, "manifest", None)
    managed = managed_names(manifest) if manifest is not None else set()
    return {str(field["key"]) for field in service.configuration if field["env"] in managed}


def revision(service: Service) -> int:
    state = ControlState.runtime()
    installation = state.installation(service.id) if state else None
    return int(installation["config_revision"]) if installation else 0


def read(service: Service) -> list[dict[str, Any]]:
    values = _settings(service).config()
    result: list[dict[str, Any]] = []
    for field in service.configuration:
        key = str(field["key"])
        secret = field["type"] == "secret"
        item = {name: value for name, value in field.items() if name != "env"}
        item["secret_present"] = bool(values.get(key)) if secret else False
        item["value"] = None if secret else values.get(key, field.get("default"))
        result.append(item)
    return result


def _serialize(field: dict[str, Any], value: Any) -> str:
    field_type = field["type"]
    if field_type == "boolean":
        if not isinstance(value, bool):
            raise ValueError(f"{field['key']} must be true or false")
        return "true" if value else "false"
    if field_type == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{field['key']} must be an integer")
        minimum, maximum = int(field.get("min", 0)), int(field.get("max", 65535))
        if value < minimum or value > maximum:
            raise ValueError(f"{field['key']} must be between {minimum} and {maximum}")
        return str(value)
    if not isinstance(value, str) or "\n" in value or "\r" in value:
        raise ValueError(f"{field['key']} must be a single-line string")
    if field_type == "enum" and value not in field.get("options", []):
        raise ValueError(f"{field['key']} has an unsupported value")
    if len(value) > int(field.get("max_length", 2048)):
        raise ValueError(f"{field['key']} is too long")
    return value


def write(service: Service, submitted: dict[str, Any], *, expected_revision: int | None = None) -> list[dict[str, Any]]:
    """Save the submitted fields; raise ``RevisionConflict`` if the form was stale."""
    if not isinstance(submitted, dict):
        raise ValueError("configuration values must be an object")
    fields = {str(field["key"]): field for field in service.configuration}
    unknown = set(submitted) - set(fields)
    if unknown:
        raise ValueError(f"unknown configuration fields: {', '.join(sorted(unknown))}")
    managed = set(submitted) & managed_keys(service)
    if managed:
        raise ValueError(f"Mu3Lab manages {', '.join(sorted(managed))}; it cannot be changed here")
    try:
        with resource_locks.hold(f"app:{service.id}", timeout=LOCK_SECONDS, paths=RuntimePaths()):
            settings = _settings(service)
            values = settings.config()
            for key, raw_value in submitted.items():
                field = fields[key]
                if field["type"] == "secret" and (raw_value is None or raw_value == ""):
                    continue
                values[key] = _serialize(field, raw_value)
            for key, field in fields.items():
                if key not in values and "default" in field and field["default"] is not None:
                    values[key] = _serialize(field, field["default"])
                if field.get("required") and not values.get(key):
                    raise ValueError(f"{key} is required")
            state = ControlState.runtime()
            if state:
                # Checked and counted before anything changes; a stale form changes nothing.
                state.bump_config_revision(service.id, expected=expected_revision)
            settings.set_config(values)
            _publish(service, values)
    except resource_locks.Busy:
        raise ConfigurationBusy(f"{service.name} is busy with another task; try again in a moment.") from None
    return read(service)


def _publish(service: Service, values: dict[str, str]) -> None:
    """Put the operator's values into ``.env`` now; the next render reproduces them."""
    target = _path(service)
    target.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    env = read_runtime_env(target)
    managed = managed_keys(service)
    for field in service.configuration:
        key = str(field["key"])
        if key in values and key not in managed:
            env[str(field["env"])] = values[key]
    write_atomic(target, runtime_env_text(env).encode())


def missing_required(service: Service) -> list[str]:
    required = [field for field in service.configuration if field.get("required")]
    if not required:
        return []
    values = _settings(service).config()
    return [str(field["key"]) for field in required if not (values.get(str(field["key"])) or field.get("default"))]


__all__ = ["ConfigurationBusy", "RevisionConflict", "managed_keys", "missing_required", "read", "revision", "write"]
