"""In-app notification."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, validates

from facility.database import Base
from facility.db_types import enum_check, varchar_enum
from facility.enums import NotificationType
from facility.models.base import utcnow


class Notification(Base):
    """In-app notification written in the same transaction as its cause."""

    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint(enum_check("type", NotificationType), name="ck_notifications_type"),
        Index("ix_notifications_user_unread", "user_id", "read_at"),
        Index("ix_notifications_user_created", "user_id", "created_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id"), nullable=False)
    event_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("incident_events.id"))
    type: Mapped[NotificationType] = mapped_column(varchar_enum(NotificationType, 32))
    message: Mapped[str] = mapped_column(Text, nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    @validates("type")
    def validate_type(self, _key: str, value: NotificationType) -> NotificationType:
        """Keep notification categories within the shared domain."""
        return NotificationType(value)
