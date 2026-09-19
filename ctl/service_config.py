"""Typed, registry-owned application configuration with write-only secrets."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ctl.control_state import ControlState
from ctl.registry import Service
from ctl.runtime import RuntimePaths
from ctl.secrets import read_runtime_env, runtime_env_text


def _path(service: Service) -> Path:
    return RuntimePaths().projects / service.id / ".env"


def read(service: Service) -> list[dict[str, Any]]:
    values = read_runtime_env(_path(service))
    result: list[dict[str, Any]] = []
    for field in service.configuration:
        env_name = str(field["env"])
        secret = field["type"] == "secret"
        item = {key: value for key, value in field.items() if key != "env"}
        item["secret_present"] = bool(values.get(env_name)) if secret else False
        item["value"] = None if secret else values.get(env_name, field.get("default"))
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


def write(service: Service, submitted: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(submitted, dict):
        raise ValueError("configuration values must be an object")
    fields = {str(field["key"]): field for field in service.configuration}
    unknown = set(submitted) - set(fields)
    if unknown:
        raise ValueError(f"unknown configuration fields: {', '.join(sorted(unknown))}")
    target = _path(service)
    target.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    values = read_runtime_env(target)
    for key, raw_value in submitted.items():
        field = fields[key]
        if field["type"] == "secret" and (raw_value is None or raw_value == ""):
            continue
        values[str(field["env"])] = _serialize(field, raw_value)
    for field in service.configuration:
        env_name = str(field["env"])
        if env_name not in values and "default" in field:
            values[env_name] = _serialize(field, field["default"])
        if field.get("required") and not values.get(env_name):
            raise ValueError(f"{field['key']} is required")
    temporary = target.with_suffix(".tmp")
    temporary.write_text(runtime_env_text(values), encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(target)
    state = ControlState.runtime()
    if state:
        state.bump_config_revision(service.id)
    return read(service)


def missing_required(service: Service) -> list[str]:
    values = read_runtime_env(_path(service))
    return [str(field["key"]) for field in service.configuration
            if field.get("required") and not values.get(str(field["env"]))]
