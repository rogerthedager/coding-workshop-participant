"""Engineer team and its lead."""

import uuid

from sqlalchemy import Boolean, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from facility.database import Base
from facility.models.base import Entity


class Team(Entity, Base):
    """Engineer team; its lead reads and summarizes members' tickets."""

    __tablename__ = "teams"
    __table_args__ = (
        UniqueConstraint("lead_user_id", name="uq_teams_lead"),
        Index("uq_teams_name_lower", text("lower(name)"), unique=True),
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    lead_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
