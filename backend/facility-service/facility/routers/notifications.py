"""The caller's own in-app notifications (FR-10)."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from facility.database import get_db
from facility.dependencies import current_user
from facility.errors import fail
from facility.models import Incident, Notification, User, utcnow

router = APIRouter(prefix="/notifications", tags=["notifications"])
Db = Annotated[AsyncSession, Depends(get_db)]
Actor = Annotated[User, Depends(current_user)]


def output(row: Notification, number: str | None) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "incident_id": str(row.incident_id),
        "incident_number": number,
        "type": row.type,
        "message": row.message,
        "read": row.read_at is not None,
        "read_at": row.read_at.isoformat() if row.read_at else None,
        "created_at": row.created_at.isoformat(),
        "href": f"/api/v1/incidents/{row.incident_id}",
    }


@router.get("")
async def list_notifications(
    db: Db,
    user: Actor,
    unread: bool = False,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
) -> dict[str, Any]:
    """Newest first."""
    conditions = [Notification.user_id == user.id]
    if unread:
        conditions.append(Notification.read_at.is_(None))
    total = await db.scalar(select(func.count(Notification.id)).where(*conditions))
    rows = (
        await db.execute(
            select(Notification, Incident.number)
            .join(Incident, Incident.id == Notification.incident_id)
            .where(*conditions)
            .order_by(Notification.created_at.desc(), Notification.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return {
        "items": [output(row, number) for row, number in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/unread-count")
async def unread_count(db: Db, user: Actor) -> dict[str, int]:
    """Polled every 30 seconds by the SPA."""
    count = await db.scalar(
        select(func.count(Notification.id)).where(
            Notification.user_id == user.id, Notification.read_at.is_(None)
        )
    )
    return {"unread": count or 0}


@router.post("/read-all", status_code=204)
async def read_all(db: Db, user: Actor) -> Response:
    await db.execute(
        update(Notification)
        .where(Notification.user_id == user.id, Notification.read_at.is_(None))
        .values(read_at=utcnow())
    )
    await db.commit()
    return Response(status_code=204)


@router.post("/{notification_id}/read")
async def mark_read(notification_id: UUID, db: Db, user: Actor) -> dict[str, Any]:
    row = await db.scalar(
        select(Notification).where(
            Notification.id == notification_id, Notification.user_id == user.id
        )
    )
    if row is None:
        raise fail(404, "not_found", "Notification not found")
    row.read_at = row.read_at or utcnow()
    number = await db.scalar(select(Incident.number).where(Incident.id == row.incident_id))
    await db.commit()
    return output(row, number)
