"""Incident ticket, its SLA due times and indexes."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, validates

from facility.database import Base
from facility.db_types import TextEnum, enum_check, varchar_enum
from facility.enums import (
    SLA_HOURS,
    BlockedReason,
    CloseKind,
    IncidentPriority,
    IncidentStatus,
    TriageReason,
)
from facility.models.base import Entity, utcnow


class Incident(Entity, Base):
    """Reported issue and its current workflow state."""

    __tablename__ = "incidents"
    __table_args__ = (
        UniqueConstraint("number", name="uq_incidents_number"),
        CheckConstraint(
            enum_check("status", IncidentStatus),
            name="ck_incidents_status",
        ),
        CheckConstraint(
            enum_check("priority", IncidentPriority),
            name="ck_incidents_priority",
        ),
        CheckConstraint(
            enum_check("requested_priority", IncidentPriority),
            name="ck_incidents_requested_priority",
        ),
        CheckConstraint("reopen_count >= 0", name="ck_incidents_reopen_count"),
        CheckConstraint(
            "seat_id IS NULL OR floor_id IS NOT NULL", name="ck_incidents_seat_requires_floor"
        ),
        CheckConstraint(
            enum_check("blocked_reason", BlockedReason), name="ck_incidents_blocked_reason"
        ),
        CheckConstraint(enum_check("triage_reason", TriageReason), name="ck_incidents_triage"),
        CheckConstraint(
            "triage_reason IS NULL OR assigned_to IS NULL", name="ck_incidents_triage_unassigned"
        ),
        CheckConstraint(enum_check("close_kind", CloseKind), name="ck_incidents_close_kind"),
        CheckConstraint(
            "(close_kind = 'duplicate') = (duplicate_of_id IS NOT NULL)",
            name="ck_incidents_duplicate_kind",
        ),
        CheckConstraint("duplicate_of_id <> id", name="ck_incidents_duplicate_self"),
        CheckConstraint("route_return_count >= 0", name="ck_incidents_route_returns"),
        Index(
            "ix_incidents_search",
            text("to_tsvector('english', title || ' ' || description)"),
            postgresql_using="gin",
        ),
    )
    number: Mapped[str] = mapped_column(String(24), nullable=False)
    title: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text)
    category_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("categories.id"), index=True)
    building_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("buildings.id"), index=True)
    floor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("floors.id"), index=True)
    seat_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("seats.id"), index=True)
    status: Mapped[IncidentStatus] = mapped_column(
        varchar_enum(IncidentStatus, 16), default=IncidentStatus.OPEN, index=True
    )
    requested_priority: Mapped[IncidentPriority] = mapped_column(
        varchar_enum(IncidentPriority, 16), default=IncidentPriority.MEDIUM
    )
    priority: Mapped[IncidentPriority] = mapped_column(
        varchar_enum(IncidentPriority, 16), default=IncidentPriority.MEDIUM, index=True
    )
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    blocked_reason: Mapped[BlockedReason | None] = mapped_column(TextEnum(BlockedReason))
    blocked_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_in_progress_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reopen_count: Mapped[int] = mapped_column(Integer, default=0)
    close_kind: Mapped[CloseKind | None] = mapped_column(TextEnum(CloseKind))
    duplicate_of_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("incidents.id"))
    routed_team_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("teams.id"), index=True)
    triage_reason: Mapped[TriageReason | None] = mapped_column(TextEnum(TriageReason))
    route_return_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ack_due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolve_due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ack_at_risk_alerted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ack_breach_alerted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolve_at_risk_alerted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolve_breach_alerted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def set_due_times(self) -> None:
        """Recompute both SLA due times from creation and the current priority."""
        ack_hours, resolve_hours = SLA_HOURS[IncidentPriority(self.priority)]
        created = self.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        ack_due = created + timedelta(hours=ack_hours)
        resolve_due = created + timedelta(hours=resolve_hours)
        # A revised SLA re-arms its alerts (FR-12.1: once per threshold and revision).
        if self.ack_due_at is not None and self.ack_due_at != ack_due:
            self.ack_at_risk_alerted_at = None
            self.ack_breach_alerted_at = None
        if self.resolve_due_at is not None and self.resolve_due_at != resolve_due:
            self.resolve_at_risk_alerted_at = None
            self.resolve_breach_alerted_at = None
        self.ack_due_at = ack_due
        self.resolve_due_at = resolve_due

    @validates("status")
    def validate_status(self, _key: str, value: IncidentStatus) -> IncidentStatus:
        """Expose a shared status member immediately on construction or update."""
        return IncidentStatus(value)

    @validates("priority", "requested_priority")
    def validate_priority(self, _key: str, value: IncidentPriority) -> IncidentPriority:
        """Apply one priority domain to both employee requests and effective urgency."""
        return IncidentPriority(value)

    @validates("blocked_reason")
    def validate_blocked_reason(
        self, _key: str, value: BlockedReason | None
    ) -> BlockedReason | None:
        """Use a fixed reason code, retaining absence for incidents that are not blocked."""
        return None if value is None else BlockedReason(value)


Index("ix_incidents_created_at", Incident.created_at)
Index("ix_incidents_updated_at", Incident.updated_at)
Index(
    "ix_incidents_triage",
    Incident.triage_reason,
    postgresql_where=text("triage_reason IS NOT NULL"),
)
Index(
    "ix_incidents_resolved_at",
    Incident.resolved_at,
    postgresql_where=text("status = 'Resolved'"),
)
_ACTIVE = "status IN ('Open', 'In Progress', 'Blocked')"
# Workload per engineer and status (dashboards, auto-routing); also serves
# plain assigned_to lookups, so it replaces the single-column index.
Index("ix_incidents_assignee_status", Incident.assigned_to, Incident.status)
# The SLA alert job only scans tickets whose timer is still running.
Index(
    "ix_incidents_active_ack_due",
    Incident.ack_due_at,
    postgresql_where=text(f"{_ACTIVE} AND acknowledged_at IS NULL"),
)
Index(
    "ix_incidents_active_resolve_due",
    Incident.resolve_due_at,
    postgresql_where=text(f"{_ACTIVE} AND resolved_at IS NULL"),
)


@event.listens_for(Incident, "before_insert")
def _default_due_times(_mapper, _connection, incident: Incident) -> None:
    """Every incident gets SLA due times, including rows created outside the API."""
    if incident.created_at is None:
        incident.created_at = utcnow()
    if incident.priority is None:
        incident.priority = IncidentPriority.MEDIUM
    if incident.ack_due_at is None or incident.resolve_due_at is None:
        incident.set_due_times()
