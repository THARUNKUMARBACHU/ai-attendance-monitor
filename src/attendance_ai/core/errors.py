"""Application errors, rendered as RFC 9457 problem-details responses."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

PROBLEM_JSON = "application/problem+json"


class AppError(Exception):
    """An error with a defined HTTP status and a message that is safe to show callers."""

    status_code = 500
    code = "internal_error"
    title = "Internal server error"

    def __init__(self, detail: str | None = None) -> None:
        self.detail = detail or self.title
        super().__init__(self.detail)


class AuthenticationError(AppError):
    status_code = 401
    code = "unauthenticated"
    title = "Authentication required"


class PermissionDeniedError(AppError):
    status_code = 403
    code = "forbidden"
    title = "Access denied"


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"
    title = "Not found"


def problem_response(
    request: Request, status_code: int, code: str, title: str, detail: str, **extra: Any
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": f"urn:attendance-ai:problem:{code}",
        "title": title,
        "status": status_code,
        "detail": detail,
        "instance": request.url.path,
        "code": code,
        "request_id": getattr(request.state, "request_id", None),
        **extra,
    }
    headers = {"WWW-Authenticate": "Bearer"} if status_code == 401 else None
    return JSONResponse(body, status_code=status_code, media_type=PROBLEM_JSON, headers=headers)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        return problem_response(request, exc.status_code, exc.code, exc.title, exc.detail)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Echo where and why validation failed, never the submitted values.
        errors = [
            {"loc": list(error["loc"]), "msg": error["msg"], "type": error["type"]} for error in exc.errors()
        ]
        return problem_response(
            request, 422, "validation_error", "Invalid request", "The request is invalid.", errors=errors
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = "not_found" if exc.status_code == 404 else "http_error"
        return problem_response(request, exc.status_code, code, str(exc.detail), str(exc.detail))

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "unhandled_error", extra={"fields": {"request_id": getattr(request.state, "request_id", None)}}
        )
        return problem_response(
            request, 500, "internal_error", "Internal server error", "An unexpected error occurred."
        )
