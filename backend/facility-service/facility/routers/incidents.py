"""Facility incidents, lifecycle, communication and escalation endpoints."""

import csv
import inspect
import io
import re
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlalchemy import case, exists, func, literal_column, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..dependencies import current_user
from ..enums import (
    BLOCKED_REASONS,
    FINAL_STATUSES,
    PRIORITY_RANK,
    VALID_TRANSITIONS,
    AssigneeFilter,
    BlockedReason,
    CloseKind,
    EngineerAvailability,
    EscalationFilter,
    EscalationState,
    IncidentEventType,
    IncidentPriority,
    IncidentScope,
    IncidentSort,
    IncidentStatus,
    NoteVisibility,
    SlaState,
    SortDirection,
    TriageFilter,
    TriageReason,
    UserRole,
)
from ..errors import fail
from ..models import (
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
from ..notifications import commit
from ..permissions import (
    Rel,
    can_staff,
    check_match,
    event,
    is_admin,
    load_incident,
    readable_clause,
    relationship,
    require_nonfinal,
    require_permission,
    touch,
)
from ..routing import route_new_incident
from ..sla import next_due_sql, sla_state, sla_state_sql
from ..workflow import supersede_pending

router = APIRouter(tags=["incidents"])
Db = Annotated[AsyncSession, Depends(get_db)]
Actor = Annotated[User, Depends(current_user)]
Version = Annotated[str | None, Header(alias="If-Match")]


class Payload(BaseModel):
    """Strict inputs prevent accidental privilege-bearing or unsupported fields."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class IncidentCreate(Payload):
    title: str = Field(min_length=5, max_length=120)
    description: str = Field(min_length=10, max_length=5000)
    category_id: UUID
    # Omit the whole location to use the reporter's default seat (FR-5.2).
    building_id: UUID | None = None
    floor_id: UUID | None = None
    seat_id: UUID | None = None
    requested_priority: IncidentPriority = IncidentPriority.MEDIUM


class IncidentPatch(Payload):
    title: str | None = Field(default=None, min_length=5, max_length=120)
    description: str | None = Field(default=None, min_length=10, max_length=5000)
    category_id: UUID | None = None
    building_id: UUID | None = None
    floor_id: UUID | None = None
    seat_id: UUID | None = None


class Assignment(Payload):
    assigned_to: UUID | None
    reason: str | None = Field(default=None, min_length=1, max_length=500)
    confirm_unavailable: StrictBool = False


class PriorityChange(Payload):
    priority: IncidentPriority
    reason: str = Field(min_length=1, max_length=500)


class Transition(Payload):
    status: IncidentStatus
    reason: str | None = Field(default=None, min_length=1, max_length=500)
    note: str | None = Field(default=None, min_length=1, max_length=5000)
    visibility: NoteVisibility = NoteVisibility.PUBLIC


class NoteCreate(Payload):
    body: str = Field(min_length=1, max_length=5000)
    visibility: NoteVisibility = NoteVisibility.PUBLIC


class NotePatch(Payload):
    body: str = Field(min_length=1, max_length=5000)


class Reason(Payload):
    reason: str = Field(min_length=1, max_length=500)


class EscalationCreate(Payload):
    reason: str = Field(min_length=10, max_length=500)


class EscalationDecision(Payload):
    approved: bool
    priority: IncidentPriority | None = None
    decision_reason: str | None = Field(default=None, min_length=1, max_length=500)
    action_plan: str | None = Field(default=None, min_length=1, max_length=5000)
    assigned_to: UUID | None = None
    confirm_unavailable: StrictBool = False


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _escalation_projection(entry: EscalationRequest | None) -> dict[str, Any] | None:
    if entry is None:
        return None
    return {
        "id": str(entry.id),
        "incident_id": str(entry.incident_id),
        "requester_id": str(entry.requester_id),
        "reason": entry.reason,
        "requested_at": _iso(entry.requested_at),
        "state": entry.state,
        "decided_by": str(entry.decided_by) if entry.decided_by else None,
        "decided_at": _iso(entry.decided_at),
        "decision_reason": entry.decision_reason,
        "action_plan": entry.action_plan,
    }


async def _latest_escalation(db: AsyncSession, incident_id: UUID) -> EscalationRequest | None:
    return await db.scalar(
        select(EscalationRequest)
        .where(EscalationRequest.incident_id == incident_id)
        .order_by(EscalationRequest.requested_at.desc(), EscalationRequest.id.desc())
        .limit(1)
    )


FOLLOWER_HIDDEN = {"description", "created_by", "requested_priority", "latest_escalation"}


async def people_names(db: AsyncSession, ids: Any, hide: UUID | None = None) -> dict[str, str]:
    """Display names for the users a response refers to (IDs alone mean nothing to
    non-admins). The identity hidden from this reader is left out."""
    wanted = {UUID(str(item)) for item in ids if item}
    wanted.discard(hide)
    if not wanted:
        return {}
    rows = await db.execute(select(User.id, User.full_name).where(User.id.in_(wanted)))
    return {str(key): name for key, name in rows.all()}


async def incident_projection(
    db: AsyncSession, incident: Incident, user: User, rel: Rel | None = None
) -> dict[str, Any]:
    """Project a readable ticket without disclosing internal communication.

    Followers get a reduced projection: no description and no creator identity.
    """
    rel = rel or await relationship(db, user, incident)
    fields = (
        "id",
        "number",
        "title",
        "description",
        "category_id",
        "building_id",
        "floor_id",
        "seat_id",
        "status",
        "requested_priority",
        "priority",
        "created_by",
        "assigned_to",
        "blocked_reason",
        "blocked_since",
        "assigned_at",
        "acknowledged_at",
        "first_in_progress_at",
        "resolved_at",
        "closed_at",
        "cancelled_at",
        "reopen_count",
        "close_kind",
        "duplicate_of_id",
        "routed_team_id",
        "triage_reason",
        "route_return_count",
        "ack_due_at",
        "resolve_due_at",
        "created_at",
        "updated_at",
    )
    result = {}
    for field in fields:
        value = getattr(incident, field)
        result[field] = (
            str(value)
            if isinstance(value, UUID)
            else (_iso(value) if isinstance(value, datetime) else value)
        )
    latest = await _latest_escalation(db, incident.id)
    result["latest_escalation"] = _escalation_projection(latest)
    if latest and latest.state in {EscalationState.APPROVED, EscalationState.REJECTED}:
        decision = await db.scalar(
            select(IncidentEvent)
            .where(
                IncidentEvent.incident_id == incident.id,
                IncidentEvent.type.in_(
                    [IncidentEventType.ESCALATION_APPROVED, IncidentEventType.ESCALATION_REJECTED]
                ),
                IncidentEvent.payload["escalation_id"].astext == str(latest.id),
            )
            .order_by(IncidentEvent.created_at.desc())
            .limit(1)
        )
        note_id = (decision.payload or {}).get("note_id") if decision else None
        if note_id:
            note = await db.get(IncidentNote, UUID(note_id))
            result["latest_escalation"]["decision_note_id"] = note_id
            if note is None or note.hidden_at is not None:
                result["latest_escalation"]["decision_reason"] = None
                result["latest_escalation"]["action_plan"] = None
            elif latest.state == EscalationState.REJECTED:
                result["latest_escalation"]["decision_reason"] = note.body
            elif latest.action_plan:
                result["latest_escalation"]["action_plan"] = note.body
    state = sla_state(incident)
    result["sla_state"] = state.value if state else None
    result["permissions"] = {
        "staff": can_staff(user, incident),
        "staff_read": rel.staff_read,
        "creator": rel.creator,
        "admin": rel.admin,
        "follower": rel.follower,
        "team_lead": rel.team_lead,
    }
    result["following"] = rel.follower
    result["people"] = await people_names(
        db,
        [incident.created_by, incident.assigned_to, latest.requester_id if latest else None],
        hide=hidden_identity(incident, rel),
    )
    if rel.follower_only:
        for key in FOLLOWER_HIDDEN:
            result.pop(key, None)
    return result


async def _finish(
    db: AsyncSession, incident: Incident, user: User, response: Response
) -> dict[str, Any]:
    await db.flush()
    result = await incident_projection(db, incident, user)
    await commit(db)
    response.headers["ETag"] = f'"{result["updated_at"]}"'
    return result


async def _validate_selection(
    db: AsyncSession,
    building_id: UUID,
    floor_id: UUID | None,
    seat_id: UUID | None,
    category_id: UUID,
    validate_location: bool = True,
    validate_category: bool = True,
) -> None:
    # Locks serialize new selections against administrator deactivation.
    if validate_location:
        building = await db.scalar(
            select(Building).where(Building.id == building_id).with_for_update()
        )
        if not building or not building.is_active:
            raise HTTPException(422, "An active building is required")
        floor = None
        if floor_id is not None:
            floor = await db.scalar(select(Floor).where(Floor.id == floor_id).with_for_update())
            if not floor or not floor.is_active or floor.building_id != building.id:
                raise HTTPException(422, "Floor must be active and belong to the building")
        if seat_id is not None:
            if floor is None:
                raise HTTPException(422, "Select a floor before selecting a seat")
            seat = await db.scalar(select(Seat).where(Seat.id == seat_id).with_for_update())
            if not seat or not seat.is_active or seat.floor_id != floor.id:
                raise HTTPException(422, "Seat must be active and belong to the floor")
    if not validate_category:
        return
    category = await db.get(Category, category_id)
    if not category or not category.is_active:
        raise HTTPException(422, "An active category is required")
    if category.parent_id:
        parent = await db.scalar(
            select(Category).where(Category.id == category.parent_id).with_for_update()
        )
        if not parent or not parent.is_active:
            raise HTTPException(422, "The category parent must be active")
        category = await db.scalar(
            select(Category)
            .where(Category.id == category_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if category is None or not category.is_active or category.parent_id != parent.id:
            raise HTTPException(422, "The selected category is no longer active")
    else:
        category = await db.scalar(
            select(Category)
            .where(Category.id == category_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if category is None or not category.is_active:
            raise HTTPException(422, "An active category is required")
    if not category.parent_id and await db.scalar(
        select(Category.id)
        .where(Category.parent_id == category.id, Category.is_active.is_(True))
        .limit(1)
    ):
        raise HTTPException(422, "Select an active child category")


async def _active_engineer(db: AsyncSession, user_id: UUID) -> EngineerProfile:
    account = await db.scalar(
        select(User)
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    profile = await db.scalar(
        select(EngineerProfile)
        .where(EngineerProfile.user_id == user_id)
        .execution_options(populate_existing=True)
    )
    if (
        account is None
        or not account.is_active
        or UserRole.ENGINEER not in (account.roles or [])
        or profile is None
        or not profile.is_active
    ):
        raise HTTPException(422, "Assignee must be an active engineer")
    return profile


def masked_id(user_id: UUID | None, hidden: UUID | None) -> str | None:
    """Return a user ID unless it is the identity hidden from this reader."""
    return None if user_id is None or user_id == hidden else str(user_id)


def hidden_identity(incident: Incident, rel: Rel) -> UUID | None:
    """Followers must not learn who reported the ticket (FR-5.3)."""
    return incident.created_by if rel.follower_only else None


def note_projection(note: IncidentNote, hide: UUID | None = None) -> dict[str, Any]:
    """Hidden notes expose a tombstone and never expose their stored body."""
    result = {
        "id": str(note.id),
        "incident_id": str(note.incident_id),
        "author_id": masked_id(note.author_id, hide),
        "visibility": note.visibility,
        "created_at": _iso(note.created_at),
        "updated_at": _iso(note.updated_at),
        "edited_at": _iso(note.edited_at),
        "hidden_at": _iso(note.hidden_at),
        "hidden": note.hidden_at is not None,
    }
    if note.hidden_at is None:
        result["body"] = note.body
    else:
        result["message"] = "Note hidden by administrator"
    return result


async def _add_note(
    db: AsyncSession, incident: Incident, user: User, body: str, visibility: NoteVisibility
) -> IncidentNote:
    staff = can_staff(user, incident)
    require_permission(staff or incident.created_by == user.id)
    require_permission(visibility == NoteVisibility.PUBLIC or staff)
    note = IncidentNote(
        incident_id=incident.id, author_id=user.id, body=body, visibility=visibility
    )
    db.add(note)
    await db.flush()
    event(
        db,
        incident,
        user,
        IncidentEventType.NOTE_ADDED,
        {"note_id": str(note.id), "visibility": visibility},
    )
    if visibility == NoteVisibility.PUBLIC and incident.assigned_to == user.id:
        incident.acknowledged_at = incident.acknowledged_at or utcnow()
    return note


async def _assign(
    db: AsyncSession,
    incident: Incident,
    user: User,
    assigned_to: UUID | None,
    reason: str | None,
    confirm_unavailable: bool = False,
) -> None:
    previous = incident.assigned_to
    if previous == assigned_to:
        raise HTTPException(409, "Incident already has that assignee")
    if previous is not None and not reason:
        raise HTTPException(422, "Reassignment or unassignment requires a reason")
    if assigned_to is None:
        if incident.status != IncidentStatus.OPEN:
            raise HTTPException(409, "Only Open incidents may be unassigned")
        if not reason:
            raise HTTPException(422, "Unassignment requires a reason")
    else:
        profile = await _active_engineer(db, assigned_to)
        if profile.availability == EngineerAvailability.OFF_DUTY and not confirm_unavailable:
            raise HTTPException(409, {
                "message": "Engineer is Off-duty; explicitly confirm assignment to continue",
                "confirmation_required": True,
                "assigned_to": str(assigned_to),
                "availability": profile.availability,
            })
        incident.assigned_at = incident.assigned_at or utcnow()
        incident.triage_reason = None
    incident.assigned_to = assigned_to
    event(
        db,
        incident,
        user,
        IncidentEventType.ASSIGNED if assigned_to else IncidentEventType.UNASSIGNED,
        {
            "previous_assignee": str(previous) if previous else None,
            "assignee": str(assigned_to) if assigned_to else None,
            "reason": reason,
            "unavailable_override": bool(
                assigned_to and profile.availability == EngineerAvailability.OFF_DUTY
            ),
        },
    )


@router.post("/incidents", status_code=201)
async def create_incident(
    payload: IncidentCreate, response: Response, db: Db, user: Actor
) -> dict[str, Any]:
    """Report a validated issue and allocate its PostgreSQL sequence number."""
    if payload.building_id is None:
        if payload.floor_id is not None or payload.seat_id is not None:
            raise fail(422, "validation_error", "building_id is required with floor_id or seat_id")
        seat = await db.get(Seat, user.default_seat_id) if user.default_seat_id else None
        floor = await db.get(Floor, seat.floor_id) if seat else None
        if seat is None or floor is None:
            raise fail(422, "location_required", "Choose a location or set a default seat")
        payload.building_id, payload.floor_id, payload.seat_id = floor.building_id, floor.id, seat.id
    await _validate_selection(
        db, payload.building_id, payload.floor_id, payload.seat_id, payload.category_id
    )
    number = await db.scalar(text("SELECT nextval('incident_number_seq')"))
    incident = Incident(
        **payload.model_dump(),
        number=f"INC-{number:06d}",
        created_by=user.id,
        status=IncidentStatus.OPEN,
        priority=payload.requested_priority,
    )
    db.add(incident)
    await db.flush()
    event(
        db,
        incident,
        user,
        IncidentEventType.CREATED,
        {"status": IncidentStatus.OPEN, "priority": incident.priority},
    )
    routing = await route_new_incident(db, incident)
    result = await _finish(db, incident, user, response)
    result["routing"] = routing
    return result


@router.get("/incidents")
async def list_incidents(
    request: Request,
    db: Db,
    user: Actor,
    q: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    status: Annotated[list[IncidentStatus] | None, Query()] = None,
    priority: Annotated[list[IncidentPriority] | None, Query()] = None,
    category_id: UUID | None = None,
    building_id: UUID | None = None,
    floor_id: UUID | None = None,
    seat_id: UUID | None = None,
    assigned_to: UUID | AssigneeFilter | None = None,
    created_by: UUID | None = None,
    escalation: EscalationState | EscalationFilter | None = None,
    scope: IncidentScope = IncidentScope.ALL,
    unassigned: bool = False,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    resolved_from: datetime | None = None,
    resolved_to: datetime | None = None,
    resolved_by: UUID | None = None,
    category_group_id: UUID | None = None,
    team_id: UUID | None = None,
    routed_team_id: UUID | None = None,
    triage_reason: TriageReason | TriageFilter | None = None,
    returned_from: datetime | None = None,
    returned_to: datetime | None = None,
    sla_state: Annotated[list[SlaState] | None, Query()] = None,
    followed: bool | None = None,
    public_note: Annotated[str | None, Query(pattern="^none$")] = None,
    sort: IncidentSort = IncidentSort.CREATED,
    direction: SortDirection = SortDirection.DESC,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
) -> dict[str, Any]:
    """Search only authorized tickets using PostgreSQL full-text predicates."""
    accepted = {
        "q",
        "status",
        "priority",
        "category_id",
        "building_id",
        "floor_id",
        "seat_id",
        "assigned_to",
        "created_by",
        "escalation",
        "scope",
        "unassigned",
        "date_from",
        "date_to",
        "created_from",
        "created_to",
        "resolved_from",
        "resolved_to",
        "resolved_by",
        "category_group_id",
        "team_id",
        "routed_team_id",
        "triage_reason",
        "returned_from",
        "returned_to",
        "sla_state",
        "followed",
        "public_note",
        "sort",
        "direction",
        "page",
        "page_size",
    }
    if set(request.query_params) - accepted:
        raise HTTPException(422, "Unsupported incident filter")
    if created_by is not None:
        if not is_admin(user):
            raise fail(403, "filter_not_permitted", "Filter is not permitted")
    if (routed_team_id is not None or triage_reason is not None) and not is_admin(user):
        raise fail(403, "filter_not_permitted", "Filter is not permitted")
    if team_id is not None and not is_admin(user):
        led = await db.scalar(
            select(Team.id).where(Team.id == team_id, Team.lead_user_id == user.id)
        )
        if led is None:
            raise fail(403, "filter_not_permitted", "Filter is not permitted")
    # created_from/created_to are the design's names; created_to is exclusive.
    date_from = date_from or created_from
    if unassigned and assigned_to not in {None, AssigneeFilter.UNASSIGNED}:
        raise HTTPException(422, "Unassigned and assigned_to filters conflict")
    for bound in (
        date_from, date_to, created_to, resolved_from, resolved_to, returned_from, returned_to
    ):
        if bound is not None and bound.tzinfo is None:
            raise HTTPException(422, "Date filters must include a timezone")
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "date_from must not exceed date_to")
    conditions = [readable_clause(user)]
    if scope == IncidentScope.MINE:
        conditions.append(Incident.created_by == user.id)
    elif scope == IncidentScope.ASSIGNED:
        require_permission(is_admin(user) or UserRole.ENGINEER in (user.roles or []))
        conditions.append(Incident.assigned_to == user.id)
    if q:
        document = func.to_tsvector(
            literal_column("'english'"),
            Incident.title + " " + Incident.description,
        )
        # INC-000123, inc-123 and 123 all find ticket INC-000123.
        numeric = re.fullmatch(r"(?:inc-?)?0*(\d{1,12})", q.strip(), re.IGNORECASE)
        number = f"INC-{int(numeric.group(1)):06d}" if numeric else None
        conditions.append(
            or_(
                Incident.number == number,
                func.upper(Incident.number) == q.strip().upper(),
                document.op("@@")(func.plainto_tsquery(literal_column("'english'"), q)),
            )
        )
    if status:
        conditions.append(Incident.status.in_(status))
    if priority:
        conditions.append(Incident.priority.in_(priority))
    for field, value in (
        (Incident.building_id, building_id),
        (Incident.floor_id, floor_id),
        (Incident.seat_id, seat_id),
        (Incident.assigned_to, assigned_to if assigned_to != AssigneeFilter.UNASSIGNED else None),
        (Incident.created_by, created_by),
    ):
        if value is not None:
            conditions.append(field == value)
    if category_id is not None:
        children = select(Category.id).where(Category.parent_id == category_id)
        conditions.append(
            or_(Incident.category_id == category_id, Incident.category_id.in_(children))
        )
    if unassigned or assigned_to == AssigneeFilter.UNASSIGNED:
        conditions.append(Incident.assigned_to.is_(None))
    if date_from:
        conditions.append(Incident.created_at >= date_from)
    if date_to:
        conditions.append(Incident.created_at <= date_to)
    if created_to:
        conditions.append(Incident.created_at < created_to)
    if resolved_from:
        conditions.append(Incident.resolved_at >= resolved_from)
    if resolved_to:
        conditions.append(Incident.resolved_at < resolved_to)
    if resolved_by is not None:
        latest_resolution = (
            select(IncidentEvent.payload["assigned_to"].astext)
            .where(
                IncidentEvent.incident_id == Incident.id,
                IncidentEvent.type == IncidentEventType.STATUS_CHANGED,
                IncidentEvent.payload["to"].astext == IncidentStatus.RESOLVED,
            )
            .order_by(IncidentEvent.created_at.desc(), IncidentEvent.id.desc())
            .limit(1)
            .correlate(Incident)
            .scalar_subquery()
        )
        conditions.append(Incident.resolved_at.is_not(None))
        conditions.append(latest_resolution == str(resolved_by))
    if category_group_id is not None:
        group_tops = select(Category.id).where(Category.group_id == category_group_id)
        group_children = select(Category.id).where(Category.parent_id.in_(group_tops))
        conditions.append(
            or_(Incident.category_id.in_(group_tops), Incident.category_id.in_(group_children))
        )
    if team_id is not None:
        members = select(EngineerProfile.user_id).where(EngineerProfile.team_id == team_id)
        conditions.append(Incident.assigned_to.in_(members))
    if routed_team_id is not None:
        conditions.append(Incident.routed_team_id == routed_team_id)
    if triage_reason == TriageFilter.NONE:
        conditions.append(Incident.triage_reason.is_(None))
    elif triage_reason is not None:
        conditions.append(Incident.triage_reason == triage_reason)
    if returned_from is not None or returned_to is not None:
        # Tickets returned to triage in the range, wherever they are now.
        returned = select(IncidentEvent.id).where(
            IncidentEvent.incident_id == Incident.id,
            IncidentEvent.type == IncidentEventType.UNASSIGNED,
            IncidentEvent.payload["via"].astext == "returned_to_triage",
        )
        if returned_from is not None:
            returned = returned.where(IncidentEvent.created_at >= returned_from)
        if returned_to is not None:
            returned = returned.where(IncidentEvent.created_at < returned_to)
        conditions.append(exists(returned))
    if sla_state:
        conditions.append(sla_state_sql().in_([state.value for state in sla_state]))
    if followed is not None:
        follows = exists(
            select(IncidentFollower.user_id).where(
                IncidentFollower.incident_id == Incident.id,
                IncidentFollower.user_id == user.id,
            )
        )
        conditions.append(follows if followed else ~follows)
    if public_note == "none":
        conditions.append(
            ~exists(
                select(IncidentNote.id).where(
                    IncidentNote.incident_id == Incident.id,
                    IncidentNote.visibility == NoteVisibility.PUBLIC,
                    IncidentNote.hidden_at.is_(None),
                )
            )
        )
    if escalation is not None:
        latest_state = (
            select(EscalationRequest.state)
            .where(EscalationRequest.incident_id == Incident.id)
            .order_by(EscalationRequest.requested_at.desc(), EscalationRequest.id.desc())
            .limit(1)
            .correlate(Incident)
            .scalar_subquery()
        )
        conditions.append(
            latest_state.is_(None)
            if escalation == EscalationFilter.NONE
            else latest_state == escalation
        )
    sort_column = {
        IncidentSort.CREATED: Incident.created_at,
        IncidentSort.UPDATED: Incident.updated_at,
        IncidentSort.PRIORITY: case(PRIORITY_RANK, value=Incident.priority, else_=0),
        IncidentSort.SLA_DUE: next_due_sql(),
    }[sort]
    order = sort_column.asc() if direction == SortDirection.ASC else sort_column.desc()
    tie_break = (
        [Incident.created_at.asc(), Incident.id.asc()]
        if sort in {IncidentSort.PRIORITY, IncidentSort.SLA_DUE}
        else [Incident.id.asc()]
    )
    total = await db.scalar(select(func.count()).select_from(Incident).where(*conditions))
    incidents = (
        await db.scalars(
            select(Incident)
            .where(*conditions)
            .order_by(order, *tie_break)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return {
        "items": [await incident_projection(db, item, user) for item in incidents],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


EXPORT_MAX_ROWS = 5000
EXPORT_COLUMNS = (
    "number", "title", "status", "priority", "sla_state", "category", "building",
    "assignee", "created_at", "resolved_at", "closed_at",
)
FORMULA_PREFIXES = {"=", "+", "-", "@", "\t", "\r"}


def _csv_cell(value: object) -> str:
    """Stop spreadsheet apps from running cell text as a formula."""
    text_value = "" if value is None else str(value)
    return "'" + text_value if text_value[:1] in FORMULA_PREFIXES else text_value


async def export_incidents(**params: Any) -> Response:
    """CSV of the same authorized, filtered list as GET /incidents (stretch goal)."""
    db: AsyncSession = params["db"]
    rows: list[dict[str, Any]] = []
    page = 1
    while len(rows) < EXPORT_MAX_ROWS:
        result = await list_incidents(**{**params, "page": page, "page_size": 100})
        rows += result["items"]
        if not result["items"] or len(rows) >= result["total"]:
            break
        page += 1
    rows = rows[:EXPORT_MAX_ROWS]

    async def names(model: Any, attr: str, ids: set[str]) -> dict[str, str]:
        if not ids:
            return {}
        found = await db.execute(
            select(model.id, getattr(model, attr)).where(model.id.in_([UUID(i) for i in ids]))
        )
        return {str(key): value for key, value in found.all()}

    categories = await names(Category, "name", {r["category_id"] for r in rows if r.get("category_id")})
    buildings = await names(Building, "name", {r["building_id"] for r in rows if r.get("building_id")})
    users = await names(User, "full_name", {r["assigned_to"] for r in rows if r.get("assigned_to")})
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(EXPORT_COLUMNS)
    for r in rows:
        writer.writerow(
            _csv_cell(value)
            for value in (
                r.get("number"), r.get("title"), r.get("status"), r.get("priority"),
                r.get("sla_state"), categories.get(r.get("category_id")),
                buildings.get(r.get("building_id")), users.get(r.get("assigned_to")),
                r.get("created_at"), r.get("resolved_at"), r.get("closed_at"),
            )
        )
    return Response(
        out.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="incidents.csv"'},
    )


# Same parameters and validation as the list; declared before /incidents/{incident_id}.
export_incidents.__signature__ = inspect.signature(list_incidents).replace(  # type: ignore[attr-defined]
    return_annotation=Response
)
router.add_api_route(
    "/incidents/export.csv", export_incidents, methods=["GET"], response_class=Response
)


@router.get("/incidents/{incident_id}")
async def get_incident(
    incident_id: UUID, response: Response, db: Db, user: Actor
) -> dict[str, Any]:
    incident = await load_incident(db, user, incident_id)
    result = await incident_projection(db, incident, user)
    response.headers["ETag"] = f'"{result["updated_at"]}"'
    return result


@router.patch("/incidents/{incident_id}")
async def update_incident(
    incident_id: UUID,
    payload: IncidentPatch,
    response: Response,
    db: Db,
    user: Actor,
    if_match: Version = None,
) -> dict[str, Any]:
    incident = await load_incident(db, user, incident_id, lock=True)
    require_permission(can_staff(user, incident) or incident.created_by == user.id)
    require_nonfinal(incident)
    if not can_staff(user, incident) and incident.status != IncidentStatus.OPEN:
        raise HTTPException(409, "Creators may edit details only while Open")
    check_match(incident, if_match)
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(422, "At least one detail field is required")
    if any(value is None for key, value in changes.items() if key not in {"floor_id", "seat_id"}):
        raise HTTPException(422, "Required incident fields cannot be null")
    if "building_id" in changes and changes["building_id"] != incident.building_id:
        changes.setdefault("floor_id", None)
        changes.setdefault("seat_id", None)
    if "floor_id" in changes and changes["floor_id"] != incident.floor_id:
        changes.setdefault("seat_id", None)
    selection = {
        key: changes.get(key, getattr(incident, key))
        for key in ("building_id", "floor_id", "seat_id", "category_id")
    }
    if set(changes) & set(selection):
        await _validate_selection(
            db,
            **selection,
            validate_location=bool(set(changes) & {"building_id", "floor_id", "seat_id"}),
            validate_category="category_id" in changes,
        )
    for key, value in changes.items():
        setattr(incident, key, value)
    touch(incident)
    event(db, incident, user, IncidentEventType.DETAILS_CHANGED, {"fields": sorted(changes)})
    return await _finish(db, incident, user, response)


@router.post("/incidents/{incident_id}/assign")
async def assign_incident(
    incident_id: UUID,
    payload: Assignment,
    response: Response,
    db: Db,
    user: Actor,
    if_match: Version = None,
) -> dict[str, Any]:
    incident = await load_incident(db, user, incident_id, lock=True)
    require_permission(is_admin(user))
    require_nonfinal(incident)
    check_match(incident, if_match)
    await _assign(
        db, incident, user, payload.assigned_to, payload.reason, payload.confirm_unavailable
    )
    touch(incident)
    return await _finish(db, incident, user, response)


@router.patch("/incidents/{incident_id}/priority")
async def change_priority(
    incident_id: UUID,
    payload: PriorityChange,
    response: Response,
    db: Db,
    user: Actor,
    if_match: Version = None,
) -> dict[str, Any]:
    incident = await load_incident(db, user, incident_id, lock=True)
    require_permission(is_admin(user))
    require_nonfinal(incident)
    check_match(incident, if_match)
    if payload.priority == incident.priority:
        raise HTTPException(409, "Incident already has that priority")
    previous = incident.priority
    incident.priority = payload.priority
    incident.set_due_times()
    touch(incident)
    event(
        db,
        incident,
        user,
        IncidentEventType.PRIORITY_CHANGED,
        {"from": previous, "to": incident.priority, "reason": payload.reason},
    )
    return await _finish(db, incident, user, response)


@router.post("/incidents/{incident_id}/transitions")
async def transition_incident(
    incident_id: UUID,
    payload: Transition,
    response: Response,
    db: Db,
    user: Actor,
    if_match: Version = None,
) -> dict[str, Any]:
    """Run one explicitly authorized transition and its required note atomically."""
    incident = await load_incident(db, user, incident_id, lock=True)
    creator = incident.created_by == user.id
    staff = can_staff(user, incident)
    if (
        payload.status in FINAL_STATUSES
        or payload.status == IncidentStatus.IN_PROGRESS
        and incident.status == IncidentStatus.RESOLVED
    ):
        require_permission(is_admin(user) or creator)
    else:
        require_permission(staff)
    require_nonfinal(incident)
    check_match(incident, if_match)
    previous = incident.status
    target = payload.status
    if (previous, target) not in VALID_TRANSITIONS:
        raise fail(409, "invalid_transition", "Invalid incident transition")
    now = utcnow()
    extra: dict[str, Any] = {}
    if target == IncidentStatus.CANCELLED:
        # Creators may cancel any Open ticket: auto-routing assigns most tickets at
        # creation, and nobody has started work while it is Open.
        if not payload.reason:
            raise HTTPException(422, "Cancellation requires a reason")
        incident.cancelled_at = now
        extra["reason"] = payload.reason
    elif previous == IncidentStatus.OPEN and target == IncidentStatus.IN_PROGRESS:
        if incident.assigned_to is None:
            raise fail(409, "no_assignee", "Assign an engineer before starting work")
        incident.first_in_progress_at = incident.first_in_progress_at or now
        incident.acknowledged_at = incident.acknowledged_at or now
    elif target == IncidentStatus.BLOCKED:
        if payload.reason not in BLOCKED_REASONS:
            raise HTTPException(422, "A valid blocked reason code is required")
        if not payload.note:
            raise HTTPException(422, "Blocking requires a note")
        note = await _add_note(db, incident, user, payload.note, payload.visibility)
        incident.blocked_reason = BlockedReason(payload.reason)
        incident.blocked_since = now
        extra.update(reason=payload.reason, note_id=str(note.id), visibility=payload.visibility)
    elif previous == IncidentStatus.BLOCKED:
        extra.update(reason=incident.blocked_reason, blocked_since=_iso(incident.blocked_since))
        incident.blocked_reason = None
        incident.blocked_since = None
    elif target == IncidentStatus.RESOLVED:
        if not payload.note or payload.visibility != NoteVisibility.PUBLIC:
            raise HTTPException(422, "Resolution requires a public note")
        note = await _add_note(db, incident, user, payload.note, NoteVisibility.PUBLIC)
        incident.resolved_at = now
        extra.update(
            note_id=str(note.id),
            assignee=str(incident.assigned_to),
            assigned_to=str(incident.assigned_to),
            visibility=NoteVisibility.PUBLIC,
        )
    elif target == IncidentStatus.CLOSED:
        incident.closed_at = now
        incident.close_kind = CloseKind.CONFIRMED if creator else CloseKind.ADMIN
        extra["close_kind"] = incident.close_kind
    elif previous == IncidentStatus.RESOLVED:
        if not payload.note or payload.visibility != NoteVisibility.PUBLIC:
            raise HTTPException(422, "Rejecting a resolution requires a public note")
        note = await _add_note(db, incident, user, payload.note, NoteVisibility.PUBLIC)
        extra.update(
            note_id=str(note.id),
            previous_resolved_at=_iso(incident.resolved_at),
            visibility=NoteVisibility.PUBLIC,
        )
        incident.resolved_at = None
        incident.reopen_count += 1
    incident.status = target
    touch(incident)
    event(
        db,
        incident,
        user,
        IncidentEventType.STATUS_CHANGED,
        {"from": previous, "to": target, **extra},
    )
    if target in FINAL_STATUSES:
        await supersede_pending(db, incident, user)
    return await _finish(db, incident, user, response)


@router.post("/incidents/{incident_id}/escalation", status_code=201)
async def request_escalation(
    incident_id: UUID,
    payload: EscalationCreate,
    response: Response,
    db: Db,
    user: Actor,
    if_match: Version = None,
) -> dict[str, Any]:
    incident = await load_incident(db, user, incident_id, lock=True)
    require_permission(incident.created_by == user.id)
    require_nonfinal(incident)
    check_match(incident, if_match)
    latest = await _latest_escalation(db, incident.id)
    if latest and latest.state == EscalationState.PENDING:
        raise fail(409, "escalation_pending", "An escalation request is already pending")
    now = utcnow()
    if latest and now < _aware(latest.requested_at) + timedelta(hours=24):
        raise fail(
            409,
            "escalation_cooldown",
            "Wait 24 hours after the prior request before escalating again",
            retry_after=_iso(_aware(latest.requested_at) + timedelta(hours=24)),
        )
    entry = EscalationRequest(
        incident_id=incident.id,
        requester_id=user.id,
        reason=payload.reason,
        requested_at=now,
        state=EscalationState.PENDING,
    )
    db.add(entry)
    await db.flush()
    touch(incident)
    event(
        db,
        incident,
        user,
        IncidentEventType.ESCALATION_REQUESTED,
        {"escalation_id": str(entry.id), "reason": payload.reason},
    )
    return await _finish(db, incident, user, response)


@router.post("/incidents/{incident_id}/escalation/decision")
async def decide_escalation(
    incident_id: UUID,
    payload: EscalationDecision,
    response: Response,
    db: Db,
    user: Actor,
    if_match: Version = None,
) -> dict[str, Any]:
    incident = await load_incident(db, user, incident_id, lock=True)
    require_permission(is_admin(user))
    require_nonfinal(incident)
    check_match(incident, if_match)
    entry = await db.scalar(
        select(EscalationRequest)
        .where(
            EscalationRequest.incident_id == incident.id,
            EscalationRequest.state == EscalationState.PENDING,
        )
        .with_for_update()
    )
    if entry is None:
        raise fail(409, "escalation_not_pending", "No pending escalation request")
    decision_note = None
    if payload.approved:
        if incident.priority == IncidentPriority.CRITICAL:
            if not payload.action_plan:
                raise HTTPException(422, "Critical approval requires a public action plan")
            if payload.priority not in {None, IncidentPriority.CRITICAL}:
                raise HTTPException(422, "Critical escalation cannot lower priority")
            decision_note = await _add_note(
                db, incident, user, payload.action_plan, NoteVisibility.PUBLIC
            )
        else:
            if (
                payload.priority is None
                or PRIORITY_RANK[payload.priority] <= PRIORITY_RANK[incident.priority]
            ):
                raise HTTPException(422, "Approval must raise priority by at least one level")
            previous = incident.priority
            incident.priority = payload.priority
            incident.set_due_times()
            event(
                db,
                incident,
                user,
                IncidentEventType.PRIORITY_CHANGED,
                {
                    "from": previous,
                    "to": incident.priority,
                    "reason": "Escalation approved",
                },
            )
            if payload.action_plan:
                decision_note = await _add_note(
                    db, incident, user, payload.action_plan, NoteVisibility.PUBLIC
                )
        entry.state = EscalationState.APPROVED
    else:
        if not payload.decision_reason:
            raise HTTPException(422, "Rejection requires a public reason")
        if payload.priority is not None or payload.action_plan is not None:
            raise HTTPException(422, "Rejection accepts only its reason and optional reassignment")
        decision_note = await _add_note(
            db, incident, user, payload.decision_reason, NoteVisibility.PUBLIC
        )
        entry.state = EscalationState.REJECTED
    if "assigned_to" in payload.model_fields_set:
        await _assign(
            db,
            incident,
            user,
            payload.assigned_to,
            "Escalation decision",
            payload.confirm_unavailable,
        )
    entry.decided_by = user.id
    entry.decided_at = utcnow()
    entry.decision_reason = payload.decision_reason
    entry.action_plan = payload.action_plan
    touch(incident)
    event(
        db,
        incident,
        user,
        IncidentEventType.ESCALATION_APPROVED
        if payload.approved
        else IncidentEventType.ESCALATION_REJECTED,
        {
            "escalation_id": str(entry.id),
            "reason": payload.decision_reason,
            "priority": incident.priority,
            "note_id": str(decision_note.id) if decision_note else None,
            "visibility": NoteVisibility.PUBLIC,
        },
    )
    return await _finish(db, incident, user, response)


@router.get("/incidents/{incident_id}/notes")
async def list_notes(incident_id: UUID, db: Db, user: Actor) -> dict[str, Any]:
    incident = await load_incident(db, user, incident_id)
    rel = await relationship(db, user, incident)
    statement = select(IncidentNote).where(IncidentNote.incident_id == incident.id)
    if not rel.staff_read:
        statement = statement.where(IncidentNote.visibility == NoteVisibility.PUBLIC)
    notes = (await db.scalars(statement.order_by(IncidentNote.created_at, IncidentNote.id))).all()
    hide = hidden_identity(incident, rel)
    return {
        "items": [note_projection(note, hide) for note in notes],
        "total": len(notes),
        "people": await people_names(db, [note.author_id for note in notes], hide=hide),
    }


@router.post("/incidents/{incident_id}/notes", status_code=201)
async def create_note(
    incident_id: UUID,
    payload: NoteCreate,
    response: Response,
    db: Db,
    user: Actor,
    if_match: Version = None,
) -> dict[str, Any]:
    incident = await load_incident(db, user, incident_id, lock=True)
    staff = can_staff(user, incident)
    require_permission(staff or incident.created_by == user.id)
    require_permission(payload.visibility == NoteVisibility.PUBLIC or staff)
    require_nonfinal(incident)
    check_match(incident, if_match)
    note = await _add_note(db, incident, user, payload.body, payload.visibility)
    touch(incident)
    await db.flush()
    result = note_projection(note)
    await commit(db)
    response.headers["ETag"] = f'"{result["updated_at"]}"'
    return result


async def _load_note(db: AsyncSession, user: User, note_id: UUID) -> tuple[Incident, IncidentNote]:
    parent_id = await db.scalar(select(IncidentNote.incident_id).where(IncidentNote.id == note_id))
    if parent_id is None:
        raise HTTPException(404, "Note not found")
    try:
        incident = await load_incident(db, user, parent_id, lock=True)
    except HTTPException as error:
        if error.status_code == 404:
            raise HTTPException(404, "Note not found") from None
        raise
    note = await db.scalar(
        select(IncidentNote)
        .where(IncidentNote.id == note_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if note is None or (
        note.visibility == NoteVisibility.INTERNAL
        and not (await relationship(db, user, incident)).staff_read
    ):
        raise HTTPException(404, "Note not found")
    return incident, note


@router.patch("/notes/{note_id}")
async def edit_note(
    note_id: UUID,
    payload: NotePatch,
    response: Response,
    db: Db,
    user: Actor,
    if_match: Version = None,
) -> dict[str, Any]:
    incident, note = await _load_note(db, user, note_id)
    staff = can_staff(user, incident)
    require_permission(note.author_id == user.id)
    require_permission(staff or incident.created_by == user.id)
    require_permission(note.visibility == NoteVisibility.PUBLIC or staff)
    require_nonfinal(incident)
    check_match(note, if_match)
    if note.hidden_at is not None:
        raise HTTPException(409, "Hidden notes cannot be edited")
    if utcnow() >= _aware(note.created_at) + timedelta(minutes=10):
        raise fail(409, "edit_window_closed", "The ten-minute note edit window has expired")
    previous_body = note.body
    note.body = payload.body
    note.edited_at = utcnow()
    touch(note)
    touch(incident)
    # Preserve edit history for audit while ordinary timeline projections never
    # return historical bodies, including after a subsequent hide.
    event(
        db,
        incident,
        user,
        IncidentEventType.NOTE_EDITED,
        {
            "note_id": str(note.id),
            "visibility": note.visibility,
            "previous_body": previous_body,
            "body": note.body,
        },
    )
    await db.flush()
    result = note_projection(note)
    await commit(db)
    response.headers["ETag"] = f'"{result["updated_at"]}"'
    return result


@router.post("/notes/{note_id}/hide")
async def hide_note(
    note_id: UUID,
    payload: Reason,
    response: Response,
    db: Db,
    user: Actor,
    if_match: Version = None,
) -> dict[str, Any]:
    incident, note = await _load_note(db, user, note_id)
    require_permission(is_admin(user))
    check_match(note, if_match)
    # Repeating an already-hidden note with its current version is a no-op.
    if note.hidden_at is None:
        note.hidden_at = utcnow()
        note.hidden_by = user.id
        note.hide_reason = payload.reason
        touch(note)
        touch(incident)
        event(
            db,
            incident,
            user,
            IncidentEventType.NOTE_HIDDEN,
            {
                "note_id": str(note.id),
                "visibility": note.visibility,
                "reason": payload.reason,
            },
        )
    await db.flush()
    result = note_projection(note)
    await commit(db)
    response.headers["ETag"] = f'"{result["updated_at"]}"'
    return result


@router.get("/incidents/{incident_id}/timeline")
async def get_timeline(incident_id: UUID, db: Db, user: Actor) -> dict[str, Any]:
    """Return sanitized events, checking each referenced note's current state."""
    incident = await load_incident(db, user, incident_id)
    rel = await relationship(db, user, incident)
    staff = rel.staff_read
    hide = hidden_identity(incident, rel)
    events = (
        await db.scalars(
            select(IncidentEvent)
            .where(IncidentEvent.incident_id == incident.id)
            .order_by(IncidentEvent.created_at, IncidentEvent.id)
        )
    ).all()
    notes = (
        await db.scalars(select(IncidentNote).where(IncidentNote.incident_id == incident.id))
    ).all()
    by_id = {str(note.id): note for note in notes}
    safe_keys = {
        "from",
        "to",
        "status",
        "priority",
        "fields",
        "assignee",
        "previous_assignee",
        "assigned_to",
        "reason",
        "blocked_since",
        "previous_resolved_at",
        "escalation_id",
        "note_id",
        "visibility",
        "via",
        "team_id",
        "close_kind",
        "duplicate_of_number",
        "attachment_id",
    }
    staff_only_reasons = {
        IncidentEventType.ASSIGNED,
        IncidentEventType.UNASSIGNED,
        IncidentEventType.PRIORITY_CHANGED,
        IncidentEventType.DUPLICATE_MARKED,
    }
    items = []
    for entry in events:
        stored = entry.payload or {}
        note = by_id.get(str(stored.get("note_id")))
        if entry.type in {
            IncidentEventType.NOTE_ADDED,
            IncidentEventType.NOTE_EDITED,
            IncidentEventType.NOTE_HIDDEN,
        } and (note is None or (note.visibility == NoteVisibility.INTERNAL and not staff)):
            continue
        if (
            entry.type == IncidentEventType.ATTACHMENT_ADDED
            and stored.get("visibility") == NoteVisibility.INTERNAL
            and not staff
        ):
            continue
        payload = {key: value for key, value in stored.items() if key in safe_keys}
        if not staff and entry.type in staff_only_reasons:
            # Reassignment, priority and duplicate reasons are staff-only (N21).
            payload.pop("reason", None)
            payload.pop("team_id", None)
        if stored.get("visibility") == NoteVisibility.INTERNAL and not staff:
            # Workflow status itself is public; its note identity/visibility is not.
            payload.pop("note_id", None)
            payload.pop("visibility", None)
        if note is not None and note.hidden_at is not None:
            payload["hidden"] = True
        # Hide reasons are retained for admin auditing, not public disclosure.
        if entry.type == IncidentEventType.NOTE_HIDDEN and not is_admin(user):
            payload.pop("reason", None)
        if entry.type in {
            IncidentEventType.ESCALATION_APPROVED,
            IncidentEventType.ESCALATION_REJECTED,
        }:
            # Decision prose is shown through its hide-aware public note or
            # escalation projection, never duplicated in ordinary audit events.
            payload.pop("reason", None)
        items.append(
            {
                "id": str(entry.id),
                "incident_id": str(entry.incident_id),
                "actor_id": masked_id(entry.actor_id, hide),
                "type": entry.type,
                "payload": payload,
                "created_at": _iso(entry.created_at),
                **(
                    {"note": note_projection(note, hide)}
                    if entry.type == IncidentEventType.NOTE_ADDED and note is not None
                    else {}
                ),
            }
        )
    referenced = [item["actor_id"] for item in items]
    for item in items:
        referenced += [item["payload"].get("assignee"), item["payload"].get("previous_assignee")]
    return {
        "items": items,
        "total": len(items),
        "people": await people_names(db, referenced, hide=hide),
    }
