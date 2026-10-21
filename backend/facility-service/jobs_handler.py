"""Jobs Lambda entry point, invoked by EventBridge every 15 minutes; never over HTTP."""

import asyncio
import json
import logging

from facility.config import settings
from facility.database import SessionLocal
from facility.jobs import run_jobs

logger = logging.getLogger("facility.jobs")


def handler(event: dict, context: object) -> dict:
    """Run auto-close, SLA alerts and upload cleanup; `{"dry_run": true}` only checks."""
    if "requestContext" in event:
        raise ValueError("Scheduled or direct invocations only")
    storage = None
    if settings.attachments_bucket:
        from facility.storage import get_storage

        storage = get_storage()
    result = asyncio.run(run_jobs(SessionLocal, storage, dry_run=bool(event.get("dry_run"))))
    logger.info(json.dumps({"event": "jobs_run", **result}))
    return result
