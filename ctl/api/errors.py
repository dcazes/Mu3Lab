"""API error type and the JSON envelope every failed request uses."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse


class ApiError(Exception):
    """Raised by handlers and dependencies; rendered as `{"ok": false, "error": ...}`."""

    def __init__(
        self, status_code: int, error: str | dict[str, Any], *, headers: dict[str, str] | None = None, **extra: Any
    ) -> None:
        super().__init__(error if isinstance(error, str) else str(error.get("message", "")))
        self.status_code = status_code
        self.error = error
        self.headers = headers
        self.extra = extra


def api_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    return JSONResponse(
        {"ok": False, "error": exc.error, **exc.extra}, status_code=exc.status_code, headers=exc.headers
    )
