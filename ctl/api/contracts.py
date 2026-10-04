"""Validate responses even when a route returns JSONResponse for status or headers."""

from __future__ import annotations

from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import TypeAdapter, ValidationError

from ctl.api.errors import response_error_handler
from ctl.api.models import ApiErrorResponse


class ContractRoute(APIRoute):
    def __init__(self, *args, **kwargs):
        responses = dict(kwargs.pop("responses", None) or {})
        for code in (400, 401, 403, 409, 422, 500, 503):
            responses.setdefault(code, {"model": ApiErrorResponse})
        super().__init__(*args, responses=responses, **kwargs)

    def get_route_handler(self):
        handler = super().get_route_handler()
        adapter = TypeAdapter(self.response_model) if self.response_model else None

        async def validated(request):
            response = await handler(request)
            response.headers["Cache-Control"] = "no-store"
            if adapter and isinstance(response, JSONResponse) and response.status_code < 400:
                try:
                    data = adapter.validate_json(bytes(response.body))
                except ValidationError as exc:
                    return response_error_handler(request, exc)
                response = JSONResponse(
                    data.model_dump(mode="json", exclude_none=self.response_model_exclude_none),
                    status_code=response.status_code,
                    headers={key: value for key, value in response.headers.items() if key != "content-length"},
                )
            return response

        return validated
