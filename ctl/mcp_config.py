"""Write-only credential configuration for checked-in MCP catalog entries."""

from __future__ import annotations

import os
from typing import Any

from ctl.mcp_registry import credential_path
from ctl.secrets import read_runtime_env, runtime_env_text


def write(server, submitted: dict[str, Any]) -> None:
    if not isinstance(submitted, dict):
        raise ValueError("MCP configuration values must be an object")
    fields = {str(field["key"]): field for field in server.credentials}
    unknown = set(submitted) - set(fields)
    if unknown:
        raise ValueError(f"unknown MCP configuration fields: {', '.join(sorted(unknown))}")
    target = credential_path(server.id)
    target.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    values = read_runtime_env(target)
    for key, value in submitted.items():
        field = fields[key]
        if field["type"] == "secret" and (value is None or value == ""):
            continue
        if not isinstance(value, str) or not value or "\n" in value or "\r" in value or len(value) > 2048:
            raise ValueError(f"{key} must be a non-empty single-line value")
        prefix = str(field.get("prefix", ""))
        if prefix and not value.startswith(prefix):
            raise ValueError(f"{key} must use the expected {prefix}… format")
        values[str(field["env"])] = value
    missing = [str(field["key"]) for field in server.credentials
               if field.get("required") and not values.get(str(field["env"]))]
    if missing:
        raise ValueError("required MCP configuration is missing: " + ", ".join(missing))
    temporary = target.with_suffix(".tmp")
    temporary.write_text(runtime_env_text(values), encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(target)
