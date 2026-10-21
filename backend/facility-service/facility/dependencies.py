"""Current-database authorization rather than trusting stale JWT roles."""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from facility.database import get_db
from facility.models import User
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
        user_id = UUID(claims["sub"])
    except (ValueError, TypeError, KeyError):
        raise HTTPException(401, "Invalid access token") from None
    user = await db.get(User, user_id)
    if user is None or not user.is_active or user.token_version != claims.get("ver"):
        raise HTTPException(401, "Session is no longer valid")
    return user


async def current_user(user: Annotated[User, Depends(authenticated_user)]) -> User:
    """Require the temporary-password flow to finish before business operations."""
    if user.must_change_password:
        raise HTTPException(403, "Change your temporary password before continuing")
    return user


async def require_admin(user: Annotated[User, Depends(current_user)]) -> User:
    """Require an additive admin role from the current database row."""
    if "admin" not in user.roles:
        raise HTTPException(403, "Administrator access required")
    return user
