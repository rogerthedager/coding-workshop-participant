"""Shared metric queries for the admin company view and the team lead summary (FR-11, FR-14).

On-time ratio has one definition everywhere: tickets resolved in the range with
resolved_at <= resolve_due_at, divided by tickets resolved in the range. Resolution
is credited to the assignee recorded on the latest resolve transition.
"""

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException
from sqlalchemy import Float, String, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from facility.enums import (
    FINAL_STATUSES,
    IncidentEventType,
    IncidentStatus,
    NoteVisibility,
    TriageFilter,
    TriageReason,
)
from facility.models import (
    Building,
    Category,
    EngineerProfile,
    Floor,
    Incident,
    IncidentEvent,
    IncidentNote,
    Seat,
    Team,
)

RESOLVE_SECONDS = func.extract("epoch", Incident.resolved_at - Incident.created_at)


def zone(name: str | None) -> ZoneInfo:
    """Validate an IANA time zone name; UTC when omitted."""
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError, OSError):
        # OSError: over-long names reach the filesystem lookup.
        raise HTTPException(422, "Unknown time zone") from None


def latest_resolver():
    """Lateral subquery (incident_id, resolver): who the ticket's latest resolve credits.

    Correlated to Incident and limited to one row, so each ticket is a single probe of
    the ix_incident_events_resolved partial index instead of a ranking of all events.
    """
    return (
        select(
            IncidentEvent.incident_id.label("incident_id"),
            IncidentEvent.payload["assigned_to"].astext.label("resolver"),
        )
        .where(
            IncidentEvent.incident_id == Incident.id,
            IncidentEvent.type == IncidentEventType.STATUS_CHANGED,
            IncidentEvent.payload["to"].astext == IncidentStatus.RESOLVED,
        )
        .order_by(IncidentEvent.created_at.desc(), IncidentEvent.id.desc())
        .limit(1)
        .lateral()
    )


def ratio(numerator: float | int | None, denominator: float | int | None) -> float | None:
    """Return a ratio, or None when the denominator is zero."""
    if not denominator:
        return None
    return round(float(numerator or 0) / float(denominator), 4)


def delta(current: float | int | None, previous: float | int | None) -> dict[str, Any]:
    """A value with its previous-period value and the change between them."""
    change = None
    if current is not None and previous is not None:
        change = round(current - previous, 4)
    return {"value": current, "previous": previous, "change": change}


def resolved_in(start: datetime, end: datetime) -> list:
    return [Incident.resolved_at >= start, Incident.resolved_at < end]


def _resolution_columns() -> list:
    return [
        func.count(Incident.id).label("count"),
        func.avg(RESOLVE_SECONDS).cast(Float).label("mean_seconds"),
        func.percentile_cont(0.5).within_group(RESOLVE_SECONDS).label("median_seconds"),
        func.count(Incident.id)
        .filter(Incident.resolved_at <= Incident.resolve_due_at)
        .label("on_time"),
    ]


def _stats(row) -> dict:
    return {
        "count": row["count"],
        "mean_seconds": row["mean_seconds"],
        "median_seconds": (
            float(row["median_seconds"]) if row["median_seconds"] is not None else None
        ),
        "on_time_ratio": ratio(row["on_time"], row["count"]),
    }


async def resolution_stats(db: AsyncSession, *conditions: Any, join_resolver=None) -> dict:
    """Count, mean, median resolve seconds and on-time ratio for resolved tickets."""
    statement = select(*_resolution_columns()).where(Incident.resolved_at.is_not(None), *conditions)
    if join_resolver is not None:
        statement = statement.join(join_resolver, join_resolver.c.incident_id == Incident.id)
    return _stats((await db.execute(statement)).mappings().one())


async def company_view(
    db: AsyncSession,
    start: datetime,
    end: datetime,
    building_id: UUID | None,
    link,
) -> dict[str, Any]:
    """Company-wide widgets for the admin dashboard."""
    length = end - start
    previous_start = start - length
    where_building = [Incident.building_id == building_id] if building_id else []
    filters = {"building_id": building_id}

    async def count(*conditions: Any) -> int:
        return await db.scalar(
            select(func.count(Incident.id)).where(*where_building, *conditions)
        )

    created = await count(Incident.created_at >= start, Incident.created_at < end)
    created_before = await count(
        Incident.created_at >= previous_start, Incident.created_at < start
    )
    resolved = await count(*resolved_in(start, end))
    resolved_before = await count(*resolved_in(previous_start, start))
    open_now = await count(Incident.status.not_in(FINAL_STATUSES))
    totals = {
        "created": {
            **delta(created, created_before),
            "href": link(**filters, created_from=start.isoformat(), created_to=end.isoformat()),
        },
        "resolved": {
            **delta(resolved, resolved_before),
            "href": link(
                **filters, resolved_from=start.isoformat(), resolved_to=end.isoformat()
            ),
        },
        "open_now": {
            "value": open_now,
            "previous": None,
            "change": None,
            "href": link(
                **filters,
                status=[s.value for s in IncidentStatus if s not in FINAL_STATUSES],
            ),
        },
    }

    seats = (
        select(Floor.building_id, func.count(Seat.id).label("seats"))
        .join(Seat, Seat.floor_id == Floor.id)
        .where(Seat.is_active.is_(True), Floor.is_active.is_(True))
        .group_by(Floor.building_id)
        .subquery()
    )
    tickets = (
        select(Incident.building_id, func.count(Incident.id).label("tickets"))
        .where(Incident.created_at >= start, Incident.created_at < end)
        .group_by(Incident.building_id)
        .subquery()
    )
    rate_query = (
        select(
            Building.id,
            Building.name,
            func.coalesce(seats.c.seats, 0).label("seats"),
            func.coalesce(tickets.c.tickets, 0).label("tickets"),
        )
        .outerjoin(seats, seats.c.building_id == Building.id)
        .outerjoin(tickets, tickets.c.building_id == Building.id)
        .where(Building.is_active.is_(True))
        .order_by(Building.name, Building.id)
    )
    if building_id:
        rate_query = rate_query.where(Building.id == building_id)
    rates = [
        {
            "building_id": row.id,
            "name": row.name,
            "tickets": row.tickets,
            "active_seats": row.seats,
            "rate_per_100_seats": round(row.tickets / row.seats * 100, 2) if row.seats else None,
            "href": link(
                building_id=row.id, created_from=start.isoformat(), created_to=end.isoformat()
            ),
        }
        for row in (await db.execute(rate_query)).all()
    ]

    parent = aliased(Category)
    top_rows = (
        await db.execute(
            select(
                Category.id,
                Category.name,
                parent.name.label("parent_name"),
                func.count(Incident.id).label("count"),
            )
            .join(Category, Category.id == Incident.category_id)
            .outerjoin(parent, parent.id == Category.parent_id)
            .where(*where_building, Incident.created_at >= start, Incident.created_at < end)
            .group_by(Category.id, Category.name, parent.name)
            .order_by(func.count(Incident.id).desc(), Category.id)
            .limit(10)
        )
    ).all()
    top_problems = [
        {
            "category_id": row.id,
            "name": row.name,
            "parent_name": row.parent_name,
            "count": row.count,
            "share": ratio(row.count, created),
            "href": link(
                **filters,
                category_id=row.id,
                created_from=start.isoformat(),
                created_to=end.isoformat(),
            ),
        }
        for row in top_rows
    ]

    # One grouped query for every team (credited to the latest resolver's team).
    resolver = latest_resolver()
    by_team = {
        row["team_id"]: row
        for row in (
            await db.execute(
                select(EngineerProfile.team_id, *_resolution_columns())
                .select_from(Incident)
                .join(resolver, resolver.c.incident_id == Incident.id)
                .join(EngineerProfile, EngineerProfile.user_id.cast(String) == resolver.c.resolver)
                .where(
                    Incident.resolved_at.is_not(None),
                    EngineerProfile.team_id.is_not(None),
                    *where_building,
                    *resolved_in(start, end),
                )
                .group_by(EngineerProfile.team_id)
            )
        ).mappings()
    }
    empty = {"count": 0, "mean_seconds": None, "median_seconds": None, "on_time": 0}
    teams = (await db.scalars(select(Team).order_by(Team.name, Team.id))).all()
    time_by_team = [
        {"team_id": team.id, "name": team.name, **_stats(by_team.get(team.id, empty))}
        for team in teams
    ]

    building_rows = (
        await db.execute(
            select(
                Building.id,
                Building.name,
                func.count(Incident.id).label("resolved"),
                func.count(Incident.id)
                .filter(Incident.resolved_at <= Incident.resolve_due_at)
                .label("on_time"),
            )
            .join(Incident, Incident.building_id == Building.id)
            .where(*where_building, *resolved_in(start, end))
            .group_by(Building.id, Building.name)
            .order_by(Building.name)
        )
    ).all()
    on_time = {
        "by_team": [
            {
                "team_id": row["team_id"],
                "name": row["name"],
                "resolved": row["count"],
                "on_time_ratio": row["on_time_ratio"],
            }
            for row in time_by_team
        ],
        "by_building": [
            {
                "building_id": row.id,
                "name": row.name,
                "resolved": row.resolved,
                "on_time_ratio": ratio(row.on_time, row.resolved),
            }
            for row in building_rows
        ],
    }
    return {
        "totals": totals,
        "incident_rate_by_building": rates,
        "top_problems": top_problems,
        "time_by_team": time_by_team,
        "on_time_completion": on_time,
    }


async def sla_compliance(db: AsyncSession, cohort: list) -> list[dict[str, Any]]:
    """Per priority: resolved within the resolve due time ÷ resolved."""
    rows = (
        await db.execute(
            select(
                Incident.priority,
                func.count(Incident.id).label("resolved"),
                func.count(Incident.id)
                .filter(Incident.resolved_at <= Incident.resolve_due_at)
                .label("met"),
            )
            .where(*cohort, Incident.resolved_at.is_not(None))
            .group_by(Incident.priority)
            .order_by(Incident.priority)
        )
    ).all()
    return [
        {"priority": row.priority, "resolved": row.resolved, "compliance": ratio(row.met, row.resolved)}
        for row in rows
    ]


async def communication_health(
    db: AsyncSession, start: datetime, end: datetime, building_id: UUID | None
) -> dict[str, Any]:
    """(a) non-final tickets with a public update in 48 h; (b) public notes per resolved
    ticket; (c) reopen rate = rejects in range ÷ resolves in range (N16)."""
    where_building = [Incident.building_id == building_id] if building_id else []
    visible_public = and_(
        IncidentNote.incident_id == Incident.id,
        IncidentNote.visibility == NoteVisibility.PUBLIC,
        IncidentNote.hidden_at.is_(None),
    )
    nonfinal = await db.scalar(
        select(func.count(Incident.id)).where(
            *where_building, Incident.status.not_in(FINAL_STATUSES)
        )
    )
    recent = await db.scalar(
        select(func.count(Incident.id)).where(
            *where_building,
            Incident.status.not_in(FINAL_STATUSES),
            select(IncidentNote.id)
            .where(visible_public, IncidentNote.created_at >= func.now() - timedelta(hours=48))
            .exists(),
        )
    )
    resolved_ids = select(Incident.id).where(*where_building, *resolved_in(start, end))
    resolved_count = await db.scalar(select(func.count()).select_from(resolved_ids.subquery()))
    notes = await db.scalar(
        select(func.count(IncidentNote.id)).where(
            IncidentNote.incident_id.in_(resolved_ids),
            IncidentNote.visibility == NoteVisibility.PUBLIC,
            IncidentNote.hidden_at.is_(None),
        )
    )

    async def transitions(source: IncidentStatus, target: IncidentStatus) -> int:
        statement = (
            select(func.count(IncidentEvent.id))
            .join(Incident, Incident.id == IncidentEvent.incident_id)
            .where(
                *where_building,
                IncidentEvent.type == IncidentEventType.STATUS_CHANGED,
                IncidentEvent.payload["from"].astext == source,
                IncidentEvent.payload["to"].astext == target,
                IncidentEvent.created_at >= start,
                IncidentEvent.created_at < end,
            )
        )
        return await db.scalar(statement)

    rejects = await transitions(IncidentStatus.RESOLVED, IncidentStatus.IN_PROGRESS)
    resolves = await transitions(IncidentStatus.IN_PROGRESS, IncidentStatus.RESOLVED)
    return {
        "recent_public_update_share": ratio(recent, nonfinal),
        "public_notes_per_resolved": ratio(notes, resolved_count),
        "reopen_rate": ratio(rejects, resolves),
    }


async def routing_health(
    db: AsyncSession, start: datetime, end: datetime, building_id: UUID | None, link
) -> dict[str, Any]:
    """Auto-assignment share, returns per team, and the current triage queue."""
    where_building = [Incident.building_id == building_id] if building_id else []
    created = await db.scalar(
        select(func.count(Incident.id)).where(
            *where_building, Incident.created_at >= start, Incident.created_at < end
        )
    )
    auto = await db.scalar(
        select(func.count(func.distinct(IncidentEvent.incident_id)))
        .join(Incident, Incident.id == IncidentEvent.incident_id)
        .where(
            *where_building,
            Incident.created_at >= start,
            Incident.created_at < end,
            IncidentEvent.type == IncidentEventType.ASSIGNED,
            IncidentEvent.payload["via"].astext == "auto_route",
        )
    )
    returns = (
        await db.execute(
            select(
                Team.id, Team.name, func.count(func.distinct(Incident.id)).label("count")
            )
            .select_from(IncidentEvent)
            .join(Incident, Incident.id == IncidentEvent.incident_id)
            .join(Team, Team.id == Incident.routed_team_id)
            .where(
                *where_building,
                IncidentEvent.type == IncidentEventType.UNASSIGNED,
                IncidentEvent.payload["via"].astext == "returned_to_triage",
                IncidentEvent.created_at >= start,
                IncidentEvent.created_at < end,
            )
            .group_by(Team.id, Team.name)
            .order_by(func.count(func.distinct(Incident.id)).desc(), Team.name)
        )
    ).all()
    queue = dict(
        (
            await db.execute(
                select(Incident.triage_reason, func.count(Incident.id))
                .where(
                    *where_building,
                    Incident.status == IncidentStatus.OPEN,
                    Incident.assigned_to.is_(None),
                )
                .group_by(Incident.triage_reason)
            )
        ).all()
    )
    return {
        "auto_assigned_share": ratio(auto, created),
        "auto_assigned": auto,
        "created": created,
        "returned_by_team": [
            {
                "team_id": row.id,
                "name": row.name,
                "count": row.count,
                "href": link(
                    routed_team_id=row.id,
                    returned_from=start.isoformat(),
                    returned_to=end.isoformat(),
                    building_id=building_id,
                ),
            }
            for row in returns
        ],
        "triage_queue": [
            {
                "reason": reason.value if reason else None,
                "count": queue.get(reason, 0),
                "href": link(
                    status=IncidentStatus.OPEN,
                    assigned_to="unassigned",
                    triage_reason=reason.value if reason else TriageFilter.NONE.value,
                    building_id=building_id,
                ),
            }
            for reason in [*TriageReason, None]
            if queue.get(reason, 0)
        ],
    }


def buckets(
    times: list[datetime], start: datetime, end: datetime, tz: ZoneInfo, weekly: bool
) -> list[dict[str, Any]]:
    """Resolved counts per local day (or ISO week) with empty buckets as zero."""

    def key(moment: datetime):
        local = moment.astimezone(tz).date()
        return local - timedelta(days=local.weekday()) if weekly else local

    counts: dict = {}
    for moment in times:
        counts[key(moment)] = counts.get(key(moment), 0) + 1
    current = key(start)
    last = key(end - timedelta(microseconds=1))
    step = timedelta(days=7 if weekly else 1)
    result = []
    while current <= last:
        result.append({"period_start": current.isoformat(), "count": counts.get(current, 0)})
        current += step
    return result

