"""Teams with leads (FR-14.1) and category groups that route new tickets (FR-3.5)."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from facility.database import get_db
from facility.dependencies import current_user, require_admin
from facility.enums import EngineerAvailability, UserRole
from facility.errors import fail
from facility.models import CategoryGroup, EngineerProfile, Team, User
from facility.permissions import is_admin
from facility.routers.people import locked_user, people_guard

router = APIRouter(tags=["Teams and routing"])
Db = Annotated[AsyncSession, Depends(get_db)]
Actor = Annotated[User, Depends(current_user)]
Admin = Annotated[User, Depends(require_admin)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TeamCreate(Input):
    name: str = Field(min_length=1, max_length=120)
    lead_user_id: UUID


class TeamUpdate(Input):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    lead_user_id: UUID | None = None
    is_active: bool | None = None


class TeamMembers(Input):
    engineer_user_ids: list[UUID] = Field(max_length=200)


class GroupCreate(Input):
    name: str = Field(min_length=1, max_length=120)
    team_id: UUID | None = None
    sort_order: int = Field(default=0, ge=0, le=10000)


class GroupUpdate(Input):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    team_id: UUID | None = None
    sort_order: int | None = Field(default=None, ge=0, le=10000)
    is_active: bool | None = None


def group_output(group: CategoryGroup) -> dict[str, Any]:
    return {
        "id": str(group.id),
        "name": group.name,
        "team_id": str(group.team_id) if group.team_id else None,
        "sort_order": group.sort_order,
        "is_active": group.is_active,
    }


async def team_output(db: AsyncSession, team: Team) -> dict[str, Any]:
    """Team with its lead, members and the category groups it owns."""
    lead = await db.get(User, team.lead_user_id)
    members = (
        await db.execute(
            select(User.id, User.full_name, EngineerProfile.availability, EngineerProfile.skills)
            .join(EngineerProfile, EngineerProfile.user_id == User.id)
            .where(
                EngineerProfile.team_id == team.id,
                EngineerProfile.is_active.is_(True),
                # Same pool as routing: deactivated accounts are not members on duty.
                User.is_active.is_(True),
                User.roles.contains([UserRole.ENGINEER]),
            )
            .order_by(User.full_name, User.id)
        )
    ).mappings().all()
    groups = (
        await db.scalars(
            select(CategoryGroup)
            .where(CategoryGroup.team_id == team.id)
            .order_by(CategoryGroup.sort_order, CategoryGroup.name)
        )
    ).all()
    on_duty = sum(1 for row in members if row.availability != EngineerAvailability.OFF_DUTY)
    warnings = []
    if any(group.is_active for group in groups) and on_duty == 0:
        warnings.append("no_member_on_duty")
    return {
        "id": str(team.id),
        "name": team.name,
        "is_active": team.is_active,
        "lead": {"id": str(lead.id), "full_name": lead.full_name} if lead else None,
        "members": [
            {
                "id": str(row.id),
                "full_name": row.full_name,
                "availability": row.availability,
                "skills": row.skills,
            }
            for row in members
        ],
        "category_groups": [group_output(group) for group in groups],
        "warnings": warnings,
    }


async def _lock_team(db: AsyncSession, team_id: UUID) -> Team:
    team = await db.scalar(
        select(Team)
        .where(Team.id == team_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if team is None:
        raise fail(404, "not_found", "Team not found")
    return team


async def _grant_lead(db: AsyncSession, user_id: UUID) -> User:
    user = await locked_user(db, user_id)
    if not user.is_active:
        raise HTTPException(422, "The team lead must be an active user")
    if await db.scalar(select(Team.id).where(Team.lead_user_id == user.id)):
        raise fail(409, "duplicate", "That user already leads a team")
    user.roles = sorted(set(user.roles) | {UserRole.LEAD})
    return user


@router.get("/teams")
async def list_teams(db: Db, user: Actor) -> dict[str, Any]:
    """Admins see all teams; a lead sees only their own."""
    statement = select(Team).order_by(Team.name, Team.id)
    if not is_admin(user):
        if UserRole.LEAD not in user.roles:
            raise fail(403, "forbidden", "Administrator or team lead access required")
        statement = statement.where(Team.lead_user_id == user.id)
    teams = (await db.scalars(statement)).all()
    items = [await team_output(db, team) for team in teams]
    return {"items": items, "total": len(items), "page": 1, "page_size": len(items)}


@router.get("/teams/{team_id}")
async def get_team(team_id: UUID, db: Db, user: Actor) -> dict[str, Any]:
    team = await db.get(Team, team_id)
    if team is None or not (is_admin(user) or team.lead_user_id == user.id):
        raise fail(404, "not_found", "Team not found")
    return await team_output(db, team)


@router.post("/teams", status_code=201)
async def create_team(payload: TeamCreate, db: Db, _: Admin) -> dict[str, Any]:
    """Create a team and give its lead the lead role in one transaction."""
    await people_guard(db)
    await _grant_lead(db, payload.lead_user_id)
    team = Team(name=payload.name, lead_user_id=payload.lead_user_id)
    db.add(team)
    await db.commit()
    return await team_output(db, team)


@router.patch("/teams/{team_id}")
async def update_team(team_id: UUID, payload: TeamUpdate, db: Db, _: Admin) -> dict[str, Any]:
    """Rename, change the lead (moving the role) or deactivate an empty team."""
    await people_guard(db)
    team = await _lock_team(db, team_id)
    changes = payload.model_dump(exclude_unset=True)
    if not changes or any(value is None for value in changes.values()):
        raise HTTPException(422, "Provide non-null fields to update")
    if payload.lead_user_id is not None and payload.lead_user_id != team.lead_user_id:
        old = await locked_user(db, team.lead_user_id)
        await _grant_lead(db, payload.lead_user_id)
        old.roles = [role for role in old.roles if role != UserRole.LEAD]
        team.lead_user_id = payload.lead_user_id
    if payload.is_active is False and team.is_active:
        members = await db.scalar(
            select(func.count())
            .select_from(EngineerProfile)
            .join(User, User.id == EngineerProfile.user_id)
            .where(
                EngineerProfile.team_id == team.id,
                EngineerProfile.is_active.is_(True),
                User.is_active.is_(True),
            )
        )
        if members:
            raise fail(
                409, "team_has_members", "Move the team's engineers first", count=members
            )
        # Detach inactive leftovers so they don't rejoin a closed team later.
        await db.execute(
            update(EngineerProfile)
            .where(EngineerProfile.team_id == team.id)
            .values(team_id=None)
        )
    if payload.is_active is True and not team.is_active:
        # The lead may have been deactivated or lost the role while the team was closed.
        lead = await locked_user(db, team.lead_user_id)
        if not lead.is_active:
            raise fail(409, "team_needs_lead", "Give the team an active lead first")
        lead.roles = sorted(set(lead.roles) | {UserRole.LEAD})
    if payload.is_active is not None:
        team.is_active = payload.is_active
    if payload.name is not None:
        team.name = payload.name
    await db.commit()
    return await team_output(db, team)


@router.put("/teams/{team_id}/members")
async def set_members(
    team_id: UUID, payload: TeamMembers, db: Db, _: Admin
) -> dict[str, Any]:
    """Replace the member list; engineers in another team must be moved deliberately."""
    await people_guard(db)
    team = await _lock_team(db, team_id)
    if not team.is_active:
        raise fail(409, "team_inactive", "Reactivate the team first")
    wanted = set(payload.engineer_user_ids)
    profiles = (
        await db.scalars(
            select(EngineerProfile)
            .where(
                (EngineerProfile.user_id.in_(wanted)) | (EngineerProfile.team_id == team.id)
            )
            .with_for_update()
        )
    ).all()
    found = {profile.user_id for profile in profiles if profile.user_id in wanted}
    if found != wanted:
        raise HTTPException(422, "Every member must have an engineer profile")
    for profile in profiles:
        if profile.user_id in wanted:
            if not profile.is_active:
                raise HTTPException(422, "Every member must be an active engineer")
            if profile.team_id not in (None, team.id):
                raise fail(
                    409,
                    "engineer_in_other_team",
                    "An engineer already belongs to another team",
                    engineer_user_id=str(profile.user_id),
                )
            profile.team_id = team.id
        else:
            profile.team_id = None
    await db.commit()
    return await team_output(db, team)


@router.get("/category-groups")
async def list_groups(db: Db, _: Actor) -> dict[str, Any]:
    groups = (
        await db.scalars(
            select(CategoryGroup).order_by(CategoryGroup.sort_order, CategoryGroup.name)
        )
    ).all()
    items = [group_output(group) for group in groups]
    return {"items": items, "total": len(items), "page": 1, "page_size": len(items)}


async def _check_team(db: AsyncSession, team_id: UUID | None) -> None:
    if team_id is not None:
        team = await db.get(Team, team_id)
        if team is None or not team.is_active:
            raise HTTPException(422, "Owning team must be an active team")


@router.post("/category-groups", status_code=201)
async def create_group(payload: GroupCreate, db: Db, _: Admin) -> dict[str, Any]:
    await _check_team(db, payload.team_id)
    group = CategoryGroup(**payload.model_dump())
    db.add(group)
    await db.commit()
    return group_output(group)


@router.patch("/category-groups/{group_id}")
async def update_group(
    group_id: UUID, payload: GroupUpdate, db: Db, _: Admin
) -> dict[str, Any]:
    """Change name, order, owning team (null sends tickets to triage) or active state."""
    group = await db.scalar(
        select(CategoryGroup).where(CategoryGroup.id == group_id).with_for_update()
    )
    if group is None:
        raise fail(404, "not_found", "Category group not found")
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(422, "Provide fields to update")
    for key, value in changes.items():
        if value is None and key != "team_id":
            raise HTTPException(422, f"{key} cannot be null")
    if "team_id" in changes:
        await _check_team(db, changes["team_id"])
    for key, value in changes.items():
        setattr(group, key, value)
    await db.commit()
    return group_output(group)
