"""Append-only audit event on a ticket."""

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Index, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, validates

from facility.database import Base
from facility.db_types import enum_check, varchar_enum
from facility.enums import IncidentEventType, IncidentStatus
from facility.models.base import Entity


class IncidentEvent(Entity, Base):
    """Append-only sanitized audit payload."""

    __tablename__ = "incident_events"
    __table_args__ = (
        CheckConstraint(enum_check("type", IncidentEventType), name="ck_incident_events_type"),
        # Report metrics count events of one type within a date range.
        Index("ix_incident_events_type_created", "type", "created_at"),
        # "Who resolved it" looks up each ticket's latest resolution event.
        Index(
            "ix_incident_events_resolved",
            "incident_id",
            "created_at",
            postgresql_where=text(
                f"type = '{IncidentEventType.STATUS_CHANGED.value}' "
                f"AND (payload ->> 'to') = '{IncidentStatus.RESOLVED.value}'"
            ),
        ),
    )
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id"), index=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    type: Mapped[IncidentEventType] = mapped_column(varchar_enum(IncidentEventType, 48))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)

    @validates("type")
    def validate_type(self, _key: str, value: IncidentEventType) -> IncidentEventType:
        """Keep audit categories within the shared event domain."""
        return IncidentEventType(value)
