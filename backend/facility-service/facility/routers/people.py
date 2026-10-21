"""Administrator people maintenance and engineer availability/workloads."""

import secrets
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field, field_validator
from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from facility.database import get_db
from facility.dependencies import current_user, require_admin
from facility.enums import (
    MAX_ENGINEER_SKILLS,
    NON_FINAL_STATUSES,
    REQUIRED_ENGINEER_SKILLS,
    EngineerAvailability,
    EngineerSkill,
    IncidentStatus,
    UserRole,
)
from facility.errors import fail
from facility.models import (
    Building,
    EngineerProfile,
    Floor,
    Incident,
    Seat,
    Team,
    User,
    validate_skill_list,
)
from facility.routers.auth import StrictModel, revoke_sessions, user_output
from facility.security import hash_password, normalize_email, validate_password

router = APIRouter(tags=["Users and engineers"])
Db = Annotated[AsyncSession, Depends(get_db)]
Actor = Annotated[User, Depends(current_user)]
Admin = Annotated[User, Depends(require_admin)]


class SelfUpdate(StrictModel):
    """Self-service profile fields; role/email/password updates use dedicated flows."""

    full_name: str = Field(min_length=1, max_length=120)

    @field_validator("full_name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        """Reject whitespace-only names."""
        if not value.strip():
            raise ValueError("Full name is required")
        return value.strip()


class MeUpdate(StrictModel):
    """Self-service fields: display name and default report seat."""

    full_name: str | None = Field(default=None, min_length=1, max_length=120)
    default_seat_id: UUID | None = None

    @field_validator("full_name")
    @classmethod
    def valid_name(cls, value: str | None) -> str | None:
        """Normalize names when supplied."""
        return SelfUpdate.valid_name(value) if value is not None else None


class UserUpdate(StrictModel):
    """Administrators may update additive roles, active state and display name."""

    full_name: str | None = Field(default=None, min_length=1, max_length=120)
    roles: list[UserRole] | None = None
    is_active: bool | None = None

    @field_validator("full_name")
    @classmethod
    def valid_name(cls, value: str | None) -> str | None:
        """Normalize names when supplied."""
        return SelfUpdate.valid_name(value) if value is not None else None


def unique_skills(value: list[EngineerSkill] | None) -> list[EngineerSkill] | None:
    """Require exactly two different skills (FR-4.5)."""
    if value is None:
        return None
    skills = validate_skill_list(value)
    if len(skills) != REQUIRED_ENGINEER_SKILLS:
        raise ValueError(f"An engineer needs exactly {REQUIRED_ENGINEER_SKILLS} different skills")
    return skills


class EngineerCreate(SelfUpdate):
    """Add Engineer to an existing identity, or create a temporary-password identity."""

    email: str
    phone: str | None = Field(default=None, max_length=40)
    skills: list[EngineerSkill] = Field(max_length=MAX_ENGINEER_SKILLS)
    team_id: UUID | None = None
    home_building_ids: list[UUID] = Field(default_factory=list, max_length=20)
    temporary_password: str | None = None
    _email = field_validator("email")(normalize_email)
    _skills = field_validator("skills")(unique_skills)

    @field_validator("temporary_password")
    @classmethod
    def password_policy(cls, value: str | None) -> str | None:
        """Apply the same password policy to administrator-supplied credentials."""
        return validate_password(value) if value is not None else None


class EngineerUpdate(StrictModel):
    """Editable engineer profile and display name, retaining historical identity."""

    full_name: str | None = Field(default=None, min_length=1, max_length=120)
    phone: str | None = Field(default=None, max_length=40)
    is_active: bool | None = None
    availability: EngineerAvailability | None = None
    skills: list[EngineerSkill] | None = Field(default=None, max_length=MAX_ENGINEER_SKILLS)
    home_building_ids: list[UUID] | None = Field(default=None, max_length=20)
    _skills = field_validator("skills")(unique_skills)

    @field_validator("full_name")
    @classmethod
    def valid_name(cls, value: str | None) -> str | None:
        """Reject an empty display name."""
        return SelfUpdate.valid_name(value) if value is not None else None


class AvailabilityUpdate(StrictModel):
    """A currently active engineer may set their own availability."""

    availability: EngineerAvailability


async def people_guard(db: AsyncSession) -> None:
    """Serialize identity/profile changes, protecting the last-admin invariant."""
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext('facility_people_admin_guard'))"))


async def locked_user(db: AsyncSession, user_id: UUID) -> User:
    """Lock identity before workload checks, matching incident assignment locking."""
    user = await db.scalar(
        select(User)
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if user is None:
        raise HTTPException(404, "User not found")
    return user


async def workload(db: AsyncSession, user_id: UUID) -> dict[IncidentStatus, int]:
    """Count non-final assignments without granting access to incident contents."""
    rows = (
        await db.execute(
            select(Incident.status, func.count())
            .where(Incident.assigned_to == user_id, Incident.status.in_(NON_FINAL_STATUSES))
            .group_by(Incident.status)
        )
    ).all()
    counts = dict.fromkeys(NON_FINAL_STATUSES, 0)
    counts.update({status: count for status, count in rows})
    return counts


async def ensure_unassigned(db: AsyncSession, user_id: UUID) -> None:
    """Prevent disabling staff while any non-final work remains assigned."""
    count = sum((await workload(db, user_id)).values())
    if count:
        raise HTTPException(
            409,
            {
                "code": "has_assigned_incidents",
                "message": "Reassign non-final incidents first",
                "affected_count": count,
            },
        )


async def engineer_output(db: AsyncSession, profile: EngineerProfile, user: User) -> dict:
    """Project an engineer's account, availability and current non-final counts."""
    counts = await workload(db, user.id)
    return {
        "id": str(profile.id),
        "user_id": str(profile.user_id),
        "phone": profile.phone,
        "is_active": profile.is_active,
        "availability": profile.availability,
        "skills": profile.skills,
        "team_id": str(profile.team_id) if profile.team_id else None,
        "home_building_ids": [str(item) for item in profile.home_building_ids or []],
        "workload": sum(counts.values()),
        "status_counts": counts,
        "user": user_output(user),
    }


async def require_staff(user: User) -> None:
    """Keep directory/contact information limited to staff."""
    if not set(user.roles).intersection({UserRole.ADMIN, UserRole.ENGINEER}):
        raise HTTPException(403, "Staff access required")


@router.get("/users")
async def list_users(
    db: Db,
    _: Admin,
    q: str | None = Query(default=None, max_length=120),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
) -> dict:
    """Search administrator-visible account records with stable pagination."""
    query = select(User)
    if q:
        term = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.where(
            or_(
                User.email.ilike(f"%{term}%", escape="\\"),
                User.full_name.ilike(f"%{term}%", escape="\\"),
            )
        )
    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    users = (
        await db.scalars(
            query.order_by(User.full_name, User.id).offset((page - 1) * page_size).limit(page_size)
        )
    ).all()
    return {
        "items": [user_output(user) for user in users],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


async def check_buildings(db: AsyncSession, building_ids: list[UUID]) -> list[UUID]:
    """Home buildings must exist; duplicates are dropped."""
    unique = list(dict.fromkeys(building_ids))
    if unique:
        found = set(await db.scalars(select(Building.id).where(Building.id.in_(unique))))
        if found != set(unique):
            raise HTTPException(422, "Every home building must exist")
    return unique


async def check_default_seat(db: AsyncSession, seat_id: UUID) -> None:
    """A default seat must be active with active ancestors."""
    row = await db.execute(
        select(Seat.is_active, Floor.is_active, Building.is_active)
        .join(Floor, Floor.id == Seat.floor_id)
        .join(Building, Building.id == Floor.building_id)
        .where(Seat.id == seat_id)
    )
    flags = row.first()
    if flags is None or not all(flags):
        raise fail(422, "location_inactive", "Choose an active seat")


@router.patch("/users/me")
async def update_self(payload: MeUpdate, db: Db, actor: Actor) -> dict:
    """Update the caller's display name and default seat."""
    changes = payload.model_dump(exclude_unset=True)
    if not changes or changes.get("full_name", "x") is None:
        raise HTTPException(422, "Provide full_name or default_seat_id")
    actor = await locked_user(db, actor.id)
    if "default_seat_id" in changes:
        if payload.default_seat_id is not None:
            await check_default_seat(db, payload.default_seat_id)
        actor.default_seat_id = payload.default_seat_id
    if payload.full_name is not None:
        actor.full_name = payload.full_name
    await db.commit()
    return user_output(actor)


@router.patch("/users/{user_id}")
async def update_user(user_id: UUID, payload: UserUpdate, db: Db, _: Admin) -> dict:
    """Protect last-admin and workload constraints in the same PostgreSQL transaction."""
    await people_guard(db)
    user = await locked_user(db, user_id)
    changes = payload.model_dump(exclude_unset=True)
    if not changes or any(value is None for value in changes.values()):
        raise HTTPException(422, "Provide non-null fields to update")
    roles = sorted(set(payload.roles)) if payload.roles is not None else user.roles
    active = payload.is_active if payload.is_active is not None else user.is_active
    if (
        user.is_active
        and UserRole.ADMIN in user.roles
        and (not active or UserRole.ADMIN not in roles)
    ):
        remaining = await db.scalar(
            select(func.count(User.id)).where(
                User.id != user.id,
                User.is_active.is_(True),
                User.roles.contains([UserRole.ADMIN]),
            )
        )
        if not remaining:
            raise fail(409, "last_admin", "The last active administrator cannot be removed")
    removing_engineer = UserRole.ENGINEER in user.roles and UserRole.ENGINEER not in roles
    if (not active or UserRole.LEAD not in roles) and await db.scalar(
        select(Team.id).where(Team.lead_user_id == user.id, Team.is_active.is_(True))
    ):
        raise fail(409, "team_needs_lead", "Give the team a new lead first")
    if not active or removing_engineer:
        await ensure_unassigned(db, user.id)
    profile = await db.scalar(select(EngineerProfile).where(EngineerProfile.user_id == user.id))
    if (
        UserRole.ENGINEER in roles
        and UserRole.ENGINEER not in user.roles
        and (profile is None or not profile.is_active)
    ):
        raise fail(
            409, "engineer_profile_required", "Create or reactivate an engineer profile first"
        )
    if removing_engineer and profile is not None:
        profile.is_active = False
        profile.team_id = None
    if user.is_active and not active:
        user.token_version += 1
        await revoke_sessions(db, user.id)
    user.is_active = active
    user.roles = roles
    if payload.full_name is not None:
        user.full_name = payload.full_name
    await db.commit()
    return user_output(user)


@router.get("/engineers")
async def list_engineers(
    db: Db,
    actor: Actor,
    q: str | None = Query(default=None, max_length=120),
    is_active: bool | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
) -> dict:
    """Show staff availability and workload without incident-body projections."""
    await require_staff(actor)
    query = select(EngineerProfile, User).join(User, User.id == EngineerProfile.user_id)
    if q:
        term = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.where(
            or_(
                User.email.ilike(f"%{term}%", escape="\\"),
                User.full_name.ilike(f"%{term}%", escape="\\"),
            )
        )
    if is_active is not None:
        query = query.where(EngineerProfile.is_active == is_active)
    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    rows = (
        await db.execute(
            query.order_by(User.full_name, EngineerProfile.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return {
        "items": [await engineer_output(db, profile, user) for profile, user in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post("/engineers", status_code=201)
async def create_engineer(payload: EngineerCreate, db: Db, _: Admin) -> dict:
    """Return a new account's temporary password exactly once to the creating admin."""
    await people_guard(db)
    user = await db.scalar(select(User).where(User.email == payload.email).with_for_update())
    temporary = None
    if user is None:
        temporary = payload.temporary_password or secrets.token_urlsafe(16) + "A7"
        user = User(
            email=payload.email,
            full_name=payload.full_name,
            password_hash=hash_password(temporary),
            roles=[UserRole.ENGINEER],
            must_change_password=True,
        )
        db.add(user)
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            raise HTTPException(409, "An account was created concurrently; retry") from None
    else:
        if not user.is_active:
            raise HTTPException(409, "Reactivate the user account first")
        if payload.temporary_password is not None:
            raise HTTPException(422, "An existing account's password cannot be reset here")
        user.full_name = payload.full_name
    profile = await db.scalar(select(EngineerProfile).where(EngineerProfile.user_id == user.id))
    if profile is not None:
        raise HTTPException(409, "Engineer profile already exists; edit or reactivate it")
    user.roles = sorted(set(user.roles) | {UserRole.ENGINEER})
    if payload.team_id is not None:
        team = await db.get(Team, payload.team_id)
        if team is None or not team.is_active:
            raise HTTPException(422, "Team must be an active team")
    profile = EngineerProfile(
        user_id=user.id,
        phone=payload.phone,
        skills=payload.skills,
        team_id=payload.team_id,
        home_building_ids=await check_buildings(db, payload.home_building_ids),
    )
    db.add(profile)
    await db.commit()
    output = await engineer_output(db, profile, user)
    if temporary is not None:
        output["temporary_password"] = temporary
    return output


@router.patch("/engineers/me/availability")
async def set_own_availability(payload: AvailabilityUpdate, db: Db, actor: Actor) -> dict:
    """Set availability only for an active engineer identity and profile."""
    actor = await locked_user(db, actor.id)
    profile = await db.scalar(select(EngineerProfile).where(EngineerProfile.user_id == actor.id))
    if UserRole.ENGINEER not in actor.roles or profile is None or not profile.is_active:
        raise HTTPException(403, "An active engineer profile is required")
    profile.availability = payload.availability
    await db.commit()
    return await engineer_output(db, profile, actor)


@router.get("/engineers/{engineer_id}")
async def get_engineer(engineer_id: UUID, db: Db, actor: Actor) -> dict:
    """Read a staff-visible engineer summary."""
    await require_staff(actor)
    profile = await db.get(EngineerProfile, engineer_id)
    if profile is None:
        raise HTTPException(404, "Engineer profile not found")
    user = await db.get(User, profile.user_id)
    return await engineer_output(db, profile, user)


@router.patch("/engineers/{engineer_id}")
async def update_engineer(engineer_id: UUID, payload: EngineerUpdate, db: Db, _: Admin) -> dict:
    """Deactivate independently from the account, requiring zero non-final workload."""
    await people_guard(db)
    profile = await db.get(EngineerProfile, engineer_id)
    if profile is None:
        raise HTTPException(404, "Engineer profile not found")
    user = await locked_user(db, profile.user_id)
    await db.refresh(profile)
    changes = payload.model_dump(exclude_unset=True)
    if not changes or any(value is None for key, value in changes.items() if key != "phone"):
        raise HTTPException(422, "Provide valid fields to update")
    if payload.is_active is False:
        await ensure_unassigned(db, user.id)
    if payload.is_active is True:
        if not user.is_active:
            raise HTTPException(409, "Reactivate the user account first")
        user.roles = sorted(set(user.roles) | {UserRole.ENGINEER})
    if payload.full_name is not None:
        user.full_name = payload.full_name
    if payload.is_active is False:
        profile.team_id = None
    if payload.home_building_ids is not None:
        profile.home_building_ids = await check_buildings(db, payload.home_building_ids)
    for key in ("phone", "is_active", "availability", "skills"):
        if key in changes:
            setattr(profile, key, changes[key])
    await db.commit()
    return await engineer_output(db, profile, user)
