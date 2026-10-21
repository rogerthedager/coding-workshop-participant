"""Team routing at creation (FR-7.6) and ranked engineer suggestions (FR-7.2).

Ranking keys, in order: (1) skill matches the ticket's top-level category,
(2) availability Available > Busy > Off-duty, (3) fewest non-final assigned
tickets, (4) home building matches, then name and ID as a stable tie-break.
"""

from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, String, any_, case, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from facility.enums import (
    NON_FINAL_STATUSES,
    EngineerAvailability,
    IncidentEventType,
    TriageReason,
    UserRole,
)
from facility.models import Category, CategoryGroup, EngineerProfile, Incident, Team, User, utcnow
from facility.permissions import event

AVAILABILITY_RANK = case(
    (EngineerProfile.availability == EngineerAvailability.AVAILABLE, 0),
    (EngineerProfile.availability == EngineerAvailability.BUSY, 1),
    else_=2,
)


async def top_category(db: AsyncSession, category_id: UUID) -> Category | None:
    """Return the top-level category of a (sub)category."""
    category = await db.get(Category, category_id)
    if category is not None and category.parent_id is not None:
        category = await db.get(Category, category.parent_id)
    return category


def open_count_sql():
    """Correlated count of an engineer's non-final assigned tickets."""
    return (
        select(func.count(Incident.id))
        .where(Incident.assigned_to == User.id, Incident.status.in_(NON_FINAL_STATUSES))
        .correlate(User)
        .scalar_subquery()
    )


async def ranked_engineers(
    db: AsyncSession,
    top: Category | None,
    building_id: UUID | None,
    *,
    team_id: UUID | None = None,
    on_duty_only: bool = False,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Rank active engineers for a ticket; optionally only one team's on-duty members."""
    skill_name = top.name if top is not None else ""
    skill_match = EngineerProfile.skills.op("@>", return_type=Boolean)(
        func.jsonb_build_array(literal(skill_name, String))
    )
    home = (
        literal(building_id) == any_(EngineerProfile.home_building_ids)
        if building_id is not None
        else literal(False)
    )
    open_count = open_count_sql()
    statement = (
        select(
            User.id,
            User.full_name,
            EngineerProfile.id.label("profile_id"),
            EngineerProfile.availability,
            EngineerProfile.skills,
            EngineerProfile.team_id,
            open_count.label("open_count"),
            skill_match.label("skill_match"),
            home.label("home_building"),
        )
        .join(EngineerProfile, EngineerProfile.user_id == User.id)
        .where(
            EngineerProfile.is_active.is_(True),
            User.is_active.is_(True),
            User.roles.contains([UserRole.ENGINEER]),
        )
        .order_by(
            skill_match.desc(),
            AVAILABILITY_RANK,
            open_count,
            home.desc(),
            User.full_name,
            User.id,
        )
    )
    if team_id is not None:
        statement = statement.where(EngineerProfile.team_id == team_id)
    if on_duty_only:
        statement = statement.where(EngineerProfile.availability != EngineerAvailability.OFF_DUTY)
    if limit is not None:
        statement = statement.limit(limit)
    rows = (await db.execute(statement)).mappings().all()
    result = []
    for row in rows:
        reasons = []
        if row.skill_match:
            reasons.append("skill")
        if row.availability == EngineerAvailability.AVAILABLE:
            reasons.append("available")
        if row.home_building:
            reasons.append("home_building")
        result.append(
            {
                "id": str(row.id),
                "profile_id": str(row.profile_id),
                "full_name": row.full_name,
                "availability": row.availability,
                "skills": row.skills,
                "team_id": str(row.team_id) if row.team_id else None,
                "open_count": row.open_count,
                "skill_match": bool(row.skill_match),
                "home_building": bool(row.home_building),
                "match_reasons": reasons,
            }
        )
    return result


async def still_eligible(db: AsyncSession, user_id: UUID, team_id: UUID) -> User | None:
    """Lock a ranked engineer and re-check them; ranking itself takes no locks.

    An admin may be deactivating the engineer, or the engineer going Off-duty,
    in a transaction that commits while we wait for the lock.
    """
    user = await db.scalar(
        select(User)
        .where(User.id == user_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    profile = await db.scalar(
        select(EngineerProfile)
        .where(EngineerProfile.user_id == user_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    if (
        user is None
        or profile is None
        or not user.is_active
        or UserRole.ENGINEER not in user.roles
        or not profile.is_active
        or profile.team_id != team_id
        or profile.availability == EngineerAvailability.OFF_DUTY
    ):
        return None
    return user


async def route_new_incident(db: AsyncSession, incident: Incident) -> dict[str, Any]:
    """Assign a new ticket to its owning team's best on-duty engineer, else triage."""
    top = await top_category(db, incident.category_id)
    group = await db.get(CategoryGroup, top.group_id) if top and top.group_id else None
    team = await db.get(Team, group.team_id) if group and group.is_active and group.team_id else None
    if team is None or not team.is_active:
        incident.triage_reason = TriageReason.UNROUTED_GROUP
        return {"status": "triage", "reason": TriageReason.UNROUTED_GROUP}
    incident.routed_team_id = team.id
    candidates = await ranked_engineers(
        db, top, incident.building_id, team_id=team.id, on_duty_only=True, limit=5
    )
    assignee = None
    for candidate in candidates:
        assignee = await still_eligible(db, UUID(candidate["id"]), team.id)
        if assignee is not None:
            break
    if assignee is None:
        incident.triage_reason = TriageReason.NO_ENGINEER
        return {
            "status": "triage",
            "reason": TriageReason.NO_ENGINEER,
            "team": {"id": str(team.id), "name": team.name},
        }
    incident.assigned_to = assignee.id
    incident.assigned_at = incident.assigned_at or utcnow()
    incident.triage_reason = None
    event(
        db,
        incident,
        None,
        IncidentEventType.ASSIGNED,
        {
            "previous_assignee": None,
            "assignee": str(assignee.id),
            "via": "auto_route",
            "team_id": str(team.id),
        },
    )
    return {
        "status": "assigned",
        "team": {"id": str(team.id), "name": team.name},
        "assignee": {"id": str(assignee.id), "full_name": assignee.full_name},
    }
