"""Operator-only expired authentication-session retention command."""

import argparse
import asyncio
from datetime import timedelta

from sqlalchemy import delete, exists, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from facility.database import SessionLocal
from facility.models import RefreshSession, utcnow


async def cleanup_expired_sessions(db: AsyncSession) -> int:
    """Purge sessions seven days after expiry; never delete business records."""
    cutoff = utcnow() - timedelta(days=7)
    reference = aliased(RefreshSession)
    retained_reference = exists(
        select(reference.id).where(
            reference.replaced_by_id == RefreshSession.id,
            reference.expires_at >= cutoff,
        )
    )
    result = await db.execute(
        delete(RefreshSession).where(
            RefreshSession.expires_at < cutoff,
            ~retained_reference,
        )
    )
    await db.commit()
    return result.rowcount


async def run_cleanup() -> None:
    """Run against the configured facility database and emit only a record count."""
    async with SessionLocal() as db:
        deleted = await cleanup_expired_sessions(db)
    print(f"Removed {deleted} expired authentication sessions after seven-day grace.")


def main() -> None:
    """Require an explicit cleanup verb and sanitize configuration/DB failures."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["cleanup"])
    parser.parse_args()
    try:
        asyncio.run(run_cleanup())
    except (RuntimeError, ValueError, OSError, SQLAlchemyError):
        raise SystemExit(
            "Session cleanup failed; check facility database configuration and access."
        ) from None


if __name__ == "__main__":
    main()
