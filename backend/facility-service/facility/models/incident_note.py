"""Public or internal note on a ticket."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Text, text
from sqlalchemy.orm import Mapped, mapped_column, validates

from facility.database import Base
from facility.db_types import enum_check, varchar_enum
from facility.enums import NoteVisibility
from facility.models.base import Entity


class IncidentNote(Entity, Base):
    """Plain-text public/internal note; hidden contents are retained."""

    __tablename__ = "incident_notes"
    __table_args__ = (
        CheckConstraint(enum_check("visibility", NoteVisibility), name="ck_notes_visibility"),
        # Dashboards ask for each ticket's latest visible public update.
        Index(
            "ix_incident_notes_public",
            "incident_id",
            "created_at",
            postgresql_where=text(
                f"visibility = '{NoteVisibility.PUBLIC.value}' AND hidden_at IS NULL"
            ),
        ),
    )
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id"), index=True)
    author_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    body: Mapped[str] = mapped_column(Text)
    visibility: Mapped[NoteVisibility] = mapped_column(
        varchar_enum(NoteVisibility, 16), default=NoteVisibility.PUBLIC
    )
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hidden_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hidden_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    hide_reason: Mapped[str | None] = mapped_column(Text)

    @validates("visibility")
    def validate_visibility(self, _key: str, value: NoteVisibility) -> NoteVisibility:
        """Validate a note audience before authorization uses it."""
        return NoteVisibility(value)
