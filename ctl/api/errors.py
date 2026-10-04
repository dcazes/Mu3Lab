"""API error type and the JSON envelope every failed request uses."""

from __future__ import annotations

import logging

from fastapi import Request
from fastapi.exceptions import RequestValidationError, ResponseValidationError
from fastapi.responses import JSONResponse
from pydantic import JsonValue, ValidationError


class ApiError(Exception):
    """Raised by handlers and dependencies; rendered as `{"ok": false, "error": ...}`."""

    def __init__(
        self,
        status_code: int,
        error: str | dict[str, JsonValue],
        *,
        headers: dict[str, str] | None = None,
        **extra: JsonValue,
    ) -> None:
        super().__init__(error if isinstance(error, str) else str(error.get("message", "")))
        self.status_code = status_code
        self.error = error
        self.headers = headers
        self.extra = extra


def api_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    detail = exc.error if isinstance(exc.error, dict) else {"message": exc.error}
    return JSONResponse(
        {
            "ok": False,
            "error": exc.error,
            "code": detail.get("code", exc.extra.get("code", "request_failed")),
            "message": detail.get("message", str(exc.error)),
            "recommended_action": detail.get("recommended_action", "Review the message and try again."),
            **exc.extra,
        },
        status_code=exc.status_code,
        headers={"Cache-Control": "no-store", **(exc.headers or {})},
    )


def validation_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    # Only field names and validation descriptions are returned. Never inputs:
    # the rejected request may contain a master password or a provider key.
    first = exc.errors()[0]
    field = ".".join(str(part) for part in first["loc"] if part not in {"body", "query"})
    message = f"Check {field or 'the request'}: {first['msg']}"
    return JSONResponse(
        {
            "ok": False,
            "error": message,
            "code": "invalid_request",
            "message": message,
            "recommended_action": "Correct the highlighted field and try again.",
        },
        status_code=422,
        headers={"Cache-Control": "no-store"},
    )


def response_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, (ResponseValidationError, ValidationError))
    logging.getLogger(__name__).error("API response failed its contract: %s", _request.url.path)
    # Pydantic diagnostics include payloads; expose neither those values nor a traceback.
    message = "Mu3Lab returned incomplete information. Refresh and check the worker logs."
    return JSONResponse(
        {
            "ok": False,
            "error": message,
            "code": "invalid_response",
            "message": message,
            "recommended_action": "Refresh and check the worker logs.",
        },
        status_code=500,
        headers={"Cache-Control": "no-store"},
    )
