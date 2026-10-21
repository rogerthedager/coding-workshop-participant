"""Workflow side effects shared by API requests and scheduled jobs."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from facility.enums import CloseKind, EscalationState, IncidentEventType, IncidentStatus
from facility.models import EscalationRequest, Incident, User, utcnow
from facility.permissions import event, touch


async def supersede_pending(db: AsyncSession, incident: Incident, user: User | None) -> None:
    """A ticket entering a final state closes any pending escalation request (N10)."""
    pending = await db.scalar(
        select(EscalationRequest)
        .where(
            EscalationRequest.incident_id == incident.id,
            EscalationRequest.state == EscalationState.PENDING,
        )
        .with_for_update()
    )
    if pending is not None:
        pending.state = EscalationState.SUPERSEDED
        pending.decided_at = utcnow()
        pending.decided_by = user.id if user is not None else None
        pending.decision_reason = f"Incident became {incident.status}"
        event(
            db,
            incident,
            user,
            IncidentEventType.ESCALATION_SUPERSEDED,
            {"escalation_id": str(pending.id), "reason": pending.decision_reason},
        )


async def close(
    db: AsyncSession,
    incident: Incident,
    user: User | None,
    kind: CloseKind,
    extra: dict | None = None,
) -> None:
    """Move a locked ticket to Closed with its close kind, event and side effects."""
    previous = incident.status
    incident.status = IncidentStatus.CLOSED
    incident.closed_at = utcnow()
    incident.close_kind = kind
    incident.blocked_reason = None
    incident.blocked_since = None
    touch(incident)
    event(
        db,
        incident,
        user,
        IncidentEventType.STATUS_CHANGED,
        {"from": previous, "to": IncidentStatus.CLOSED, "close_kind": kind, **(extra or {})},
    )
    await supersede_pending(db, incident, user)
