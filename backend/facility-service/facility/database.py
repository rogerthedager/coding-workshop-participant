"""PostgreSQL session lifecycle shared by all routers."""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from facility.config import settings


class Base(DeclarativeBase):
    """Metadata base used by Alembic migrations."""


engine: AsyncEngine | None = None


def get_engine() -> AsyncEngine:
    """Create the engine lazily; require encrypted PostgreSQL outside local mode."""
    global engine
    if engine is None:
        engine = create_async_engine(
            settings.resolved_database_url(),
            poolclass=NullPool,
            connect_args={} if settings.is_local else {"ssl": "require"},
        )
    return engine


def SessionLocal() -> AsyncSession:
    """Retain the conventional session-factory interface with lazy configuration."""
    return async_sessionmaker(get_engine(), expire_on_commit=False)()


async def get_db() -> AsyncIterator[AsyncSession]:
    """Use one transaction context per request, never committing implicitly."""
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
