"""PostgreSQL entities for the core MVP; business records are retained."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, validates

from facility.database import Base
from facility.db_types import TextEnum, UserRolesJSON, enum_check, varchar_enum
from facility.enums import (
    BlockedReason,
    EngineerAvailability,
    EscalationState,
    IncidentEventType,
    IncidentPriority,
    IncidentStatus,
    NoteVisibility,
    UserRole,
)


def utcnow() -> datetime:
    """Return an aware UTC timestamp."""
    return datetime.now(UTC)


class Entity:
    """Common immutable ID and UTC timestamps."""

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


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

    @validates("roles")
    def validate_roles(self, _key: str, value: list[UserRole]) -> list[UserRole]:
        """Normalize legacy values on assignment and reject unsupported role grants."""
        if not isinstance(value, list):
            raise ValueError("Roles must be a list")
        return [UserRole(role) for role in value]


class RefreshSession(Entity, Base):
    """Hashed refresh-token lineage with transactionally enforced rotation."""

    __tablename__ = "refresh_sessions"
    __table_args__ = (UniqueConstraint("token_hash", name="uq_refresh_token_hash"),)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), default=uuid.uuid4, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("refresh_sessions.id"))


class EngineerProfile(Entity, Base):
    """Engineer activation and self-managed availability."""

    __tablename__ = "engineer_profiles"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_engineer_user"),
        CheckConstraint(
            enum_check("availability", EngineerAvailability),
            name="ck_engineer_availability",
        ),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(40))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    availability: Mapped[EngineerAvailability] = mapped_column(
        varchar_enum(EngineerAvailability, 16),
        default=EngineerAvailability.AVAILABLE,
        nullable=False,
    )

    @validates("availability")
    def validate_availability(self, _key: str, value: EngineerAvailability) -> EngineerAvailability:
        """Keep workflow code typed even before a profile has been flushed."""
        return EngineerAvailability(value)


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


class Seat(Entity, Base):
    """Optional seat location."""

    __tablename__ = "seats"
    __table_args__ = (UniqueConstraint("floor_id", "code", name="uq_seats_floor_code"),)
    floor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("floors.id"), index=True)
    code: Mapped[str] = mapped_column(String(40))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Category(Entity, Base):
    """At most two-level category, depth enforced by mutation service."""

    __tablename__ = "categories"
    name: Mapped[str] = mapped_column(String(120))
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("categories.id"), index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Incident(Entity, Base):
    """Reported issue and its current workflow state."""

    __tablename__ = "incidents"
    __table_args__ = (
        UniqueConstraint("number", name="uq_incidents_number"),
        CheckConstraint(
            enum_check("status", IncidentStatus),
            name="ck_incidents_status",
        ),
        CheckConstraint(
            enum_check("priority", IncidentPriority),
            name="ck_incidents_priority",
        ),
        CheckConstraint(
            enum_check("requested_priority", IncidentPriority),
            name="ck_incidents_requested_priority",
        ),
        CheckConstraint("reopen_count >= 0", name="ck_incidents_reopen_count"),
        CheckConstraint(
            enum_check("blocked_reason", BlockedReason), name="ck_incidents_blocked_reason"
        ),
        Index(
            "ix_incidents_search",
            text("to_tsvector('english', title || ' ' || description)"),
            postgresql_using="gin",
        ),
    )
    number: Mapped[str] = mapped_column(String(24), nullable=False)
    title: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text)
    category_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("categories.id"), index=True)
    building_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("buildings.id"), index=True)
    floor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("floors.id"), index=True)
    seat_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("seats.id"), index=True)
    status: Mapped[IncidentStatus] = mapped_column(
        varchar_enum(IncidentStatus, 16), default=IncidentStatus.OPEN, index=True
    )
    requested_priority: Mapped[IncidentPriority] = mapped_column(
        varchar_enum(IncidentPriority, 16), default=IncidentPriority.MEDIUM
    )
    priority: Mapped[IncidentPriority] = mapped_column(
        varchar_enum(IncidentPriority, 16), default=IncidentPriority.MEDIUM, index=True
    )
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    blocked_reason: Mapped[BlockedReason | None] = mapped_column(TextEnum(BlockedReason))
    blocked_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_in_progress_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reopen_count: Mapped[int] = mapped_column(Integer, default=0)

    @validates("status")
    def validate_status(self, _key: str, value: IncidentStatus) -> IncidentStatus:
        """Expose a shared status member immediately on construction or update."""
        return IncidentStatus(value)

    @validates("priority", "requested_priority")
    def validate_priority(self, _key: str, value: IncidentPriority) -> IncidentPriority:
        """Apply one priority domain to both employee requests and effective urgency."""
        return IncidentPriority(value)

    @validates("blocked_reason")
    def validate_blocked_reason(
        self, _key: str, value: BlockedReason | None
    ) -> BlockedReason | None:
        """Use a fixed reason code, retaining absence for incidents that are not blocked."""
        return None if value is None else BlockedReason(value)


Index("ix_incidents_created_at", Incident.created_at)
Index("ix_incidents_updated_at", Incident.updated_at)


class IncidentNote(Entity, Base):
    """Plain-text public/internal note; hidden contents are retained."""

    __tablename__ = "incident_notes"
    __table_args__ = (
        CheckConstraint(enum_check("visibility", NoteVisibility), name="ck_notes_visibility"),
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


class IncidentEvent(Entity, Base):
    """Append-only sanitized audit payload."""

    __tablename__ = "incident_events"
    __table_args__ = (
        CheckConstraint(enum_check("type", IncidentEventType), name="ck_incident_events_type"),
    )
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id"), index=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    type: Mapped[IncidentEventType] = mapped_column(varchar_enum(IncidentEventType, 48))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)

    @validates("type")
    def validate_type(self, _key: str, value: IncidentEventType) -> IncidentEventType:
        """Keep audit categories within the shared event domain."""
        return IncidentEventType(value)


class EscalationRequest(Entity, Base):
    """One pending escalation at a time, enforced by PostgreSQL."""

    __tablename__ = "escalation_requests"
    __table_args__ = (
        CheckConstraint(
            enum_check("state", EscalationState),
            name="ck_escalation_state",
        ),
        Index(
            "uq_pending_escalation",
            "incident_id",
            unique=True,
            postgresql_where=text(f"state = '{EscalationState.PENDING.value}'"),
        ),
    )
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id"), index=True)
    requester_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str] = mapped_column(Text)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    state: Mapped[EscalationState] = mapped_column(
        varchar_enum(EscalationState, 16), default=EscalationState.PENDING
    )
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_reason: Mapped[str | None] = mapped_column(Text)
    action_plan: Mapped[str | None] = mapped_column(Text)

    @validates("state")
    def validate_state(self, _key: str, value: EscalationState) -> EscalationState:
        """Normalize escalation states before workflow decisions."""
        return EscalationState(value)
