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
_session_factory: async_sessionmaker[AsyncSession] | None = None
_reuse = False

# A warm Lambda container serves one request at a time, so a tiny pool is enough.
# Pre-ping replaces connections the database closed while the container was frozen;
# recycling stays well under typical idle timeouts.
POOL_OPTIONS = {"pool_size": 2, "max_overflow": 3, "pool_pre_ping": True, "pool_recycle": 300}


def reuse_connections() -> None:
    """Keep database connections open between requests.

    Only for processes that run every request on one long-lived event loop: the HTTP
    Lambda (Mangum keeps one loop per warm container) and uvicorn. Entry points that
    call asyncio.run() per invocation (jobs, migrations, seed) must not call this,
    because a pooled connection cannot move to a new event loop.
    """
    global _reuse, engine, _session_factory
    if _reuse:
        return
    _reuse = True
    # Any engine made so far used NullPool and holds no open connections.
    engine = None
    _session_factory = None


def get_engine() -> AsyncEngine:
    """Create the engine lazily; require encrypted PostgreSQL outside local mode."""
    global engine
    if engine is None:
        pooling = POOL_OPTIONS if _reuse else {"poolclass": NullPool}
        engine = create_async_engine(
            settings.resolved_database_url(),
            connect_args={} if settings.is_local else {"ssl": "require"},
            **pooling,
        )
    return engine


def SessionLocal() -> AsyncSession:
    """Retain the conventional session-factory interface with lazy configuration."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _session_factory()


async def get_db() -> AsyncIterator[AsyncSession]:
    """Use one transaction context per request, never committing implicitly."""
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
