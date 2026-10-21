"""Employee account: email, password hash, roles and lockout state."""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, validates

from facility.database import Base
from facility.db_types import UserRolesJSON
from facility.enums import UserRole
from facility.models.base import Entity


class User(Entity, Base):
    """Employee identity with additive admin and engineer roles."""

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email", name="uq_users_email"),
        CheckConstraint("email = lower(email)", name="ck_users_email_lower"),
        CheckConstraint("jsonb_typeof(roles) = 'array'", name="ck_users_roles_array"),
        CheckConstraint(
            "roles <@ '[" + ",".join(f'"{role.value}"' for role in UserRole) + "]'::jsonb",
            name="ck_users_roles_values",
        ),
    )
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    roles: Mapped[list[UserRole]] = mapped_column(UserRolesJSON(), default=list, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    token_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_login_at: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    default_seat_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("seats.id"))

    @validates("roles")
    def validate_roles(self, _key: str, value: list[UserRole]) -> list[UserRole]:
        """Normalize legacy values on assignment and reject unsupported role grants."""
        if not isinstance(value, list):
            raise ValueError("Roles must be a list")
        return [UserRole(role) for role in value]
