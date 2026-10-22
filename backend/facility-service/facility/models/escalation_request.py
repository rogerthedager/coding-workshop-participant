"""Escalation request and the admin decision on it."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Text, text
from sqlalchemy.orm import Mapped, mapped_column, validates

from facility.database import Base
from facility.db_types import enum_check, varchar_enum
from facility.enums import EscalationState
from facility.models.base import Entity, utcnow


class EscalationRequest(Entity, Base):
    """One pending escalation at a time, enforced by PostgreSQL."""

    __tablename__ = "escalation_requests"
    __table_args__ = (
        CheckConstraint(
            enum_check("state", EscalationState),
            name="ck_escalation_state",
        ),
        Index(
            "uq_pending_escalation",
            "incident_id",
            unique=True,
            postgresql_where=text(f"state = '{EscalationState.PENDING.value}'"),
        ),
        # Latest escalation per ticket; also serves plain incident_id lookups.
        Index("ix_escalation_requests_latest", "incident_id", "requested_at", "id"),
    )
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id"))
    requester_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str] = mapped_column(Text)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    state: Mapped[EscalationState] = mapped_column(
        varchar_enum(EscalationState, 16), default=EscalationState.PENDING
    )
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_reason: Mapped[str | None] = mapped_column(Text)
    action_plan: Mapped[str | None] = mapped_column(Text)

    @validates("state")
    def validate_state(self, _key: str, value: EscalationState) -> EscalationState:
        """Normalize escalation states before workflow decisions."""
        return EscalationState(value)
