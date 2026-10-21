"""Category group: routes new tickets to its owning team."""

import uuid

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from facility.database import Base
from facility.models.base import Entity


class CategoryGroup(Entity, Base):
    """Routing group above top-level categories; its team receives new tickets."""

    __tablename__ = "category_groups"
    __table_args__ = (Index("uq_category_groups_name_lower", text("lower(name)"), unique=True),)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    team_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("teams.id"), index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
