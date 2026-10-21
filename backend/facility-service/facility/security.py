"""Password hashing and purpose-bound signed/opaque tokens."""

import hashlib
import re
import secrets
from datetime import timedelta

import bcrypt
import jwt
from fastapi import HTTPException

from facility.config import settings
from facility.models import User, utcnow


def validate_password(password: str) -> str:
    """Validate bcrypt's byte limit and ACME password strength policy."""
    if (
        len(password) < 10
        or len(password.encode("utf-8")) > 72
        or not re.search(r"[^\W\d_]", password, re.UNICODE)
        or not re.search(r"[0-9]", password)
    ):
        raise ValueError(
            "Password needs 10+ characters, a letter and digit, and at most 72 UTF-8 bytes"
        )
    return password


def normalize_email(email: str) -> str:
    """Normalize and validate the organization-only registration domain."""
    value = email.strip().lower()
    if len(value) > 254 or not re.fullmatch(r"[^@\s]+@acme\.inc", value):
        raise ValueError("An @acme.inc email address is required")
    return value


def hash_password(password: str) -> str:
    """Hash a validated password with bcrypt cost 12."""
    validate_password(password)
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(password: str, encoded: str) -> bool:
    """Reject overlong or malformed credentials without truncating."""
    if len(password.encode("utf-8")) > 72:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), encoded.encode("ascii"))
    except (ValueError, UnicodeError):
        return False


def signing_secret() -> str:
    """Refuse issuing or accepting tokens until an operator provides a secret."""
    try:
        secret = settings.resolved_jwt_secret()
    except RuntimeError:
        raise HTTPException(503, "Authentication is not configured") from None
    if len(secret.encode("utf-8")) < 32:
        raise HTTPException(503, "Authentication is not configured")
    return secret


def create_access_token(user: User) -> str:
    """Bind access tokens to the user's current database token version."""
    now = utcnow()
    return jwt.encode(
        {
            "sub": str(user.id),
            "ver": user.token_version,
            "iat": now,
            "exp": now + timedelta(minutes=settings.access_minutes),
            "iss": "acme-facility",
            "aud": "facility-service",
            "jti": secrets.token_hex(16),
        },
        signing_secret(),
        algorithm="HS256",
    )


def decode_access_token(token: str) -> dict:
    """Validate signature, algorithm, purpose and required claims."""
    try:
        return jwt.decode(
            token,
            signing_secret(),
            algorithms=["HS256"],
            audience="facility-service",
            issuer="acme-facility",
            options={"require": ["sub", "ver", "iat", "exp", "iss", "aud", "jti"]},
        )
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid or expired access token") from None


def hash_refresh_token(raw: str) -> str:
    """Store only the SHA-256 digest of a cryptographically random token."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def new_refresh_token() -> str:
    """Return a 384-bit opaque browser session secret."""
    return secrets.token_urlsafe(48)
