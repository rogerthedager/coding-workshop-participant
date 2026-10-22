"""Password hashing and purpose-bound signed/opaque tokens."""

import functools
import hashlib
import re
import secrets
import unicodedata
from datetime import timedelta
from uuid import UUID

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


NAME_PUNCTUATION = frozenset(" -'’.")
NAME_RULE = (
    "Enter your real name using letters only "
    "(spaces, hyphens, apostrophes and periods are allowed)"
)


def _is_letter(char: str) -> bool:
    # Letters in any script, plus combining marks such as accents.
    return unicodedata.category(char)[0] in {"L", "M"}


def validate_full_name(value: str) -> str:
    """A person's display name: letters in any language, no digits or symbols.

    Extra spaces are collapsed, so "  Mary   Jane " is stored as "Mary Jane".
    """
    name = " ".join(value.split())
    if not name:
        raise ValueError("Full name is required")
    if not all(_is_letter(char) or char in NAME_PUNCTUATION for char in name):
        raise ValueError(NAME_RULE)
    if not unicodedata.category(name[0]).startswith("L"):
        raise ValueError("Name must start with a letter")
    if sum(_is_letter(char) for char in name) < 2:
        raise ValueError("Name must have at least 2 letters")
    return name


def normalize_email(email: str) -> str:
    """Accept corporate ASCII dot-atom addresses on the exact ACME domain."""
    value = email.strip().lower()
    local, separator, domain = value.rpartition("@")
    atom = r"[a-z0-9!#$%&'*+/=?^_`{|}~-]+"
    if (
        separator != "@"
        or domain != "acme.inc"
        or not 1 <= len(local) <= 64
        or not re.fullmatch(rf"{atom}(?:\.{atom})*", local)
    ):
        raise ValueError("An @acme.inc email address is required")
    return value


def hash_password(password: str) -> str:
    """Hash a validated password with bcrypt cost 12."""
    validate_password(password)
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("ascii")


@functools.cache
def dummy_password_hash() -> str:
    """A cost-12 hash to check against when the account is unknown or locked."""
    return bcrypt.hashpw(secrets.token_bytes(16), bcrypt.gensalt(rounds=12)).decode("ascii")


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


def create_access_token(user: User, family_id: UUID) -> str:
    """Bind access to the user version and a revocable login-session family."""
    now = utcnow()
    return jwt.encode(
        {
            "sub": str(user.id),
            "ver": user.token_version,
            "sid": str(family_id),
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
            options={"require": ["sub", "ver", "sid", "iat", "exp", "iss", "aud", "jti"]},
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            401, {"code": "token_expired", "message": "Access token has expired"}
        ) from None
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid access token") from None


def hash_refresh_token(raw: str) -> str:
    """Store only the SHA-256 digest of a cryptographically random token."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def new_refresh_token() -> str:
    """Return a 384-bit opaque browser session secret."""
    return secrets.token_urlsafe(48)
