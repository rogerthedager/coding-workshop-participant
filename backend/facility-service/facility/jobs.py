"""Scheduled work run every 15 minutes (FR-12). Every step is idempotent.

1. Auto-close Resolved tickets 72 hours after resolution, one transaction each.
2. SLA alerts: each threshold fires once per ticket, marked on the incident row.
3. Delete pending uploads older than one hour.
"""

import asyncio
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from sqlalchemy import and_, delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from facility.enums import (
    AUTO_CLOSE_HOURS,
    AttachmentStatus,
    CloseKind,
    IncidentStatus,
    NotificationType,
)
from facility.models import Attachment, Incident
from facility.notifications import active_admin_ids, commit, notify
from facility.sla import ACTIVE_STATUSES, reached_risk
from facility.workflow import close

JOB_LOCK = 624030121
SessionFactory = Callable[[], AsyncSession]


async def auto_close(sessions: SessionFactory) -> int:
    """Close each eligible ticket in its own transaction, re-checking under the lock."""
    closed = 0
    while True:
        async with sessions() as db:
            incident = await db.scalar(
                select(Incident)
                .where(
                    Incident.status == IncidentStatus.RESOLVED,
                    Incident.resolved_at <= func.now() - timedelta(hours=AUTO_CLOSE_HOURS),
                )
                .order_by(Incident.resolved_at, Incident.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if incident is None:
                return closed
            await close(db, incident, None, CloseKind.AUTO)
            await commit(db)
            closed += 1


def _threshold(due, at_risk: bool):
    return reached_risk(func.now(), due) if at_risk else func.now() > due


ALERTS = [
    # (marker column, due column, at_risk, window-active condition, label)
    ("ack_at_risk_alerted_at", "ack_due_at", True, "ack", "acknowledgment SLA is at risk"),
    ("ack_breach_alerted_at", "ack_due_at", False, "ack", "acknowledgment SLA was breached"),
    ("resolve_at_risk_alerted_at", "resolve_due_at", True, "resolve", "resolution SLA is at risk"),
    ("resolve_breach_alerted_at", "resolve_due_at", False, "resolve", "resolution SLA was breached"),
]


async def sla_alerts(db: AsyncSession) -> int:
    """Mark and notify each newly reached threshold once (assignee and every admin)."""
    admins = await active_admin_ids(db)
    sent = 0
    for marker_name, due_name, at_risk, window, label in ALERTS:
        marker = getattr(Incident, marker_name)
        due = getattr(Incident, due_name)
        active = Incident.status.in_(ACTIVE_STATUSES)
        if window == "ack":
            active = and_(active, Incident.acknowledged_at.is_(None))
        else:
            active = and_(active, Incident.resolved_at.is_(None))
        rows = (
            await db.execute(
                update(Incident)
                .where(marker.is_(None), active, _threshold(due, at_risk))
                .values({marker_name: func.now()})
                .returning(Incident.id)
            )
        ).scalars().all()
        for incident_id in rows:
            incident = await db.get(Incident, incident_id)
            kind = NotificationType.SLA_AT_RISK if at_risk else NotificationType.SLA_BREACHED
            sent += await notify(
                db,
                incident,
                [incident.assigned_to, *admins],
                kind,
                f"{incident.number} {label}: {incident.title}",
            )
    await db.commit()
    return sent


async def cleanup_uploads(db: AsyncSession, storage: Any | None) -> int:
    """Delete pending attachments older than one hour, and their objects if any."""
    # The status check is part of the DELETE, so a row that finalize accepted
    # (it holds the row lock) is skipped; objects go only after the commit.
    keys = (
        await db.scalars(
            delete(Attachment)
            .where(
                Attachment.status == AttachmentStatus.PENDING,
                Attachment.created_at < func.now() - timedelta(hours=1),
            )
            .returning(Attachment.s3_key)
        )
    ).all()
    await db.commit()
    for key in keys:
        if storage is not None:
            try:
                await asyncio.to_thread(storage.delete, key)
            except Exception:  # noqa: BLE001 - a missing object must not stop cleanup
                pass
    return len(keys)


async def run_jobs(
    sessions: SessionFactory, storage: Any | None = None, dry_run: bool = False
) -> dict[str, Any]:
    """Run all steps under a session-level advisory lock so runs never overlap."""
    async with sessions() as guard:
        acquired = await guard.scalar(text(f"SELECT pg_try_advisory_lock({JOB_LOCK})"))
        if not acquired:
            return {"status": "skipped", "reason": "another run holds the lock"}
        try:
            if dry_run:
                await guard.execute(text("SELECT 1"))
                return {"status": "ok", "dry_run": True}
            closed = await auto_close(sessions)
            async with sessions() as db:
                alerts = await sla_alerts(db)
            async with sessions() as db:
                cleaned = await cleanup_uploads(db, storage)
            return {"status": "ok", "auto_closed": closed, "sla_alerts": alerts, "uploads": cleaned}
        finally:
            await guard.execute(text(f"SELECT pg_advisory_unlock({JOB_LOCK})"))
            await guard.commit()
