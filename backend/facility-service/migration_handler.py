"""Separate AWS control-plane entrypoint for a restricted migration-only Lambda."""

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config

from facility.seed import seed


def handler(event: dict, context: object) -> dict:
    """Apply migrations and idempotent reference/admin seed; never expose over HTTP."""
    if "requestContext" in event or event.get("operation") != "migrate":
        raise ValueError("Control-plane migrate operation required")
    command.upgrade(Config(str(Path(__file__).with_name("alembic.ini"))), "head")
    return {"status": "migrated", "seed": asyncio.run(seed(include_demo=False))}
