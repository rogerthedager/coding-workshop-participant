"""FastAPI application factory and sanitized API error boundary."""

import json
import logging
import time
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

from facility.config import settings
from facility.database import SessionLocal
from facility.enums import APIErrorCode
from facility.routers import (
    attachments,
    auth,
    coordination,
    dashboards,
    facilities,
    incidents,
    notifications,
    people,
    teams,
)

logger = logging.getLogger("facility.requests")
app = FastAPI(
    title="ACME Facility Incident API",
    version="1.1.0",
    description="Core MVP. PostgreSQL only. All mutations enforce server permissions.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.resolved_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "If-Match"],
    expose_headers=["X-Request-ID", "ETag"],
)


def error_response(
    status: int, message: str, details: object = None, code: str | None = None
) -> JSONResponse:
    """Produce the public error contract without raw input or SQL text."""
    codes = {
        401: APIErrorCode.UNAUTHENTICATED,
        403: APIErrorCode.FORBIDDEN,
        404: APIErrorCode.NOT_FOUND,
        409: APIErrorCode.CONFLICT,
        422: APIErrorCode.VALIDATION_ERROR,
        428: APIErrorCode.PRECONDITION_REQUIRED,
        429: APIErrorCode.LOCKED,
        503: APIErrorCode.UNAVAILABLE,
        500: APIErrorCode.INTERNAL_ERROR,
    }
    return JSONResponse(
        status_code=status,
        content=jsonable_encoder(
            {
                "error": {
                    "code": code or codes.get(status, APIErrorCode.REQUEST_ERROR),
                    "message": message,
                    "details": details or {},
                }
            }
        ),
    )


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Normalize authorization and domain errors."""
    detail = exc.detail
    details = None
    code = None
    if isinstance(detail, dict):
        details = {key: value for key, value in detail.items() if key not in {"code", "message"}}
        code = detail.get("code")
    response = error_response(
        exc.status_code,
        detail if isinstance(detail, str) else detail.get("message", "Request rejected"),
        details,
        code,
    )
    if exc.headers:
        response.headers.update(exc.headers)
    return response


@app.exception_handler(RequestValidationError)
async def invalid_input(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Omit Pydantic input values, which may include credentials."""
    errors = exc.errors()
    skills = any(item["loc"][:2] == ("body", "skills") for item in errors)
    return error_response(
        422,
        "Invalid request",
        [
            {"location": item["loc"], "message": item["msg"], "type": item["type"]}
            for item in errors
        ],
        "skills_invalid" if skills else None,
    )


@app.exception_handler(IntegrityError)
async def constraint_error(request: Request, exc: IntegrityError) -> JSONResponse:
    """Hide driver SQL and credentials when uniqueness/reference checks fail."""
    return error_response(409, "A uniqueness or reference constraint was violated")


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """Report a request ID while keeping exception contents out of public output."""
    request_id = getattr(request.state, "request_id", None) or uuid4().hex
    logger.error(
        json.dumps(
            {
                "event": "unhandled_error",
                "request_id": request_id,
                "exception_type": type(exc).__name__,
            }
        )
    )
    # This handler runs outside the request middleware, so set the header here too.
    response = error_response(
        500,
        "Unexpected error; provide the request ID for support",
        {"request_id": request_id},
    )
    response.headers["X-Request-ID"] = request_id
    response.headers["Cache-Control"] = "no-store"
    return response


@app.middleware("http")
async def request_audit(request: Request, call_next):
    """Emit body-free request timing logs and prohibit caching private responses."""
    request.state.request_id = uuid4().hex
    start = time.monotonic()
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["Cache-Control"] = "no-store"
    logger.info(
        json.dumps(
            {
                "request_id": request.state.request_id,
                "method": request.method,
                "status": response.status_code,
                "elapsed_ms": round((time.monotonic() - start) * 1000, 2),
            }
        )
    )
    return response


@app.get("/health", tags=["health"])
@app.get("/api/health", tags=["health"], include_in_schema=False)
async def health() -> dict:
    """Verify actual database connectivity without returning configuration."""
    try:
        async with SessionLocal() as db:
            await db.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(503, "Database unavailable") from exc
    # No version or host details: the endpoint is public (NFR-6).
    return {"status": "healthy"}


# coordination precedes incidents so /incidents/similar is not parsed as an ID.
for route_module in [
    auth,
    people,
    teams,
    facilities,
    coordination,
    incidents,
    attachments,
    notifications,
    dashboards,
]:
    app.include_router(route_module.router, prefix="/api/v1")
