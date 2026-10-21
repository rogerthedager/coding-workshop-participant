"""Current-database authorization rather than trusting stale JWT roles."""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from facility.database import get_db
from facility.enums import UserRole
from facility.models import RefreshSession, User, utcnow
from facility.security import decode_access_token

bearer = HTTPBearer(auto_error=False)


async def authenticated_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """Authenticate active users, including those required to change a password."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Bearer"})
    claims = decode_access_token(credentials.credentials)
    try:
        if not isinstance(claims.get("sub"), str) or not isinstance(claims.get("sid"), str):
            raise ValueError("Invalid identity claims")
        user_id = UUID(claims["sub"])
        family_id = UUID(claims["sid"])
    except (ValueError, TypeError, KeyError):
        raise HTTPException(401, "Invalid access token") from None
    user = await db.get(User, user_id)
    if user is None or not user.is_active or user.token_version != claims.get("ver"):
        raise HTTPException(401, "Session is no longer valid")
    live_session = await db.scalar(
        select(RefreshSession.id).where(
            RefreshSession.user_id == user_id,
            RefreshSession.family_id == family_id,
            RefreshSession.revoked_at.is_(None),
            RefreshSession.rotated_at.is_(None),
            RefreshSession.expires_at > utcnow(),
        ).limit(1)
    )
    if live_session is None:
        raise HTTPException(401, "Session is no longer valid")
    return user


async def current_user(user: Annotated[User, Depends(authenticated_user)]) -> User:
    """Require the temporary-password flow to finish before business operations."""
    if user.must_change_password:
        raise HTTPException(
            403,
            {
                "code": "password_change_required",
                "message": "Change your temporary password before continuing",
            },
        )
    return user


async def require_admin(user: Annotated[User, Depends(current_user)]) -> User:
    """Require an additive admin role from the current database row."""
    if UserRole.ADMIN not in user.roles:
        raise HTTPException(403, "Administrator access required")
    return user
