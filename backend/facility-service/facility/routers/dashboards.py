"""Live PostgreSQL aggregates restricted to each dashboard persona."""

from datetime import datetime, timedelta
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Float, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from facility.database import get_db
from facility.dependencies import current_user, require_admin
from facility.models import (
    Building,
    Category,
    EngineerProfile,
    EscalationRequest,
    Floor,
    Incident,
    IncidentEvent,
    IncidentNote,
    Seat,
    User,
    utcnow,
)

router = APIRouter(prefix="/dashboard", tags=["dashboards"])
FINAL = ["Closed", "Cancelled"]


def link(**filters: object) -> str:
    """Return an inspectable list API link with matching filter parameters."""
    return "/api/v1/incidents?" + urlencode(
        {key: value for key, value in filters.items() if value is not None}, doseq=True
    )


async def grouped(db: AsyncSession, column: object, *conditions: object) -> list[dict]:
    """Calculate a count in SQL for each status/priority/assignee group."""
    rows = (
        await db.execute(
            select(column, func.count(Incident.id))
            .where(*conditions)
            .group_by(column)
            .order_by(column)
        )
    ).all()
    return [{"value": value, "count": count} for value, count in rows]


async def communication(db: AsyncSession, *conditions: object) -> list[dict]:
    """Show last visible public note without exposing any internal note contents."""
    last = (
        select(
            IncidentNote.incident_id, func.max(IncidentNote.created_at).label("last_public_update")
        )
        .where(IncidentNote.visibility == "public", IncidentNote.hidden_at.is_(None))
        .group_by(IncidentNote.incident_id)
        .subquery()
    )
    rows = (
        await db.execute(
            select(Incident.id, Incident.number, Incident.title, last.c.last_public_update)
            .outerjoin(last, last.c.incident_id == Incident.id)
            .where(*conditions)
            .order_by(last.c.last_public_update.asc().nullsfirst(), Incident.id)
        )
    ).all()
    return [
        {
            "id": row.id,
            "number": row.number,
            "title": row.title,
            "last_public_update": row.last_public_update,
            "href": f"/api/v1/incidents/{row.id}",
        }
        for row in rows
    ]


@router.get("/employee")
async def employee_dashboard(
    db: AsyncSession = Depends(get_db), user: User = Depends(current_user)
) -> dict:
    """Count the creator's tickets and show actionable public updates."""
    owned = Incident.created_by == user.id
    counts = await grouped(db, Incident.status, owned)
    for count in counts:
        count["href"] = link(status=count["value"], scope="mine")
    awaiting = (
        (
            await db.execute(
                select(Incident.id, Incident.number, Incident.title, Incident.resolved_at)
                .where(owned, Incident.status == "Resolved")
                .order_by(Incident.resolved_at, Incident.id)
            )
        )
        .mappings()
        .all()
    )
    recent = await db.execute(
        select(
            IncidentNote.id, IncidentNote.incident_id, IncidentNote.body, IncidentNote.created_at
        )
        .join(Incident, Incident.id == IncidentNote.incident_id)
        .where(owned, IncidentNote.visibility == "public", IncidentNote.hidden_at.is_(None))
        .order_by(IncidentNote.created_at.desc(), IncidentNote.id)
        .limit(10)
    )
    return {
        "status_counts": counts,
        "awaiting_confirmation": [dict(row) for row in awaiting],
        "recent_public_notes": [dict(row) for row in recent.mappings()],
        "communication": await communication(db, owned, Incident.status.not_in(FINAL)),
    }


@router.get("/engineer")
async def engineer_dashboard(
    week_start: datetime | None = None,
    week_end: datetime | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """Aggregate current assignment and resolution-event attributed work."""
    if "engineer" not in user.roles:
        raise HTTPException(403, "Engineer role required")
    profile = await db.scalar(
        select(EngineerProfile).where(
            EngineerProfile.user_id == user.id, EngineerProfile.is_active.is_(True)
        )
    )
    if profile is None:
        raise HTTPException(403, "Active engineer profile required")
    now = utcnow()
    start = week_start or (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    end = week_end or (start + timedelta(days=7))
    if start.tzinfo is None or end.tzinfo is None or end <= start:
        raise HTTPException(422, "Supply timezone-aware week_start and week_end")
    assigned = Incident.assigned_to == user.id
    counts = await grouped(db, Incident.status, assigned, Incident.status.not_in(FINAL))
    latest = (
        select(IncidentEvent.incident_id, func.max(IncidentEvent.created_at).label("at"))
        .where(
            IncidentEvent.type == "status_changed", IncidentEvent.payload["to"].astext == "Resolved"
        )
        .group_by(IncidentEvent.incident_id)
        .subquery()
    )
    # The event, rather than today's assignee, attributes completed work.
    completed = (
        (
            await db.execute(
                select(
                    func.count(Incident.id).label("count"),
                    func.avg(func.extract("epoch", Incident.resolved_at - Incident.created_at))
                    .cast(Float)
                    .label("mean_seconds"),
                )
                .join(latest, latest.c.incident_id == Incident.id)
                .join(
                    IncidentEvent,
                    (IncidentEvent.incident_id == Incident.id)
                    & (IncidentEvent.created_at == latest.c.at),
                )
                .where(
                    Incident.status.in_(["Resolved", "Closed"]),
                    Incident.resolved_at >= start,
                    Incident.resolved_at < end,
                    IncidentEvent.type == "status_changed",
                    IncidentEvent.payload["to"].astext == "Resolved",
                    IncidentEvent.payload["assigned_to"].astext == str(user.id),
                )
            )
        )
        .mappings()
        .one()
    )
    for count in counts:
        count["href"] = link(assigned_to=user.id, status=count["value"])
    return {
        "availability": profile.availability,
        "status_counts": counts,
        "queue_href": link(
            assigned_to=user.id,
            status=["Open", "In Progress", "Blocked", "Resolved"],
            sort="created",
            direction="asc",
        ),
        "resolved_this_week": {**dict(completed), "from": start, "to": end},
    }


@router.get("/admin")
async def admin_dashboard(
    date_from: datetime | None = Query(None, alias="from"),
    date_to: datetime | None = Query(None, alias="to"),
    building_id: UUID | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
) -> dict:
    """Answer the seven workshop questions from a single explicit SQL cohort."""
    end = date_to or utcnow()
    start = date_from or end - timedelta(days=30)
    if start.tzinfo is None or end.tzinfo is None or start >= end:
        raise HTTPException(422, "Use timezone-aware from < to")
    cohort = [Incident.created_at >= start, Incident.created_at < end]
    filters = {
        "date_from": start.isoformat(),
        "date_to": end.isoformat(),
        "building_id": building_id,
    }
    if building_id:
        cohort.append(Incident.building_id == building_id)
    nonfinal = [*cohort, Incident.status.not_in(FINAL)]
    statuses = await grouped(db, Incident.status, *nonfinal)
    priorities = await grouped(db, Incident.priority, *nonfinal)
    for item in statuses:
        item["href"] = link(**filters, status=item["value"])
    for item in priorities:
        item["href"] = link(
            **filters, priority=item["value"], status=["Open", "In Progress", "Blocked", "Resolved"]
        )
    workload_rows = (
        await db.execute(
            select(Incident.assigned_to, Incident.status, func.count().label("count"))
            .where(*nonfinal)
            .group_by(Incident.assigned_to, Incident.status)
            .order_by(Incident.assigned_to, Incident.status)
        )
    ).all()
    workforce = (
        (
            await db.execute(
                select(User.id, User.full_name, EngineerProfile.availability)
                .join(EngineerProfile, EngineerProfile.user_id == User.id)
                .where(User.is_active.is_(True), EngineerProfile.is_active.is_(True))
                .order_by(User.full_name, User.id)
            )
        )
        .mappings()
        .all()
    )
    availability_rows = (
        await db.execute(
            select(EngineerProfile.availability, func.count())
            .join(User, User.id == EngineerProfile.user_id)
            .where(User.is_active.is_(True), EngineerProfile.is_active.is_(True))
            .group_by(EngineerProfile.availability)
        )
    ).all()
    lifecycle = {}
    for label, column in [
        ("acknowledged", Incident.acknowledged_at),
        ("assigned", Incident.assigned_at),
        ("resolved", Incident.resolved_at),
    ]:
        result = (
            (
                await db.execute(
                    select(
                        func.count().label("sample_count"),
                        func.avg(func.extract("epoch", column - Incident.created_at))
                        .cast(Float)
                        .label("mean_seconds"),
                    ).where(*cohort, column.is_not(None))
                )
            )
            .mappings()
            .one()
        )
        lifecycle[label] = {**dict(result), "href": link(**filters)}
    hotspots = {}
    for label, model, column in [
        ("buildings", Building, Incident.building_id),
        ("floors", Floor, Incident.floor_id),
        ("seats", Seat, Incident.seat_id),
    ]:
        name_column = model.code if model is Seat else model.name
        rows = (
            (
                await db.execute(
                    select(
                        column.label("id"), name_column.label("name"), func.count().label("count")
                    )
                    .join(model, model.id == column)
                    .where(*cohort)
                    .group_by(column, name_column)
                    .order_by(func.count().desc(), column)
                    .limit(10)
                )
            )
            .mappings()
            .all()
        )
        filter_name = {"buildings": "building_id", "floors": "floor_id", "seats": "seat_id"}[label]
        hotspots[label] = [
            {**dict(row), "href": link(**{**filters, filter_name: row.id})} for row in rows
        ]
    exact_location = case(
        (Incident.seat_id.is_not(None), Incident.seat_id), else_=Incident.floor_id
    )
    recurring_conditions = [
        Incident.created_at >= end - timedelta(days=30),
        Incident.created_at < end,
    ]
    if building_id:
        recurring_conditions.append(Incident.building_id == building_id)
    recurring = await db.execute(
        select(
            exact_location.label("location_id"), Incident.category_id, func.count().label("count")
        )
        .where(*recurring_conditions)
        .group_by(exact_location, Incident.category_id)
        .having(func.count() >= 3)
        .order_by(func.count().desc())
    )
    parent = aliased(Category)
    root_id = func.coalesce(Category.parent_id, Category.id)
    category_rows = (
        (
            await db.execute(
                select(root_id.label("id"), parent.name, func.count().label("count"))
                .select_from(Incident)
                .join(Category, Category.id == Incident.category_id)
                .join(parent, parent.id == root_id)
                .where(*cohort)
                .group_by(root_id, parent.name)
                .order_by(func.count().desc(), root_id)
            )
        )
        .mappings()
        .all()
    )
    pending = (
        (
            await db.execute(
                select(
                    Incident.id,
                    Incident.number,
                    EscalationRequest.reason,
                    EscalationRequest.requested_at,
                )
                .join(EscalationRequest, EscalationRequest.incident_id == Incident.id)
                .where(*cohort, EscalationRequest.state == "Pending")
                .order_by(EscalationRequest.requested_at)
            )
        )
        .mappings()
        .all()
    )
    blocked = (
        (
            await db.execute(
                select(
                    Incident.id,
                    Incident.number,
                    Incident.blocked_reason,
                    Incident.blocked_since,
                    Incident.assigned_to,
                )
                .where(*cohort, Incident.status == "Blocked")
                .order_by(Incident.blocked_since)
            )
        )
        .mappings()
        .all()
    )
    return {
        "cohort": {"from": start, "to": end, "building_id": building_id},
        "status_counts": statuses,
        "priority_counts": priorities,
        "workload": [
            {
                "assigned_to": row.assigned_to,
                "status": row.status,
                "count": row.count,
                "href": link(
                    **filters, assigned_to=row.assigned_to or "unassigned", status=row.status
                ),
            }
            for row in workload_rows
        ],
        "engineers": [dict(row) for row in workforce],
        "availability_counts": [
            {"value": value, "count": count} for value, count in availability_rows
        ],
        "lifecycle": lifecycle,
        "hotspots": hotspots,
        "recurring": [dict(row) for row in recurring.mappings()],
        "categories": [
            {**dict(row), "href": link(**filters, category_id=row.id)} for row in category_rows
        ],
        "pending_escalations": [dict(row) for row in pending],
        "blocked": [dict(row) for row in blocked],
        "communication": await communication(db, *nonfinal),
    }
