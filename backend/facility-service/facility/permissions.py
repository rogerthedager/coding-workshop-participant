"""Incident authorization (design section 5) and optimistic-concurrency primitives.

One module owns both the per-row relationship check and the SQL predicate used by
lists, counts, dashboards and notification recipients, so they cannot drift apart.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import exists, false, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from .enums import FINAL_STATUSES, IncidentEventType, UserRole
from .errors import fail
from .models import (
    EngineerProfile,
    Incident,
    IncidentEvent,
    IncidentFollower,
    Team,
    User,
    utcnow,
)


@dataclass(frozen=True)
class Rel:
    """A caller's relationships to one incident; roles alone grant nothing."""

    creator: bool = False
    follower: bool = False
    assignee: bool = False
    team_lead: bool = False
    admin: bool = False

    @property
    def staff_read(self) -> bool:
        """Admins, the assignee and the assignee's team lead read internal content."""
        return self.admin or self.assignee or self.team_lead

    @property
    def readable(self) -> bool:
        """Any relationship grants at least the follower projection."""
        return self.staff_read or self.creator or self.follower

    @property
    def follower_only(self) -> bool:
        """Followers see a reduced projection without description or creator."""
        return self.follower and not (self.staff_read or self.creator)


def is_admin(user: User) -> bool:
    """Return whether the current database user has the administrator role."""
    return UserRole.ADMIN in (user.roles or [])


def can_staff(user: User, incident: Incident) -> bool:
    """Staff *action* permission: admin, or the engineer assigned to this incident."""
    return is_admin(user) or (
        UserRole.ENGINEER in (user.roles or []) and incident.assigned_to == user.id
    )


def led_members(user_id: UUID):
    """Subquery of engineer user IDs in the active team this user leads."""
    return (
        select(EngineerProfile.user_id)
        .join(Team, Team.id == EngineerProfile.team_id)
        .where(Team.lead_user_id == user_id, Team.is_active.is_(True))
    )


def readable_clause(user: User) -> ColumnElement[bool]:
    """Return a SQL predicate that cannot expose another employee's incident."""
    if is_admin(user):
        return Incident.id.is_not(None)
    conditions = [
        Incident.created_by == user.id,
        exists(
            select(IncidentFollower.incident_id).where(
                IncidentFollower.incident_id == Incident.id,
                IncidentFollower.user_id == user.id,
            )
        ),
    ]
    if UserRole.ENGINEER in (user.roles or []):
        conditions.append(Incident.assigned_to == user.id)
    if UserRole.LEAD in (user.roles or []):
        conditions.append(Incident.assigned_to.in_(led_members(user.id)))
    return or_(*conditions)


def staff_read_clause(user: User) -> ColumnElement[bool]:
    """Incidents whose internal content this user may read."""
    if is_admin(user):
        return Incident.id.is_not(None)
    conditions = []
    if UserRole.ENGINEER in (user.roles or []):
        conditions.append(Incident.assigned_to == user.id)
    if UserRole.LEAD in (user.roles or []):
        conditions.append(Incident.assigned_to.in_(led_members(user.id)))
    return or_(*conditions) if conditions else false()


async def relationship(db: AsyncSession, user: User, incident: Incident) -> Rel:
    """Compute every relationship from current database state."""
    return (await relationships(db, user, [incident]))[incident.id]


async def relationships(
    db: AsyncSession, user: User, incidents: list[Incident]
) -> dict[UUID, Rel]:
    """Relationships for many incidents at once: at most two queries for any count."""
    if not incidents:
        return {}
    followed = set(
        await db.scalars(
            select(IncidentFollower.incident_id).where(
                IncidentFollower.incident_id.in_([item.id for item in incidents]),
                IncidentFollower.user_id == user.id,
            )
        )
    )
    led: set[UUID] = set()
    assignees = {item.assigned_to for item in incidents if item.assigned_to is not None}
    if assignees and UserRole.LEAD in (user.roles or []):
        led = set(
            await db.scalars(
                select(EngineerProfile.user_id).where(
                    EngineerProfile.user_id.in_(assignees),
                    EngineerProfile.user_id.in_(led_members(user.id)),
                )
            )
        )
    engineer = UserRole.ENGINEER in (user.roles or [])
    return {
        item.id: Rel(
            creator=item.created_by == user.id,
            follower=item.id in followed,
            assignee=engineer and item.assigned_to == user.id,
            team_lead=item.assigned_to in led,
            admin=is_admin(user),
        )
        for item in incidents
    }


async def load_incident(
    db: AsyncSession, user: User, incident_id: UUID, lock: bool = False
) -> Incident:
    """Load an authorized incident; mutation paths hold its row until commit."""
    statement = select(Incident).where(Incident.id == incident_id, readable_clause(user))
    if lock:
        # Re-read ORM state after acquiring the lock; stale identity-map state
        # must never bypass a concurrent version/state change.
        statement = (
            statement.with_for_update(of=Incident)
            .execution_options(populate_existing=True)
        )
    incident = await db.scalar(statement)
    if incident is None:
        raise fail(404, "not_found", "Incident not found")
    return incident


def check_match(record: Any, value: str | None) -> None:
    """Check the timestamp version returned by detail/note projections."""
    if value is None:
        raise fail(428, "precondition_required", "If-Match is required")
    candidate = value.strip()
    candidate = candidate.removeprefix("W/")
    candidate = candidate.strip('"')
    try:
        supplied = datetime.fromisoformat(candidate)
        current = record.updated_at
        if supplied.tzinfo is None:
            raise ValueError("Missing timezone")
        if current.tzinfo is None:
            current = current.replace(tzinfo=UTC)
        matches = supplied.astimezone(UTC) == current.astimezone(UTC)
    except (ValueError, TypeError, AttributeError):
        matches = False
    if not matches:
        raise fail(409, "stale_version", "Record changed; reload and retry")


def touch(record: Any) -> None:
    """Advance a record version even for operations within one clock tick."""
    now = utcnow()
    previous = record.updated_at
    if previous is not None:
        if previous.tzinfo is None:
            previous = previous.replace(tzinfo=UTC)
        now = max(now, previous + timedelta(microseconds=1))
    record.updated_at = now


def event(
    db: AsyncSession,
    incident: Incident,
    user: User | None,
    event_type: IncidentEventType,
    payload: dict[str, Any] | None = None,
) -> IncidentEvent:
    """Append a transactional audit event, never log its sensitive payload.

    `user` None records the system (scheduled job or automatic routing). The event
    is queued on the session so `notifications.dispatch` can fan it out before commit.
    """
    entry = IncidentEvent(
        id=uuid4(),
        incident_id=incident.id,
        actor_id=user.id if user is not None else None,
        type=event_type,
        payload=payload or {},
    )
    db.add(entry)
    db.info.setdefault("pending_events", []).append((entry, incident))
    return entry


def require_nonfinal(incident: Incident) -> None:
    """Reject otherwise permitted changes to final incidents."""
    if incident.status in FINAL_STATUSES:
        raise fail(409, "final_state", "Final incidents are read-only")


def require_permission(allowed: bool) -> None:
    """Apply action authorization after parent visibility has been checked."""
    if not allowed:
        raise fail(403, "forbidden", "Action is not permitted")
