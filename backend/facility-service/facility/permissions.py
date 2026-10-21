"""Incident-specific authorization and optimistic-concurrency primitives."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from .enums import FINAL_STATUSES, IncidentEventType, UserRole
from .models import Incident, IncidentEvent, User, utcnow


def is_admin(user: User) -> bool:
    """Return whether the current database user has the administrator role."""
    return UserRole.ADMIN in (user.roles or [])


def can_staff(user: User, incident: Incident) -> bool:
    """Staff permission is scoped to this incident, including additive roles."""
    return is_admin(user) or (
        UserRole.ENGINEER in (user.roles or []) and incident.assigned_to == user.id
    )


def readable_clause(user: User) -> ColumnElement[bool]:
    """Return a SQL predicate that cannot expose another employee's incident."""
    if is_admin(user):
        return Incident.id.is_not(None)
    if UserRole.ENGINEER in (user.roles or []):
        return or_(Incident.created_by == user.id, Incident.assigned_to == user.id)
    return Incident.created_by == user.id


async def load_incident(
    db: AsyncSession, user: User, incident_id: UUID, lock: bool = False
) -> Incident:
    """Load an authorized incident; mutation paths hold its row until commit."""
    statement = select(Incident).where(Incident.id == incident_id, readable_clause(user))
    if lock:
        # Re-read ORM state after acquiring the lock; stale identity-map state
        # must never bypass a concurrent version/state change.
        statement = statement.with_for_update().execution_options(populate_existing=True)
    incident = await db.scalar(statement)
    if incident is None:
        raise HTTPException(404, "Incident not found")
    return incident


def check_match(record: Any, value: str | None) -> None:
    """Check the timestamp version returned by detail/note projections."""
    if value is None:
        raise HTTPException(428, "If-Match is required")
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
        raise HTTPException(409, "Record changed; reload and retry")


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
    user: User,
    event_type: IncidentEventType,
    payload: dict[str, Any] | None = None,
) -> IncidentEvent:
    """Append a transactional audit event, never log its sensitive payload."""
    entry = IncidentEvent(
        incident_id=incident.id,
        actor_id=user.id,
        type=event_type,
        payload=payload or {},
    )
    db.add(entry)
    return entry


def require_nonfinal(incident: Incident) -> None:
    """Reject otherwise permitted changes to final incidents."""
    if incident.status in FINAL_STATUSES:
        raise HTTPException(409, "Final incidents are read-only")


def require_permission(allowed: bool) -> None:
    """Apply action authorization after parent visibility has been checked."""
    if not allowed:
        raise HTTPException(403, "Action is not permitted")
