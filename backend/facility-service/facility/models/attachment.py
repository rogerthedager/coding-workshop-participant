"""Image attached to a ticket, stored in S3."""

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from facility.database import Base
from facility.db_types import enum_check, varchar_enum
from facility.enums import MAX_ATTACHMENT_BYTES, AttachmentStatus, AttachmentType
from facility.models.base import Entity


class Attachment(Entity, Base):
    """Image stored in the private attachments bucket; visibility follows its note."""

    __tablename__ = "attachments"
    __table_args__ = (
        UniqueConstraint("s3_key", name="uq_attachments_key"),
        CheckConstraint(enum_check("content_type", AttachmentType), name="ck_attachments_type"),
        CheckConstraint(enum_check("status", AttachmentStatus), name="ck_attachments_status"),
        CheckConstraint(
            f"size_bytes BETWEEN 1 AND {MAX_ATTACHMENT_BYTES}", name="ck_attachments_size"
        ),
    )
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id"), index=True)
    note_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("incident_notes.id"))
    uploaded_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    s3_key: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[AttachmentType] = mapped_column(varchar_enum(AttachmentType, 16))
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[AttachmentStatus] = mapped_column(
        varchar_enum(AttachmentStatus, 16), default=AttachmentStatus.PENDING, nullable=False
    )
