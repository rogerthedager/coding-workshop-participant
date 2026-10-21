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
from facility.routers import auth, dashboards, facilities, incidents, people

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
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "If-Match"],
    expose_headers=["X-Request-ID", "ETag"],
)


def error_response(status: int, message: str, details: object = None) -> JSONResponse:
    """Produce the public error contract without raw input or SQL text."""
    codes = {
        401: "unauthenticated",
        403: "forbidden",
        404: "not_found",
        409: "conflict",
        422: "validation_error",
        428: "precondition_required",
        429: "locked",
        503: "unavailable",
        500: "internal_error",
    }
    return JSONResponse(
        status_code=status,
        content=jsonable_encoder(
            {
                "error": {
                    "code": codes.get(status, "request_error"),
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
    response = error_response(
        exc.status_code,
        detail if isinstance(detail, str) else detail.get("message", "Request rejected"),
        detail if isinstance(detail, dict) else None,
    )
    if exc.headers:
        response.headers.update(exc.headers)
    return response


@app.exception_handler(RequestValidationError)
async def invalid_input(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Omit Pydantic input values, which may include credentials."""
    return error_response(
        422,
        "Invalid request",
        [
            {"location": item["loc"], "message": item["msg"], "type": item["type"]}
            for item in exc.errors()
        ],
    )


@app.exception_handler(IntegrityError)
async def constraint_error(request: Request, exc: IntegrityError) -> JSONResponse:
    """Hide driver SQL and credentials when uniqueness/reference checks fail."""
    return error_response(409, "A uniqueness or reference constraint was violated")


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """Report a request ID while keeping exception contents out of public output."""
    logger.error(
        json.dumps(
            {
                "event": "unhandled_error",
                "request_id": getattr(request.state, "request_id", "unknown"),
                "exception_type": type(exc).__name__,
            }
        )
    )
    return error_response(500, "Unexpected error; provide the request ID for support")


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
    return {"status": "healthy", "database": "postgresql", "version": "1.1.0"}


for route_module in [auth, people, facilities, incidents, dashboards]:
    app.include_router(route_module.router, prefix="/api/v1")
