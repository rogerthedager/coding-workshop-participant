"""Environment-based configuration; imports never read secrets or connect."""

import json
from urllib.parse import quote, urlsplit

from botocore.exceptions import BotoCoreError, ClientError
from pydantic import AliasChoices, Field, PrivateAttr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings for the isolated facility service."""

    model_config = SettingsConfigDict(
        env_prefix="FACILITY_",
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
        hide_input_in_errors=True,
    )
    database_url: str = Field(default="", repr=False)
    jwt_secret: str = Field(default="", repr=False)
    postgres_host: str = Field(default="", validation_alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, validation_alias="POSTGRES_PORT")
    postgres_user: str = Field(default="", validation_alias="POSTGRES_USER")
    postgres_pass: str = Field(default="", validation_alias="POSTGRES_PASS", repr=False)
    postgres_name: str = Field(default="", validation_alias="POSTGRES_NAME")
    secret_arn: str = ""
    app_name: str = Field(default="", validation_alias="APP_NAME")
    _secret_cache: dict | None = PrivateAttr(default=None)
    is_local: bool = Field(
        default=False,
        validation_alias=AliasChoices("IS_LOCAL", "FACILITY_IS_LOCAL"),
    )
    allowed_origins: list[str] = []
    access_minutes: int = 15
    refresh_days: int = 7
    # Private image bucket; empty disables attachments (endpoints return 503).
    attachments_bucket: str = ""
    # Optional S3-compatible endpoint (MinIO) for local development.
    attachments_endpoint_url: str = ""

    @field_validator("database_url")
    @classmethod
    def postgres_only(cls, value: str) -> str:
        """Refuse accidental fallback to a different persistence engine."""
        if value and not value.startswith("postgresql+asyncpg://"):
            raise ValueError("FACILITY_DATABASE_URL must use postgresql+asyncpg")
        return value

    @model_validator(mode="after")
    def validate_origins(self) -> "Settings":
        """Permit exact origins only, with HTTPS mandatory outside local mode."""
        for origin in self.allowed_origins:
            parsed = urlsplit(origin)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
                or "*" in origin
                or (not self.is_local and parsed.scheme != "https")
            ):
                raise ValueError("Configure exact HTTPS public origins; HTTP is local-only")
        return self

    def secret_values(self) -> dict:
        """Lazily resolve the dedicated facility secret; never log its contents."""
        if self._secret_cache is not None:
            return self._secret_cache
        name = self.secret_arn or (f"{self.app_name}/facility" if self.app_name else "")
        if not name:
            return {}
        try:
            import boto3

            raw = boto3.client("secretsmanager").get_secret_value(SecretId=name)
            values = json.loads(raw["SecretString"])
            if not isinstance(values, dict):
                raise TypeError("Secret must be a JSON object")
        except (ImportError, KeyError, TypeError, ValueError, BotoCoreError, ClientError):
            raise RuntimeError("Facility secret is unavailable or invalid") from None
        self._secret_cache = values
        return values

    def resolved_jwt_secret(self) -> str:
        """Resolve an explicit environment secret before consulting Secrets Manager."""
        if self.jwt_secret:
            return self.jwt_secret
        values = self.secret_values()
        return str(values.get("jwt_secret") or values.get("FACILITY_JWT_SECRET") or "")

    def resolved_origins(self) -> list[str]:
        """Use configured public origins, adding loopback only in local mode."""
        origins = list(self.allowed_origins)
        if self.is_local:
            origins.extend(
                [
                    "http://localhost:5173",
                    "http://localhost:8019",
                    "http://127.0.0.1:5173",
                    "http://127.0.0.1:8019",
                ]
            )
        return list(dict.fromkeys(origin.rstrip("/") for origin in origins))

    def resolved_database_url(self) -> str:
        """Accept Workshop POSTGRES_* injection without any production fallback."""
        if self.database_url:
            return self.database_url
        values = {}
        if not all(
            (
                self.postgres_host,
                self.postgres_user,
                self.postgres_pass,
                self.postgres_name,
            )
        ):
            values = self.secret_values()
        explicit = values.get("database_url") or values.get("FACILITY_DATABASE_URL")
        if explicit:
            return self.postgres_only(str(explicit))
        host = self.postgres_host or values.get("POSTGRES_HOST") or values.get("host")
        user = self.postgres_user or values.get("POSTGRES_USER") or values.get("username")
        password = self.postgres_pass or values.get("POSTGRES_PASS") or values.get("password")
        name = self.postgres_name or values.get("POSTGRES_NAME") or values.get("dbname")
        port = values.get("POSTGRES_PORT") or values.get("port") or self.postgres_port
        if not all((host, user, password, name)):
            raise RuntimeError("PostgreSQL connection is not configured")
        host_value = str(host)
        if ":" in host_value and not host_value.startswith("["):
            host_value = f"[{host_value}]"
        return (
            f"postgresql+asyncpg://{quote(str(user), safe='')}:{quote(str(password), safe='')}"
            f"@{host_value}:{int(port)}/{quote(str(name), safe='')}"
        )

    @property
    def cookie_secure(self) -> bool:
        """Only explicitly local development may use insecure cookies."""
        return not self.is_local


settings = Settings()
