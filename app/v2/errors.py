"""Safe v2 error envelopes. Internal details remain in logs."""

import logging
from uuid import uuid4

from fastapi import Request
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.models.v2 import ErrorEnvelope

logger = logging.getLogger(__name__)


class V2Error(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details


def is_v2(request):
    return request.url.path == "/v2" or request.url.path.startswith("/v2/")


def error_body(request, code, message, details=None):
    request_id = getattr(request.state, "request_id", None) or str(uuid4())
    request.state.request_id = request_id
    return ErrorEnvelope(
        error={"code": code, "message": message, "request_id": request_id, "details": details}
    ).model_dump(exclude_none=True)


def unavailable(message="Public data is unavailable"):
    return V2Error(503, "data_unavailable", message)


def install_errors(app):
    @app.middleware("http")
    async def request_identity(request: Request, call_next):
        if not is_v2(request):
            return await call_next(request)
        request.state.request_id = str(uuid4())
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("Unhandled v2 error, request %s", request.state.request_id)
            response = JSONResponse(
                status_code=500,
                content=error_body(request, "internal_error", "Internal server error"),
            )
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(V2Error)
    async def typed_error(request, error):
        return JSONResponse(
            status_code=error.status,
            content=error_body(request, error.code, error.message, error.details),
        )

    @app.exception_handler(HTTPException)
    async def http_error(request, error):
        if not is_v2(request):
            return await http_exception_handler(request, error)
        codes = {
            400: "invalid_cursor",
            404: "not_found",
            409: "ambiguous_population",
            410: "cursor_expired",
            422: "invalid_query",
            429: "rate_limited",
            503: "data_unavailable",
        }
        return JSONResponse(
            status_code=error.status_code,
            content=error_body(
                request,
                codes.get(error.status_code, "internal_error"),
                "Resource not found" if error.status_code == 404 else "Request failed",
            ),
            headers=error.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        if not is_v2(request):
            return await request_validation_exception_handler(request, error)
        return JSONResponse(
            status_code=422,
            content=error_body(request, "invalid_query", "Invalid request parameters"),
        )
