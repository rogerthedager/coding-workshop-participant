"""Turn audit events into in-app notifications in the caller's transaction (FR-10).

Recipients are filtered to active people who can read the ticket at that moment,
minus the actor. Messages are fixed templates and never contain note text.
"""

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from facility.enums import (
    IncidentEventType,
    IncidentStatus,
    NoteVisibility,
    NotificationType,
    UserRole,
)
from facility.models import Incident, IncidentEvent, IncidentFollower, Notification, User
from facility.permissions import readable_clause

STATUS_TYPES = {IncidentEventType.STATUS_CHANGED}
DECISION_TYPES = {IncidentEventType.ESCALATION_APPROVED, IncidentEventType.ESCALATION_REJECTED}


async def active_admin_ids(db: AsyncSession) -> list[UUID]:
    """Every active administrator."""
    return list(
        await db.scalars(
            select(User.id).where(
                User.is_active.is_(True), User.roles.contains([UserRole.ADMIN])
            )
        )
    )


async def follower_ids(db: AsyncSession, incident_id: UUID) -> list[UUID]:
    """Current followers of a ticket."""
    return list(
        await db.scalars(
            select(IncidentFollower.user_id).where(IncidentFollower.incident_id == incident_id)
        )
    )


async def notify(
    db: AsyncSession,
    incident: Incident,
    recipients: Iterable[UUID | None],
    kind: NotificationType,
    message: str,
    *,
    actor_id: UUID | None = None,
    event_id: UUID | None = None,
    require_read: bool = True,
    exempt: Iterable[UUID | None] = (),
) -> int:
    """Insert one notification per eligible recipient; return how many were written."""
    exempt_ids = {item for item in exempt if item is not None}
    wanted = {item for item in recipients if item is not None and item != actor_id}
    if not wanted:
        return 0
    users = (
        await db.scalars(select(User).where(User.id.in_(wanted), User.is_active.is_(True)))
    ).all()
    written = 0
    for user in users:
        if require_read and user.id not in exempt_ids:
            can_read = await db.scalar(
                select(Incident.id).where(Incident.id == incident.id, readable_clause(user))
            )
            if can_read is None:
                continue
        db.add(
            Notification(
                user_id=user.id,
                incident_id=incident.id,
                event_id=event_id,
                type=kind,
                message=message,
            )
        )
        written += 1
    return written


def _label(incident: Incident) -> str:
    return f"{incident.number}: {incident.title}"


async def _status_message(db: AsyncSession, incident: Incident, payload: dict) -> str:
    target = payload.get("to")
    if payload.get("close_kind") == "duplicate":
        return f"{incident.number} was closed as a duplicate of {payload.get('duplicate_of_number')}"
    if target == IncidentStatus.BLOCKED and payload.get("reason"):
        return f"{incident.number} is now Blocked: {str(payload['reason']).lower()}"
    if payload.get("close_kind") == "auto":
        return f"{incident.number} was closed automatically 72 hours after resolution"
    return f"{incident.number} is now {target}: {incident.title}"


async def _fan_out(db: AsyncSession, entry: IncidentEvent, incident: Incident, batch: set) -> None:
    payload = entry.payload or {}
    actor = entry.actor_id
    followers = await follower_ids(db, incident.id)
    audience = [incident.created_by, incident.assigned_to, *followers]
    common = {"actor_id": actor, "event_id": entry.id}
    if entry.type in STATUS_TYPES:
        message = await _status_message(db, incident, payload)
        await notify(db, incident, audience, NotificationType.STATUS_CHANGED, message, **common)
    elif entry.type in {IncidentEventType.ASSIGNED, IncidentEventType.UNASSIGNED}:
        previous = payload.get("previous_assignee")
        previous_id = UUID(previous) if previous else None
        if payload.get("via") == "returned_to_triage":
            admins = await active_admin_ids(db)
            recipients = [*admins, previous_id] if previous_id != actor else admins
            await notify(
                db,
                incident,
                recipients,
                NotificationType.RETURNED_TO_TRIAGE,
                f"{incident.number} was returned to triage: {incident.title}",
                exempt=[previous_id],
                **common,
            )
            return
        new = payload.get("assignee")
        new_id = UUID(new) if new else None
        if new_id is not None:
            await notify(
                db,
                incident,
                [new_id],
                NotificationType.ASSIGNED,
                f"{incident.number} was assigned to you: {incident.title}",
                **common,
            )
        if previous_id is not None and previous_id != new_id:
            # The previous assignee may no longer read the ticket; the template
            # repeats only the number they already knew.
            await notify(
                db,
                incident,
                [previous_id],
                NotificationType.UNASSIGNED,
                f"{incident.number} is no longer assigned to you",
                require_read=False,
                **common,
            )
        others = [incident.created_by, *followers]
        others = [item for item in others if item not in {new_id, previous_id}]
        message = (
            f"{incident.number} has a new assignee: {incident.title}"
            if new_id
            else f"{incident.number} is waiting for assignment: {incident.title}"
        )
        kind = NotificationType.ASSIGNED if new_id else NotificationType.UNASSIGNED
        await notify(db, incident, others, kind, message, **common)
    elif entry.type == IncidentEventType.NOTE_ADDED:
        # A note posted as part of a transition or decision is covered by that event.
        if (incident.id, "covered") in batch:
            return
        if payload.get("visibility") == NoteVisibility.INTERNAL:
            await notify(
                db,
                incident,
                [incident.assigned_to],
                NotificationType.NOTE_ADDED,
                f"New internal note on {_label(incident)}",
                **common,
            )
        else:
            await notify(
                db,
                incident,
                audience,
                NotificationType.NOTE_ADDED,
                f"New update on {_label(incident)}",
                **common,
            )
    elif entry.type in DECISION_TYPES:
        verdict = "approved" if entry.type == IncidentEventType.ESCALATION_APPROVED else "rejected"
        await notify(
            db,
            incident,
            audience,
            NotificationType.ESCALATION_DECIDED,
            f"Escalation on {incident.number} was {verdict}",
            **common,
        )


async def dispatch(db: AsyncSession) -> None:
    """Fan out every event queued on this session since the last dispatch."""
    pending: list[tuple[IncidentEvent, Incident]] = db.info.pop("pending_events", [])
    if not pending:
        return
    await db.flush()
    batch: set = set()
    for entry, incident in pending:
        if entry.type in STATUS_TYPES | DECISION_TYPES:
            batch.add((incident.id, "covered"))
    for entry, incident in pending:
        await _fan_out(db, entry, incident, batch)


async def commit(db: AsyncSession) -> None:
    """Dispatch queued notifications, then commit everything together."""
    await dispatch(db)
    await db.commit()
