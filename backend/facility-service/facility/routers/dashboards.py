"""Live PostgreSQL aggregates restricted to each dashboard persona."""

from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Float, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from facility import metrics
from facility.database import get_db
from facility.dependencies import current_user, require_admin
from facility.enums import (
    AUTO_CLOSE_HOURS,
    FINAL_STATUSES,
    NON_FINAL_STATUSES,
    RESOLVED_STATUSES,
    TEAM_PERIOD_DAYS,
    AssigneeFilter,
    EngineerAvailability,
    EscalationState,
    IncidentEventType,
    IncidentSort,
    IncidentStatus,
    NoteVisibility,
    SlaState,
    SortDirection,
    TeamPeriod,
    UserRole,
)
from facility.errors import fail
from facility.models import (
    Building,
    Category,
    EngineerProfile,
    EscalationRequest,
    Floor,
    Incident,
    IncidentEvent,
    IncidentFollower,
    IncidentNote,
    Seat,
    Team,
    User,
    utcnow,
)
from facility.permissions import is_admin
from facility.sla import sla_state_sql

router = APIRouter(prefix="/dashboard", tags=["dashboards"])


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
        .where(IncidentNote.visibility == NoteVisibility.PUBLIC, IncidentNote.hidden_at.is_(None))
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
    """Count the caller's created and followed tickets; show actionable updates."""
    created = Incident.created_by == user.id
    owned = or_(
        created,
        Incident.id.in_(
            select(IncidentFollower.incident_id).where(IncidentFollower.user_id == user.id)
        ),
    )
    counts = await grouped(db, Incident.status, owned)
    for count in counts:
        count["href"] = link(status=count["value"])
    now = utcnow()
    awaiting = [
        {
            **dict(row),
            "auto_close_at": close_at,
            "seconds_until_auto_close": max(0, int((close_at - now).total_seconds()))
            if close_at
            else None,
        }
        for row in (
            await db.execute(
                select(Incident.id, Incident.number, Incident.title, Incident.resolved_at)
                .where(created, Incident.status == IncidentStatus.RESOLVED)
                .order_by(Incident.resolved_at, Incident.id)
            )
        )
        .mappings()
        .all()
        for close_at in [
            row.resolved_at + timedelta(hours=AUTO_CLOSE_HOURS) if row.resolved_at else None
        ]
    ]
    recent = await db.execute(
        select(
            IncidentNote.id, IncidentNote.incident_id, IncidentNote.body, IncidentNote.created_at
        )
        .join(Incident, Incident.id == IncidentNote.incident_id)
        .where(
            owned,
            IncidentNote.visibility == NoteVisibility.PUBLIC,
            IncidentNote.hidden_at.is_(None),
        )
        .order_by(IncidentNote.created_at.desc(), IncidentNote.id)
        .limit(10)
    )
    return {
        "status_counts": counts,
        "awaiting_confirmation": awaiting,
        "recent_public_notes": [dict(row) for row in recent.mappings()],
        "communication": await communication(db, owned, Incident.status.not_in(FINAL_STATUSES)),
    }


@router.get("/engineer")
async def engineer_dashboard(
    week_start: datetime | None = None,
    week_end: datetime | None = None,
    tz: str | None = Query(default=None, max_length=64),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """Aggregate current assignment and resolution-event attributed work."""
    if UserRole.ENGINEER not in user.roles:
        raise HTTPException(403, "Engineer role required")
    profile = await db.scalar(
        select(EngineerProfile).where(
            EngineerProfile.user_id == user.id, EngineerProfile.is_active.is_(True)
        )
    )
    if profile is None:
        raise HTTPException(403, "Active engineer profile required")
    # "This week" is the viewer's local calendar week, Monday 00:00 (NFR-10).
    local_now = utcnow().astimezone(metrics.zone(tz))
    local_monday = (local_now - timedelta(days=local_now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    start = week_start or local_monday.astimezone(UTC)
    end = week_end or (start + timedelta(days=7))
    if start.tzinfo is None or end.tzinfo is None or end <= start:
        raise HTTPException(422, "Supply timezone-aware week_start and week_end")
    assigned = Incident.assigned_to == user.id
    counts = await grouped(db, Incident.status, assigned, Incident.status.not_in(FINAL_STATUSES))
    latest = (
        select(IncidentEvent.incident_id, func.max(IncidentEvent.created_at).label("at"))
        .where(
            IncidentEvent.type == IncidentEventType.STATUS_CHANGED,
            IncidentEvent.payload["to"].astext == IncidentStatus.RESOLVED,
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
                    Incident.status.in_(RESOLVED_STATUSES),
                    Incident.resolved_at >= start,
                    Incident.resolved_at < end,
                    IncidentEvent.type == IncidentEventType.STATUS_CHANGED,
                    IncidentEvent.payload["to"].astext == IncidentStatus.RESOLVED,
                    IncidentEvent.payload["assigned_to"].astext == str(user.id),
                )
            )
        )
        .mappings()
        .one()
    )
    for count in counts:
        count["href"] = link(assigned_to=user.id, status=count["value"])
    state = sla_state_sql()
    sla_rows = dict(
        (
            await db.execute(
                select(state, func.count(Incident.id))
                .where(assigned, Incident.status.not_in(FINAL_STATUSES))
                .group_by(state)
            )
        ).all()
    )
    sla_health = [
        {
            "value": value,
            "count": sla_rows.get(value.value, 0),
            "href": link(
                assigned_to=user.id, status=NON_FINAL_STATUSES, sla_state=value.value
            ),
        }
        for value in (SlaState.ON_TRACK, SlaState.AT_RISK, SlaState.BREACHED)
    ]
    return {
        "availability": profile.availability,
        "status_counts": counts,
        "sla_health": sla_health,
        "queue_href": link(
            assigned_to=user.id,
            status=NON_FINAL_STATUSES,
            sort=IncidentSort.SLA_DUE,
            direction=SortDirection.ASC,
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
    # created_to is exclusive, matching the cohort's `< end` (date_to is inclusive).
    filters = {
        "created_from": start.isoformat(),
        "created_to": end.isoformat(),
        "building_id": building_id,
    }
    location = [Incident.building_id == building_id] if building_id else []
    cohort.extend(location)
    # Current-state widgets count every non-final ticket now; the range applies to
    # flow widgets only (N17).
    nonfinal = [*location, Incident.status.not_in(FINAL_STATUSES)]
    statuses = await grouped(db, Incident.status, *nonfinal)
    priorities = await grouped(db, Incident.priority, *nonfinal)
    current_filters = {"building_id": building_id}
    for item in statuses:
        item["href"] = link(**current_filters, status=item["value"])
    for item in priorities:
        item["href"] = link(
            **current_filters, priority=item["value"], status=NON_FINAL_STATUSES
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
                        func.percentile_cont(0.5)
                        .within_group(func.extract("epoch", column - Incident.created_at))
                        .label("median_seconds"),
                    ).where(*cohort, column.is_not(None))
                )
            )
            .mappings()
            .one()
        )
        lifecycle[label] = {
            **dict(result),
            "median_seconds": float(result["median_seconds"])
            if result["median_seconds"] is not None
            else None,
            "href": link(**filters),
        }
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
        # Most-reported category per location in the same period (ties: lowest category id).
        top: dict = {}
        if rows:
            ranked = await db.execute(
                select(column.label("id"), Incident.category_id, func.count().label("count"))
                .where(*cohort, column.in_([row.id for row in rows]))
                .group_by(column, Incident.category_id)
                .order_by(column, func.count().desc(), Incident.category_id)
            )
            for entry in ranked.mappings():
                top.setdefault(entry["id"], entry)
        filter_name = {"buildings": "building_id", "floors": "floor_id", "seats": "seat_id"}[label]
        hotspots[label] = [
            {
                **dict(row),
                "top_category_id": top[row.id]["category_id"] if row.id in top else None,
                "top_category_count": top[row.id]["count"] if row.id in top else 0,
                "href": link(**{**filters, filter_name: row.id}),
            }
            for row in rows
        ]
    exact_location = case(
        (Incident.seat_id.is_not(None), Incident.seat_id),
        (Incident.floor_id.is_not(None), Incident.floor_id),
        else_=Incident.building_id,
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
                .where(*location, EscalationRequest.state == EscalationState.PENDING)
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
                .where(*location, Incident.status == IncidentStatus.BLOCKED)
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
                    **current_filters,
                    assigned_to=row.assigned_to or AssigneeFilter.UNASSIGNED,
                    status=row.status,
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
        "communication_health": await metrics.communication_health(
            db, start, end, building_id
        ),
        "sla_compliance": await metrics.sla_compliance(db, cohort),
        "company": await metrics.company_view(db, start, end, building_id, link),
        "routing_health": await metrics.routing_health(db, start, end, building_id, link),
        "triage_href": link(
            status=IncidentStatus.OPEN,
            assigned_to=AssigneeFilter.UNASSIGNED,
            sort=IncidentSort.PRIORITY,
        ),
    }


@router.get("/team")
async def team_dashboard(
    period: TeamPeriod = TeamPeriod.WEEK,
    team_id: UUID | None = None,
    tz: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """Team lead summary (FR-14.2 to 14.6): totals vs previous period, per engineer, charts."""
    zone = metrics.zone(tz)
    if team_id is None:
        team = await db.scalar(select(Team).where(Team.lead_user_id == user.id))
        if team is None:
            raise fail(403, "forbidden", "Team lead or administrator access required")
    else:
        team = await db.get(Team, team_id)
        if team is None or not (is_admin(user) or team.lead_user_id == user.id):
            raise fail(404, "not_found", "Team not found")
    if not is_admin(user) and (team.lead_user_id != user.id or UserRole.LEAD not in user.roles):
        raise fail(403, "forbidden", "Team lead or administrator access required")
    days = TEAM_PERIOD_DAYS[period]
    end = utcnow()
    start = end - timedelta(days=days)
    previous_start = start - timedelta(days=days)
    members = (
        await db.execute(
            select(User.id, User.full_name, EngineerProfile.availability)
            .join(EngineerProfile, EngineerProfile.user_id == User.id)
            .where(
                EngineerProfile.team_id == team.id,
                EngineerProfile.is_active.is_(True),
                User.is_active.is_(True),
            )
            .order_by(User.full_name, User.id)
        )
    ).all()
    member_ids = [row.id for row in members]
    member_text = [str(item) for item in member_ids]
    resolver = metrics.latest_resolver()

    async def window(lo, hi) -> dict:
        assigned = await db.scalar(
            select(func.count(IncidentEvent.id)).where(
                IncidentEvent.type == IncidentEventType.ASSIGNED,
                IncidentEvent.payload["assignee"].astext.in_(member_text),
                IncidentEvent.created_at >= lo,
                IncidentEvent.created_at < hi,
            )
        )
        stats = await metrics.resolution_stats(
            db,
            Incident.resolved_at >= lo,
            Incident.resolved_at < hi,
            resolver.c.resolver.in_(member_text),
            join_resolver=resolver,
        )
        reopened = await db.scalar(
            select(func.count(IncidentEvent.id))
            .join(Incident, Incident.id == IncidentEvent.incident_id)
            .where(
                Incident.assigned_to.in_(member_ids),
                IncidentEvent.type == IncidentEventType.STATUS_CHANGED,
                IncidentEvent.payload["from"].astext == IncidentStatus.RESOLVED,
                IncidentEvent.payload["to"].astext == IncidentStatus.IN_PROGRESS,
                IncidentEvent.created_at >= lo,
                IncidentEvent.created_at < hi,
            )
        )
        closed = await db.scalar(
            select(func.count(Incident.id)).where(
                Incident.assigned_to.in_(member_ids),
                Incident.closed_at >= lo,
                Incident.closed_at < hi,
            )
        )
        returned = await db.scalar(
            select(func.count(IncidentEvent.id)).where(
                IncidentEvent.type == IncidentEventType.UNASSIGNED,
                IncidentEvent.payload["via"].astext == "returned_to_triage",
                IncidentEvent.payload["previous_assignee"].astext.in_(member_text),
                IncidentEvent.created_at >= lo,
                IncidentEvent.created_at < hi,
            )
        )
        ack = await db.scalar(
            select(
                func.avg(func.extract("epoch", Incident.acknowledged_at - Incident.created_at))
                .cast(Float)
            ).where(
                Incident.assigned_to.in_(member_ids),
                Incident.acknowledged_at >= lo,
                Incident.acknowledged_at < hi,
            )
        )
        return {
            "assigned": assigned,
            "resolved": stats["count"],
            "reopened": reopened,
            "closed": closed,
            "returned_to_triage": returned,
            "avg_ack_seconds": ack,
            "avg_resolve_seconds": stats["mean_seconds"],
            "on_time_ratio": stats["on_time_ratio"],
        }

    current = await window(start, end)
    before = await window(previous_start, start)
    totals = {key: metrics.delta(value, before[key]) for key, value in current.items()}
    for key, statuses in [
        ("open_now", NON_FINAL_STATUSES),
        ("blocked_now", (IncidentStatus.BLOCKED,)),
    ]:
        totals[key] = {
            "value": await db.scalar(
                select(func.count(Incident.id)).where(
                    Incident.assigned_to.in_(member_ids), Incident.status.in_(statuses)
                )
            ),
            "previous": None,
            "change": None,
            "href": link(team_id=team.id, status=statuses),
        }
    engineers = []
    for row in members:
        stats = await metrics.resolution_stats(
            db,
            Incident.resolved_at >= start,
            Incident.resolved_at < end,
            resolver.c.resolver == str(row.id),
            join_resolver=resolver,
        )
        engineers.append(
            {
                "user_id": row.id,
                "full_name": row.full_name,
                "availability": row.availability,
                "resolved": stats["count"],
                "avg_resolve_seconds": stats["mean_seconds"],
                "on_time_ratio": stats["on_time_ratio"],
                "open_now": await db.scalar(
                    select(func.count(Incident.id)).where(
                        Incident.assigned_to == row.id,
                        Incident.status.in_(NON_FINAL_STATUSES),
                    )
                ),
                "reopens": await db.scalar(
                    select(func.count(IncidentEvent.id))
                    .join(Incident, Incident.id == IncidentEvent.incident_id)
                    .where(
                        Incident.assigned_to == row.id,
                        IncidentEvent.type == IncidentEventType.STATUS_CHANGED,
                        IncidentEvent.payload["from"].astext == IncidentStatus.RESOLVED,
                        IncidentEvent.payload["to"].astext == IncidentStatus.IN_PROGRESS,
                        IncidentEvent.created_at >= start,
                        IncidentEvent.created_at < end,
                    )
                ),
                "href": link(
                    # The count credits the latest resolver, not the current assignee.
                    resolved_by=row.id,
                    resolved_from=start.isoformat(),
                    resolved_to=end.isoformat(),
                ),
            }
        )
    resolved_rows = (
        await db.execute(
            select(Incident.resolved_at, func.coalesce(Category.parent_id, Category.id))
            .join(resolver, resolver.c.incident_id == Incident.id)
            .join(Category, Category.id == Incident.category_id)
            .where(
                Incident.resolved_at >= start,
                Incident.resolved_at < end,
                resolver.c.resolver.in_(member_text),
            )
        )
    ).all()
    by_category: dict = {}
    for _, top_id in resolved_rows:
        by_category[top_id] = by_category.get(top_id, 0) + 1
    names = dict(
        (await db.execute(select(Category.id, Category.name).where(
            Category.id.in_(list(by_category) or [None])
        ))).all()
    )
    on_duty = sum(1 for row in members if row.availability != EngineerAvailability.OFF_DUTY)
    return {
        "team": {"id": team.id, "name": team.name, "members_on_duty": on_duty},
        "period": {
            "name": period,
            "from": start,
            "to": end,
            "previous_from": previous_start,
            "tz": zone.key,
        },
        "totals": totals,
        "engineers": engineers,
        "charts": {
            "resolved_over_time": metrics.buckets(
                [row[0] for row in resolved_rows], start, end, zone, period != TeamPeriod.WEEK
            ),
            "resolved_by_category": [
                {"category_id": key, "name": names.get(key), "count": value}
                for key, value in sorted(by_category.items(), key=lambda item: -item[1])
            ],
        },
    }
