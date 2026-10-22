"""Separate AWS control-plane entrypoint for a restricted migration-only Lambda."""

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config

from facility.config import settings
from facility.seed import seed


def _put_object():
    """Store showcase photos in the attachments bucket, or skip them when there is none."""
    if not settings.attachments_bucket:
        return None
    from facility.storage import S3Storage

    client = S3Storage(settings.attachments_bucket, settings.attachments_endpoint_url).client

    def put(key: str, body: bytes, content_type: str) -> None:
        client.put_object(
            Bucket=settings.attachments_bucket, Key=key, Body=body, ContentType=content_type
        )

    return put


def handler(event: dict, context: object) -> dict:
    """Apply migrations and idempotent seed; never expose over HTTP.

    `{"operation": "migrate"}` runs migrations plus the reference/admin seed.
    `{"operation": "showcase", "incidents": 120}` also adds demo data with 10+ records per
    feature; seed.demo_allowed() requires FACILITY_ALLOW_DEMO_SEED=true and a strong
    DEMO_PASSWORD in the function environment (bin/facility-aws-migrate.sh sets both briefly).
    """
    operation = event.get("operation")
    if "requestContext" in event or operation not in {"migrate", "showcase"}:
        raise ValueError("Control-plane migrate operation required")
    command.upgrade(Config(str(Path(__file__).with_name("alembic.ini"))), "head")
    if operation == "migrate":
        return {"status": "migrated", "seed": asyncio.run(seed(include_demo=False))}
    incidents = max(0, min(int(event.get("incidents", 120)), 200))
    result = asyncio.run(
        seed(include_demo=True, incidents=incidents, showcase=True, put_object=_put_object())
    )
    return {"status": "seeded", "seed": result}
