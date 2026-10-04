"""Shared test helpers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from unittest.mock import patch

from ctl.runtime import RuntimePaths

_RUNTIME_PATH_USERS = (
    "ctl.service_ops",
    "ctl.lifecycle.materialize",
    "ctl.lifecycle.accounts",
    "ctl.lifecycle.uninstall",
    "ctl.lifecycle.app_releases",
    "ctl.identity",
    "ctl.install",
    "ctl.people",
)


@contextmanager
def runtime_paths(paths: RuntimePaths) -> Iterator[None]:
    """Point every application-lifecycle module at a temporary runtime root."""
    with ExitStack() as stack:
        for module in _RUNTIME_PATH_USERS:
            stack.enter_context(patch(f"{module}.RuntimePaths", return_value=paths))
        yield
