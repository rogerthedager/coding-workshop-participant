"""Office building."""


from sqlalchemy import Boolean, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from facility.database import Base
from facility.models.base import Entity


class Building(Entity, Base):
    """Office building retained after deactivation."""

    __tablename__ = "buildings"
    __table_args__ = (
        UniqueConstraint("name", name="uq_buildings_name"),
        UniqueConstraint("code", name="uq_buildings_code"),
    )
    name: Mapped[str] = mapped_column(String(120))
    code: Mapped[str] = mapped_column(String(32))
    address: Mapped[str | None] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
