"""Duplicate suggestions, following, duplicate close, return to triage and suggestions.

Included before the incidents router so `/incidents/similar` is matched before
`/incidents/{incident_id}`.
"""

from datetime import timedelta
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..dependencies import current_user
from ..enums import (
    FINAL_STATUSES,
    CloseKind,
    IncidentEventType,
    IncidentStatus,
    TriageReason,
)
from ..errors import fail
from ..models import (
    Building,
    Category,
    Floor,
    Incident,
    IncidentFollower,
    User,
    utcnow,
)
from ..permissions import (
    check_match,
    event,
    is_admin,
    load_incident,
    relationship,
    require_nonfinal,
    require_permission,
    touch,
)
from ..routing import ranked_engineers, top_category
from ..workflow import close
from .incidents import _finish

router = APIRouter(tags=["incidents"])
Db = Annotated[AsyncSession, Depends(get_db)]
Actor = Annotated[User, Depends(current_user)]
Version = Annotated[str | None, Header(alias="If-Match")]
SIMILAR_DAYS = 7
SIMILAR_LIMIT = 5


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DuplicateMark(Payload):
    duplicate_of_id: UUID
    reason: str = Field(min_length=1, max_length=500)


class ReturnToTriage(Payload):
    reason: str = Field(min_length=10, max_length=500)


def _root_category_ids(top_id: UUID):
    return select(Category.id).where((Category.id == top_id) | (Category.parent_id == top_id))


def _similar_conditions(top_id: UUID, floor_id: UUID):
    return [
        Incident.floor_id == floor_id,
        Incident.category_id.in_(_root_category_ids(top_id)),
        Incident.status.not_in(FINAL_STATUSES),
        Incident.created_at >= utcnow() - timedelta(days=SIMILAR_DAYS),
    ]


@router.get("/incidents/similar")
async def similar_incidents(
    category_id: UUID, floor_id: UUID, db: Db, user: Actor
) -> dict[str, Any]:
    """Recent open tickets on the same floor and top-level category, as previews only."""
    top = await top_category(db, category_id)
    if top is None or await db.get(Floor, floor_id) is None:
        raise HTTPException(404, "Category or floor not found")
    rows = (
        await db.execute(
            select(
                Incident.id,
                Incident.number,
                Incident.title,
                Incident.status,
                Incident.building_id,
                Incident.floor_id,
                Incident.seat_id,
                Incident.created_at,
                Building.name.label("building_name"),
                Floor.name.label("floor_name"),
            )
            .join(Building, Building.id == Incident.building_id)
            .join(Floor, Floor.id == Incident.floor_id)
            .where(*_similar_conditions(top.id, floor_id))
            .order_by(Incident.created_at.desc(), Incident.id)
            .limit(SIMILAR_LIMIT)
        )
    ).mappings().all()
    now = utcnow()
    following = set(
        await db.scalars(
            select(IncidentFollower.incident_id).where(
                IncidentFollower.user_id == user.id,
                IncidentFollower.incident_id.in_([row.id for row in rows]),
            )
        )
    )
    return {
        "items": [
            {
                "id": str(row.id),
                "number": row.number,
                "title": row.title,
                "status": row.status,
                "location": {
                    "building_id": str(row.building_id),
                    "building_name": row.building_name,
                    "floor_id": str(row.floor_id),
                    "floor_name": row.floor_name,
                    "seat_id": str(row.seat_id) if row.seat_id else None,
                },
                "age_seconds": int((now - row.created_at).total_seconds()),
                "following": row.id in following,
            }
            for row in rows
        ],
        "total": len(rows),
    }


async def _follow_eligible(db: AsyncSession, user: User, incident_id: UUID) -> Incident:
    """Readable tickets, or ones meeting the duplicate-suggestion rule, may be followed."""
    incident = await db.scalar(
        select(Incident)
        .where(Incident.id == incident_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if incident is None:
        raise fail(404, "not_found", "Incident not found")
    rel = await relationship(db, user, incident)
    if rel.readable:
        return incident
    suggested = (
        incident.floor_id is not None
        and incident.status not in FINAL_STATUSES
        and incident.created_at >= utcnow() - timedelta(days=SIMILAR_DAYS)
    )
    if not suggested:
        raise fail(404, "not_found", "Incident not found")
    return incident


@router.post("/incidents/{incident_id}/follow", status_code=204)
async def follow(incident_id: UUID, db: Db, user: Actor) -> Response:
    """Follow a ticket for public updates; idempotent."""
    incident = await _follow_eligible(db, user, incident_id)
    require_nonfinal(incident)
    await db.execute(
        insert(IncidentFollower)
        .values(incident_id=incident.id, user_id=user.id, created_at=utcnow())
        .on_conflict_do_nothing()
    )
    await db.commit()
    return Response(status_code=204)


@router.delete("/incidents/{incident_id}/follow", status_code=204)
async def unfollow(incident_id: UUID, db: Db, user: Actor) -> Response:
    """Stop following; allowed in any state."""
    result = await db.execute(
        delete(IncidentFollower).where(
            IncidentFollower.incident_id == incident_id, IncidentFollower.user_id == user.id
        )
    )
    if not result.rowcount:
        raise fail(404, "not_found", "You are not following this incident")
    await db.commit()
    return Response(status_code=204)


@router.post("/incidents/{incident_id}/duplicate")
async def mark_duplicate(
    incident_id: UUID,
    payload: DuplicateMark,
    response: Response,
    db: Db,
    user: Actor,
    if_match: Version = None,
) -> dict[str, Any]:
    """Close a ticket as a duplicate; its creator follows the surviving ticket (FR-7.5)."""
    incident = await load_incident(db, user, incident_id, lock=True)
    require_permission(is_admin(user))
    require_nonfinal(incident)
    check_match(incident, if_match)
    target = await db.scalar(
        select(Incident).where(Incident.id == payload.duplicate_of_id).with_for_update(read=True)
    )
    if (
        target is None
        or target.id == incident.id
        or target.status == IncidentStatus.CANCELLED
        or target.close_kind == CloseKind.DUPLICATE
    ):
        raise fail(
            409,
            "invalid_duplicate_target",
            "Choose a different ticket that is not cancelled or itself a duplicate",
        )
    incident.duplicate_of_id = target.id
    event(
        db,
        incident,
        user,
        IncidentEventType.DUPLICATE_MARKED,
        {"duplicate_of_number": target.number, "reason": payload.reason},
    )
    await close(
        db,
        incident,
        user,
        CloseKind.DUPLICATE,
        {"duplicate_of_number": target.number},
    )
    await db.execute(
        insert(IncidentFollower)
        .values(incident_id=target.id, user_id=incident.created_by, created_at=utcnow())
        .on_conflict_do_nothing()
    )
    return await _finish(db, incident, user, response)


@router.post("/incidents/{incident_id}/return-to-triage")
async def return_to_triage(
    incident_id: UUID,
    payload: ReturnToTriage,
    response: Response,
    db: Db,
    user: Actor,
    if_match: Version = None,
) -> dict[str, Any]:
    """The assignee or their team lead sends an Open ticket back to admin triage."""
    incident = await load_incident(db, user, incident_id, lock=True)
    rel = await relationship(db, user, incident)
    require_permission(rel.assignee or rel.team_lead)
    check_match(incident, if_match)
    if incident.status != IncidentStatus.OPEN or incident.assigned_to is None:
        raise fail(409, "not_returnable", "Only Open, assigned tickets can be returned")
    previous = incident.assigned_to
    incident.assigned_to = None
    incident.triage_reason = TriageReason.RETURNED
    incident.route_return_count += 1
    touch(incident)
    event(
        db,
        incident,
        user,
        IncidentEventType.UNASSIGNED,
        {
            "previous_assignee": str(previous),
            "assignee": None,
            "reason": payload.reason,
            "via": "returned_to_triage",
        },
    )
    return await _finish(db, incident, user, response)


@router.get("/incidents/{incident_id}/engineer-suggestions")
async def engineer_suggestions(incident_id: UUID, db: Db, user: Actor) -> dict[str, Any]:
    """Every active engineer, ranked for this ticket (admin only)."""
    incident = await load_incident(db, user, incident_id)
    require_permission(is_admin(user))
    top = await top_category(db, incident.category_id)
    items = await ranked_engineers(db, top, incident.building_id)
    return {
        "items": items,
        "total": len(items),
        "top_category": {"id": str(top.id), "name": top.name} if top else None,
    }
