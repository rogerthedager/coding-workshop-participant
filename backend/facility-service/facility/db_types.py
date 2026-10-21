"""Strict domain enum mappings that preserve the existing PostgreSQL storage types."""

from enum import StrEnum
from typing import TypeVar

from sqlalchemy import Enum as SQLAlchemyEnum
from sqlalchemy import Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

from facility.enums import EngineerSkill, UserRole

DomainEnum = TypeVar("DomainEnum", bound=StrEnum)


def enum_check(column: str, enum_type: type[DomainEnum]) -> str:
    """Derive runtime CHECK values from the same domain used by the application."""
    values = ",".join("'" + member.value.replace("'", "''") + "'" for member in enum_type)
    return f"{column} IN ({values})"


def varchar_enum(enum_type: type[DomainEnum], length: int) -> SQLAlchemyEnum:
    """Persist enum values, never names, in the original VARCHAR column width."""
    return SQLAlchemyEnum(
        enum_type,
        values_callable=lambda members: [member.value for member in members],
        native_enum=False,
        validate_strings=True,
        create_constraint=False,
        length=length,
    )


class TextEnum(TypeDecorator):
    """Round-trip an enum through an existing TEXT column without a type migration."""

    impl = Text
    cache_ok = True

    def __init__(self, enum_type: type[DomainEnum]) -> None:
        self.enum_type = enum_type
        super().__init__()

    def process_bind_param(self, value: StrEnum | str | None, dialect: Dialect) -> str | None:
        """Validate assignments again when binding SQL, including Core updates."""
        return None if value is None else self.enum_type(value).value

    def process_result_value(self, value: str | None, dialect: Dialect) -> StrEnum | None:
        """Expose actual enum members to business logic after database reads."""
        return None if value is None else self.enum_type(value)


class UserRolesJSON(TypeDecorator):
    """Retain JSONB role storage and operators while exposing typed role members."""

    impl = JSONB
    cache_ok = True

    def process_bind_param(self, value: object, dialect: Dialect) -> list[str]:
        """Only a list of supported additive roles may be stored."""
        if not isinstance(value, list):
            raise ValueError("Roles must be a list")
        return [UserRole(role).value for role in value]

    def process_result_value(self, value: list[str], dialect: Dialect) -> list[UserRole]:
        """Rehydrate JSON values into the shared role type."""
        return [UserRole(role) for role in value]


class EngineerSkillsJSON(TypeDecorator):
    """Store engineer skills as a JSONB array while exposing typed skill members."""

    impl = JSONB
    cache_ok = True

    def process_bind_param(self, value: object, dialect: Dialect) -> list[str]:
        """Only a list of supported skills may be stored."""
        if not isinstance(value, list):
            raise ValueError("Skills must be a list")
        return [EngineerSkill(skill).value for skill in value]

    def process_result_value(self, value: list[str], dialect: Dialect) -> list[EngineerSkill]:
        """Rehydrate JSON values into the shared skill type."""
        return [EngineerSkill(skill) for skill in value]
