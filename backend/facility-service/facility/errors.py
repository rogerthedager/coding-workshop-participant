"""Domain errors carrying a stable machine-readable code (design section 12)."""

from fastapi import HTTPException


def fail(status: int, code: str, message: str, **details: object) -> HTTPException:
    """Build an HTTPException whose error envelope uses a specific code."""
    return HTTPException(status, {"code": code, "message": message, **details})
