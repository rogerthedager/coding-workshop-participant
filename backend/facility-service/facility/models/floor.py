"""Floor within a building."""

import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from facility.database import Base
from facility.models.base import Entity


class Floor(Entity, Base):
    """Building floor with a unique level within its building."""

    __tablename__ = "floors"
    __table_args__ = (
        UniqueConstraint("building_id", "level_number", name="uq_floors_building_level"),
    )
    building_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("buildings.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    level_number: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
