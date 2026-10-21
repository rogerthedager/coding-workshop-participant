"""Registration, persistent lockout and rotating browser sessions."""

import uuid
from datetime import datetime, timedelta
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from facility.config import settings
from facility.database import get_db
from facility.dependencies import authenticated_user
from facility.models import EngineerProfile, RefreshSession, Team, User, utcnow
from facility.security import (
    create_access_token,
    dummy_password_hash,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    normalize_email,
    signing_secret,
    validate_password,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])
COOKIE_NAME = "facility_refresh"
Db = Annotated[AsyncSession, Depends(get_db)]
Identity = Annotated[User, Depends(authenticated_user)]


class StrictModel(BaseModel):
    """Reject unknown properties rather than silently accepting role injection."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class Registration(StrictModel):
    """Self-service employee registration; roles are never client supplied."""

    email: str
    full_name: str = Field(min_length=1, max_length=120)
    password: str
    _email = field_validator("email")(normalize_email)
    _password = field_validator("password")(validate_password)

    @field_validator("full_name")
    @classmethod
    def nonblank_name(cls, value: str) -> str:
        """Store a nonempty display name."""
        if not value.strip():
            raise ValueError("Full name is required")
        return value.strip()


class Login(StrictModel):
    """Login deliberately returns generic errors for unknown domains/users."""

    email: str = Field(max_length=254)
    password: str = Field(max_length=1000)


class PasswordChange(StrictModel):
    """Password update invalidates every old access/refresh session."""

    current_password: str = Field(max_length=1000)
    new_password: str
    _new_password = field_validator("new_password")(validate_password)


def user_output(user: User) -> dict:
    """Public account projection without credential or lockout material."""
    return {
        "id": str(user.id),
        "email": user.email,
        "full_name": user.full_name,
        "roles": user.roles,
        "is_active": user.is_active,
        "must_change_password": user.must_change_password,
        "default_seat_id": str(user.default_seat_id) if user.default_seat_id else None,
    }


def require_same_origin(request: Request) -> None:
    """Protect cookie mutations; allow headerless non-browser local/API clients."""
    site = request.headers.get("sec-fetch-site")
    if site == "cross-site":
        raise HTTPException(403, "Cross-origin session mutation is forbidden")
    origin = request.headers.get("origin")
    referer = request.headers.get("referer")
    if not origin and referer:
        parts = urlsplit(referer)
        origin = f"{parts.scheme}://{parts.netloc}"
    if origin:
        own = f"{request.url.scheme}://{request.url.netloc}"
        permitted = {own}
        permitted.update(settings.resolved_origins())
        if origin.rstrip("/") not in permitted:
            raise HTTPException(403, "Cross-origin session mutation is forbidden")
    elif site is not None and site != "none":
        raise HTTPException(403, "An origin is required for browser session mutation")


def set_refresh_cookie(response: Response, raw: str) -> None:
    """Set a seven-day, HTTP-only, strictly same-site session cookie."""
    response.set_cookie(
        COOKIE_NAME,
        raw,
        max_age=settings.refresh_days * 86400,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="strict",
        path="/",
    )


async def revoke_sessions(db: AsyncSession, user_id: uuid.UUID) -> None:
    """Revoke all of an identity's refresh sessions in the current transaction."""
    await db.execute(
        update(RefreshSession)
        .where(RefreshSession.user_id == user_id, RefreshSession.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )


async def issue_session(
    db: AsyncSession,
    user: User,
    response: Response,
    family_id: uuid.UUID | None = None,
) -> tuple[dict, RefreshSession]:
    """Add an uncommitted session and attach its opaque browser token."""
    family_id = family_id or uuid.uuid4()
    access = create_access_token(user, family_id)
    raw = new_refresh_token()
    session = RefreshSession(
        id=uuid.uuid4(),
        user_id=user.id,
        token_hash=hash_refresh_token(raw),
        family_id=family_id,
        expires_at=utcnow() + timedelta(days=settings.refresh_days),
    )
    db.add(session)
    set_refresh_cookie(response, raw)
    return {
        "access_token": access,
        "token_type": "bearer",
        "expires_in": settings.access_minutes * 60,
        "user": user_output(user),
    }, session


@router.post("/register", status_code=201)
async def register(payload: Registration, db: Db, request: Request) -> dict:
    """Register an employee with a normalized, database-unique ACME email."""
    require_same_origin(request)
    user = User(
        email=payload.email,
        full_name=payload.full_name,
        password_hash=hash_password(payload.password),
        roles=[],
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            409, {"code": "duplicate_email", "message": "An account already exists for that email"}
        ) from None
    return user_output(user)


@router.post("/login")
async def login(payload: Login, response: Response, db: Db, request: Request) -> dict:
    """Serialize failures per identity so lockout persists across instances."""
    require_same_origin(request)
    signing_secret()
    user = await db.scalar(
        select(User).where(User.email == payload.email.strip().lower()).with_for_update()
    )
    now = utcnow()
    if user is None or not user.is_active or (user.locked_until and user.locked_until > now):
        # Spend the same bcrypt time as a real check so timing does not reveal
        # which accounts exist or are locked.
        verify_password(payload.password, dummy_password_hash())
        raise HTTPException(401, "Invalid email or password")
    if not verify_password(payload.password, user.password_hash):
        window = now - timedelta(minutes=15)
        failures = [
            stamp for stamp in user.failed_login_at if datetime.fromisoformat(stamp) > window
        ]
        failures.append(now.isoformat())
        user.failed_login_at = failures
        if len(failures) >= 5:
            user.locked_until = now + timedelta(minutes=15)
        await db.commit()
        raise HTTPException(401, "Invalid email or password")
    user.failed_login_at = []
    user.locked_until = None
    result, _ = await issue_session(db, user, response)
    await db.commit()
    return result


@router.get("/me")
async def me(user: Identity, db: Db) -> dict:
    """Inspect the current account, its skills, team and led team."""
    result = user_output(user)
    profile = await db.scalar(select(EngineerProfile).where(EngineerProfile.user_id == user.id))
    result["engineer"] = (
        {
            "profile_id": str(profile.id),
            "is_active": profile.is_active,
            "availability": profile.availability,
            "skills": profile.skills,
            "team_id": str(profile.team_id) if profile.team_id else None,
        }
        if profile is not None
        else None
    )
    led = await db.scalar(select(Team).where(Team.lead_user_id == user.id))
    result["leads_team"] = {"id": str(led.id), "name": led.name} if led else None
    return result


async def locked_refresh(
    request: Request,
    db: AsyncSession,
) -> tuple[RefreshSession | None, User | None]:
    """Lock user before session, matching password-change/revocation lock order."""
    raw = request.cookies.get(COOKIE_NAME)
    if not raw or len(raw) > 256:
        return None, None
    stored = await db.scalar(
        select(RefreshSession).where(RefreshSession.token_hash == hash_refresh_token(raw))
    )
    if stored is None:
        return None, None
    user = await db.scalar(
        select(User)
        .where(User.id == stored.user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    stored = await db.scalar(
        select(RefreshSession)
        .where(RefreshSession.id == stored.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return stored, user


@router.post("/refresh")
async def refresh(request: Request, response: Response, db: Db) -> dict:
    """One-time rotation; replay revokes the entire lineage, including successors."""
    require_same_origin(request)
    stored, user = await locked_refresh(request, db)
    if stored is None or user is None:
        raise HTTPException(401, "Invalid refresh session")
    now = utcnow()
    if stored.revoked_at is not None or stored.rotated_at is not None:
        await db.execute(
            update(RefreshSession)
            .where(
                RefreshSession.family_id == stored.family_id,
                RefreshSession.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        await db.commit()
        raise HTTPException(401, "Refresh session has been revoked")
    if stored.expires_at <= now or not user.is_active:
        stored.revoked_at = now
        await db.commit()
        raise HTTPException(401, "Refresh session has expired")
    result, successor = await issue_session(db, user, response, stored.family_id)
    await db.flush()
    stored.revoked_at = now
    stored.rotated_at = now
    stored.replaced_by_id = successor.id
    await db.commit()
    return result


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response, db: Db) -> None:
    """Idempotently revoke the presented browser session's entire family."""
    require_same_origin(request)
    stored, _ = await locked_refresh(request, db)
    if stored:
        await db.execute(
            update(RefreshSession)
            .where(
                RefreshSession.family_id == stored.family_id,
                RefreshSession.revoked_at.is_(None),
            )
            .values(revoked_at=utcnow())
        )
        await db.commit()
    response.delete_cookie(
        COOKIE_NAME,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="strict",
    )


@router.post("/change-password")
async def change_password(
    payload: PasswordChange,
    response: Response,
    db: Db,
    user: Identity,
    request: Request,
) -> dict:
    """Invalidate old sessions and replace the current session after a password change."""
    require_same_origin(request)
    user = await db.scalar(
        select(User)
        .where(User.id == user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not verify_password(payload.current_password, user.password_hash):
        # 422, not 401: a 401 tells the client its session expired.
        raise HTTPException(
            422,
            {"code": "invalid_current_password", "message": "Current password is incorrect"},
        )
    if payload.new_password == payload.current_password:
        raise HTTPException(422, "The new password must differ from the current password")
    signing_secret()
    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    user.failed_login_at = []
    user.locked_until = None
    user.token_version += 1
    await revoke_sessions(db, user.id)
    result, _ = await issue_session(db, user, response)
    await db.commit()
    return result
