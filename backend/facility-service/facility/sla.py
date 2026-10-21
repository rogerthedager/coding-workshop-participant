"""SLA state (design section 7). Computed from due times, never stored.

For each active window: breached if now > due; at risk once 75% of the window has
elapsed; otherwise on track. The ack window is active until acknowledgment, the
resolve window until resolution or a final status. Resolved and closed tickets
show met or missed. The Python and SQL versions must agree.
"""

from datetime import UTC, datetime

from sqlalchemy import and_, case, func, literal, or_
from sqlalchemy.sql.elements import ColumnElement

from facility.enums import SLA_AT_RISK_FRACTION, IncidentStatus, SlaState
from facility.models import Incident, utcnow

ACTIVE_STATUSES = (IncidentStatus.OPEN, IncidentStatus.IN_PROGRESS, IncidentStatus.BLOCKED)


def _aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def sla_state(incident: Incident, now: datetime | None = None) -> SlaState | None:
    """Return the ticket's SLA state; None for cancelled or never-resolved closures."""
    now = now or utcnow()
    created = _aware(incident.created_at)
    ack_due = _aware(incident.ack_due_at)
    resolve_due = _aware(incident.resolve_due_at)
    resolved = _aware(incident.resolved_at)
    if incident.status in (IncidentStatus.RESOLVED, IncidentStatus.CLOSED):
        if resolved is None:
            return None
        return SlaState.MET if resolved <= resolve_due else SlaState.MISSED
    if incident.status not in ACTIVE_STATUSES:
        return None
    windows = [resolve_due]
    if incident.acknowledged_at is None:
        windows.append(ack_due)
    if any(now > due for due in windows):
        return SlaState.BREACHED
    if any(now >= created + SLA_AT_RISK_FRACTION * (due - created) for due in windows):
        return SlaState.AT_RISK
    return SlaState.ON_TRACK


def reached_risk(now: ColumnElement, due: ColumnElement) -> ColumnElement:
    """True once 75% of the window from creation to `due` has elapsed."""
    elapsed = func.extract("epoch", now - Incident.created_at)
    window = func.extract("epoch", due - Incident.created_at)
    return elapsed >= window * SLA_AT_RISK_FRACTION


def sla_state_sql(now: ColumnElement | None = None) -> ColumnElement:
    """SQL expression matching `sla_state`, using PostgreSQL now() by default."""
    now = now if now is not None else func.now()
    ack_active = Incident.acknowledged_at.is_(None)
    breached = or_(now > Incident.resolve_due_at, and_(ack_active, now > Incident.ack_due_at))
    at_risk = or_(
        reached_risk(now, Incident.resolve_due_at),
        and_(ack_active, reached_risk(now, Incident.ack_due_at)),
    )
    return case(
        (
            Incident.status.in_([IncidentStatus.RESOLVED, IncidentStatus.CLOSED]),
            case(
                (Incident.resolved_at.is_(None), literal(None)),
                (Incident.resolved_at <= Incident.resolve_due_at, literal(SlaState.MET.value)),
                else_=literal(SlaState.MISSED.value),
            ),
        ),
        (Incident.status.not_in(ACTIVE_STATUSES), literal(None)),
        (breached, literal(SlaState.BREACHED.value)),
        (at_risk, literal(SlaState.AT_RISK.value)),
        else_=literal(SlaState.ON_TRACK.value),
    )


def next_due_sql() -> ColumnElement:
    """The next due time: acknowledgment while unacknowledged, else resolution."""
    return case(
        (Incident.acknowledged_at.is_(None), Incident.ack_due_at), else_=Incident.resolve_due_at
    )
