"""Incident category (two levels: top-level and subcategory)."""

import uuid

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from facility.database import Base
from facility.models.base import Entity


class Category(Entity, Base):
    """At most two-level category, depth enforced by mutation service."""

    __tablename__ = "categories"
    __table_args__ = (
        CheckConstraint(
            "parent_id IS NULL OR group_id IS NULL", name="ck_categories_group_top_level"
        ),
    )
    name: Mapped[str] = mapped_column(String(120))
    group_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("category_groups.id"), index=True
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("categories.id"), index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
