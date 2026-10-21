"""Execute migrations using the same validated PostgreSQL configuration as the API."""

import asyncio

from alembic import context

from facility import models  # noqa: F401
from facility.database import Base, get_engine

target_metadata = Base.metadata


def run_sync(connection):
    """Serialize migration runners within one PostgreSQL transaction."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        transaction_per_migration=False,
    )
    with context.begin_transaction():
        connection.exec_driver_sql("SELECT pg_advisory_xact_lock(624030119)")
        context.run_migrations()


async def run_online():
    """Acquire a short-lived connection; do not alter schemas at import."""
    engine = get_engine()
    async with engine.connect() as connection:
        await connection.run_sync(run_sync)
    await engine.dispose()


if context.config.attributes.get("connection") is not None:
    run_sync(context.config.attributes["connection"])
elif context.is_offline_mode():
    raise RuntimeError("Offline migrations are not supported; use an isolated PostgreSQL database")
else:
    asyncio.run(run_online())
