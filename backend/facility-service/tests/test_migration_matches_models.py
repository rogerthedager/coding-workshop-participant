"""The checked-in migrations produce exactly the schema the ORM models describe."""

from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import text

from facility.database import Base


async def test_migrations_match_models(db):
    connection = await db.connection()
    original = await connection.scalar(text("SHOW search_path"))
    schema = f"acme_migrate_{uuid4().hex}"
    await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
    try:

        def migrate_and_compare(sync_connection):
            config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
            config.attributes["connection"] = sync_connection
            command.upgrade(config, "head")
            context = MigrationContext.configure(
                sync_connection, opts={"compare_type": True, "include_schemas": False}
            )
            return compare_metadata(context, Base.metadata)

        differences = await connection.run_sync(migrate_and_compare)
        # Alembic cannot compare expression indexes; it reports them as remove + add.
        expression_indexes = {
            "ix_incidents_search",
            "uq_teams_name_lower",
            "uq_category_groups_name_lower",
        }
        remaining = [
            diff
            for diff in differences
            if not (
                isinstance(diff, tuple)
                and diff[0] in {"add_index", "remove_index"}
                and diff[1].name in expression_indexes
            )
        ]
        assert remaining == [], "\n".join(repr(item) for item in remaining)
    finally:
        await connection.execute(text(f"SET LOCAL search_path TO {original}"))
        await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await db.commit()
