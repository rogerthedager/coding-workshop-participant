"""Engineer profile: availability, skills, team and home buildings."""

import uuid

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, validates

from facility.database import Base
from facility.db_types import EngineerSkillsJSON, enum_check, varchar_enum
from facility.enums import MAX_ENGINEER_SKILLS, EngineerAvailability, EngineerSkill
from facility.models.base import Entity


def validate_skill_list(value: object) -> list[EngineerSkill]:
    """Return unique, supported engineer skills within the per-engineer limit."""
    if not isinstance(value, list):
        raise ValueError("Skills must be a list")
    skills = [EngineerSkill(skill) for skill in value]
    if len(set(skills)) != len(skills):
        raise ValueError("Skills must not repeat")
    if len(skills) > MAX_ENGINEER_SKILLS:
        raise ValueError(f"An engineer may have at most {MAX_ENGINEER_SKILLS} skills")
    return skills


class EngineerProfile(Entity, Base):
    """Engineer activation, self-managed availability and up to two skills."""

    __tablename__ = "engineer_profiles"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_engineer_user"),
        CheckConstraint(
            enum_check("availability", EngineerAvailability),
            name="ck_engineer_availability",
        ),
        CheckConstraint("jsonb_typeof(skills) = 'array'", name="ck_engineer_skills_array"),
        CheckConstraint(
            "CASE WHEN jsonb_typeof(skills) = 'array' "
            f"THEN jsonb_array_length(skills) <= {MAX_ENGINEER_SKILLS} END",
            name="ck_engineer_skills_limit",
        ),
        CheckConstraint(
            "skills <@ '[" + ",".join(f'"{skill.value}"' for skill in EngineerSkill) + "]'::jsonb",
            name="ck_engineer_skills_values",
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
    skills: Mapped[list[EngineerSkill]] = mapped_column(
        EngineerSkillsJSON(), default=list, server_default=text("'[]'::jsonb"), nullable=False
    )
    team_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("teams.id"), index=True)
    home_building_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), default=list, server_default=text("'{}'"), nullable=False
    )

    @validates("availability")
    def validate_availability(self, _key: str, value: EngineerAvailability) -> EngineerAvailability:
        """Keep workflow code typed even before a profile has been flushed."""
        return EngineerAvailability(value)

    @validates("skills")
    def validate_skills(self, _key: str, value: list[EngineerSkill]) -> list[EngineerSkill]:
        """Reject unknown, repeated or too many skills before they reach PostgreSQL."""
        return validate_skill_list(value)
