"""Seat within a floor."""

import uuid

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from facility.database import Base
from facility.models.base import Entity


class Seat(Entity, Base):
    """Optional seat location."""

    __tablename__ = "seats"
    __table_args__ = (UniqueConstraint("floor_id", "code", name="uq_seats_floor_code"),)
    floor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("floors.id"), index=True)
    code: Mapped[str] = mapped_column(String(40))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
