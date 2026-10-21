"""Facility incidents, lifecycle, communication and escalation endpoints."""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import case, func, literal_column, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..dependencies import current_user
from ..models import (
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
from ..permissions import (
    can_staff,
    check_match,
    event,
    is_admin,
    load_incident,
    readable_clause,
    require_nonfinal,
    require_permission,
    touch,
)

router = APIRouter(tags=["incidents"])
Priority = Literal["Critical", "High", "Medium", "Low"]
Status = Literal["Open", "In Progress", "Blocked", "Resolved", "Closed", "Cancelled"]
Visibility = Literal["public", "internal"]
EscalationState = Literal["Pending", "Approved", "Rejected", "Superseded", "none"]
PRIORITY_RANK = {"Low": 1, "Medium": 2, "High": 3, "Critical": 4}
BLOCKED_REASONS = {
    "Waiting on parts",
    "Waiting on employee",
    "Vendor needed",
    "Access needed",
    "Other",
}
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
    building_id: UUID
    floor_id: UUID
    seat_id: UUID | None = None
    requested_priority: Priority = "Medium"


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


class PriorityChange(Payload):
    priority: Priority
    reason: str = Field(min_length=1, max_length=500)


class Transition(Payload):
    status: Status
    reason: str | None = Field(default=None, min_length=1, max_length=500)
    note: str | None = Field(default=None, min_length=1, max_length=5000)
    visibility: Visibility = "public"


class NoteCreate(Payload):
    body: str = Field(min_length=1, max_length=5000)
    visibility: Visibility = "public"


class NotePatch(Payload):
    body: str = Field(min_length=1, max_length=5000)


class Reason(Payload):
    reason: str = Field(min_length=1, max_length=500)


class EscalationCreate(Payload):
    reason: str = Field(min_length=10, max_length=500)


class EscalationDecision(Payload):
    approved: bool
    priority: Priority | None = None
    decision_reason: str | None = Field(default=None, min_length=1, max_length=500)
    action_plan: str | None = Field(default=None, min_length=1, max_length=5000)
    assigned_to: UUID | None = None


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


async def incident_projection(db: AsyncSession, incident: Incident, user: User) -> dict[str, Any]:
    """Project a readable ticket without disclosing internal communication."""
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
    if latest and latest.state in {"Approved", "Rejected"}:
        decision = await db.scalar(
            select(IncidentEvent)
            .where(
                IncidentEvent.incident_id == incident.id,
                IncidentEvent.type.in_(["escalation_approved", "escalation_rejected"]),
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
            elif latest.state == "Rejected":
                result["latest_escalation"]["decision_reason"] = note.body
            elif latest.action_plan:
                result["latest_escalation"]["action_plan"] = note.body
    result["permissions"] = {
        "staff": can_staff(user, incident),
        "creator": incident.created_by == user.id,
        "admin": is_admin(user),
    }
    return result


async def _finish(
    db: AsyncSession, incident: Incident, user: User, response: Response
) -> dict[str, Any]:
    await db.flush()
    result = await incident_projection(db, incident, user)
    await db.commit()
    response.headers["ETag"] = f'"{result["updated_at"]}"'
    return result


async def _validate_selection(
    db: AsyncSession,
    building_id: UUID,
    floor_id: UUID,
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
        floor = await db.scalar(select(Floor).where(Floor.id == floor_id).with_for_update())
        if not building or not building.is_active:
            raise HTTPException(422, "An active building is required")
        if not floor or not floor.is_active or floor.building_id != building.id:
            raise HTTPException(422, "Floor must be active and belong to the building")
        if seat_id is not None:
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
        or "engineer" not in (account.roles or [])
        or profile is None
        or not profile.is_active
    ):
        raise HTTPException(422, "Assignee must be an active engineer")
    return profile


def note_projection(note: IncidentNote) -> dict[str, Any]:
    """Hidden notes expose a tombstone and never expose their stored body."""
    result = {
        "id": str(note.id),
        "incident_id": str(note.incident_id),
        "author_id": str(note.author_id),
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
    db: AsyncSession, incident: Incident, user: User, body: str, visibility: str
) -> IncidentNote:
    require_permission(visibility == "public" or can_staff(user, incident))
    note = IncidentNote(
        incident_id=incident.id, author_id=user.id, body=body, visibility=visibility
    )
    db.add(note)
    await db.flush()
    event(
        db,
        incident,
        user,
        "note_added",
        {"note_id": str(note.id), "visibility": visibility},
    )
    if visibility == "public" and incident.assigned_to == user.id:
        incident.acknowledged_at = incident.acknowledged_at or utcnow()
    return note


async def _assign(
    db: AsyncSession,
    incident: Incident,
    user: User,
    assigned_to: UUID | None,
    reason: str | None,
) -> None:
    previous = incident.assigned_to
    if previous == assigned_to:
        raise HTTPException(409, "Incident already has that assignee")
    if previous is not None and not reason:
        raise HTTPException(422, "Reassignment or unassignment requires a reason")
    if assigned_to is None:
        if incident.status != "Open":
            raise HTTPException(409, "Only Open incidents may be unassigned")
        if not reason:
            raise HTTPException(422, "Unassignment requires a reason")
    else:
        await _active_engineer(db, assigned_to)
        incident.assigned_at = incident.assigned_at or utcnow()
    incident.assigned_to = assigned_to
    event(
        db,
        incident,
        user,
        "assigned" if assigned_to else "unassigned",
        {
            "previous_assignee": str(previous) if previous else None,
            "assignee": str(assigned_to) if assigned_to else None,
            "reason": reason,
        },
    )


async def _supersede_pending(db: AsyncSession, incident: Incident, user: User) -> None:
    pending = await db.scalar(
        select(EscalationRequest)
        .where(
            EscalationRequest.incident_id == incident.id,
            EscalationRequest.state == "Pending",
        )
        .with_for_update()
    )
    if pending is not None:
        pending.state = "Superseded"
        pending.decided_at = utcnow()
        pending.decided_by = user.id
        pending.decision_reason = f"Incident became {incident.status}"
        event(
            db,
            incident,
            user,
            "escalation_superseded",
            {"escalation_id": str(pending.id), "reason": pending.decision_reason},
        )


@router.post("/incidents", status_code=201)
async def create_incident(
    payload: IncidentCreate, response: Response, db: Db, user: Actor
) -> dict[str, Any]:
    """Report a validated issue and allocate its PostgreSQL sequence number."""
    await _validate_selection(
        db, payload.building_id, payload.floor_id, payload.seat_id, payload.category_id
    )
    number = await db.scalar(text("SELECT nextval('incident_number_seq')"))
    incident = Incident(
        **payload.model_dump(),
        number=f"INC-{number:06d}",
        created_by=user.id,
        status="Open",
        priority=payload.requested_priority,
    )
    db.add(incident)
    await db.flush()
    event(db, incident, user, "created", {"status": "Open", "priority": incident.priority})
    return await _finish(db, incident, user, response)


@router.get("/incidents")
async def list_incidents(
    request: Request,
    db: Db,
    user: Actor,
    q: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    status: Annotated[list[Status] | None, Query()] = None,
    priority: Annotated[list[Priority] | None, Query()] = None,
    category_id: UUID | None = None,
    building_id: UUID | None = None,
    floor_id: UUID | None = None,
    seat_id: UUID | None = None,
    assigned_to: UUID | Literal["unassigned"] | None = None,
    created_by: UUID | None = None,
    escalation: EscalationState | None = None,
    scope: Literal["all", "mine", "assigned"] = "all",
    unassigned: bool = False,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    sort: Literal["created", "updated", "priority"] = "created",
    direction: Literal["asc", "desc"] = "desc",
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
        "sort",
        "direction",
        "page",
        "page_size",
    }
    if set(request.query_params) - accepted:
        raise HTTPException(422, "Unsupported incident filter")
    if created_by is not None:
        require_permission(is_admin(user))
    if unassigned and assigned_to not in {None, "unassigned"}:
        raise HTTPException(422, "Unassigned and assigned_to filters conflict")
    for bound in (date_from, date_to):
        if bound is not None and bound.tzinfo is None:
            raise HTTPException(422, "Date filters must include a timezone")
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "date_from must not exceed date_to")
    conditions = [readable_clause(user)]
    if scope == "mine":
        conditions.append(Incident.created_by == user.id)
    elif scope == "assigned":
        require_permission(is_admin(user) or "engineer" in (user.roles or []))
        conditions.append(Incident.assigned_to == user.id)
    if q:
        document = func.to_tsvector(
            literal_column("'english'"),
            Incident.title + " " + Incident.description,
        )
        conditions.append(
            or_(
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
        (Incident.assigned_to, assigned_to if assigned_to != "unassigned" else None),
        (Incident.created_by, created_by),
    ):
        if value is not None:
            conditions.append(field == value)
    if category_id is not None:
        children = select(Category.id).where(Category.parent_id == category_id)
        conditions.append(
            or_(Incident.category_id == category_id, Incident.category_id.in_(children))
        )
    if unassigned or assigned_to == "unassigned":
        conditions.append(Incident.assigned_to.is_(None))
    if date_from:
        conditions.append(Incident.created_at >= date_from)
    if date_to:
        conditions.append(Incident.created_at <= date_to)
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
            latest_state.is_(None) if escalation == "none" else latest_state == escalation
        )
    sort_column = {
        "created": Incident.created_at,
        "updated": Incident.updated_at,
        "priority": case(PRIORITY_RANK, value=Incident.priority, else_=0),
    }[sort]
    order = sort_column.asc() if direction == "asc" else sort_column.desc()
    tie_break = (
        [Incident.created_at.asc(), Incident.id.asc()]
        if sort == "priority"
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
    if not can_staff(user, incident) and incident.status != "Open":
        raise HTTPException(409, "Creators may edit details only while Open")
    check_match(incident, if_match)
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(422, "At least one detail field is required")
    if any(value is None for key, value in changes.items() if key != "seat_id"):
        raise HTTPException(422, "Required incident fields cannot be null")
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
    event(db, incident, user, "details_changed", {"fields": sorted(changes)})
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
    await _assign(db, incident, user, payload.assigned_to, payload.reason)
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
    touch(incident)
    event(
        db,
        incident,
        user,
        "priority_changed",
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
        payload.status in {"Closed", "Cancelled"}
        or payload.status == "In Progress"
        and incident.status == "Resolved"
    ):
        require_permission(is_admin(user) or creator)
    else:
        require_permission(staff)
    require_nonfinal(incident)
    check_match(incident, if_match)
    previous = incident.status
    target = payload.status
    valid = {
        ("Open", "In Progress"),
        ("Open", "Cancelled"),
        ("In Progress", "Blocked"),
        ("Blocked", "In Progress"),
        ("In Progress", "Resolved"),
        ("Resolved", "Closed"),
        ("Resolved", "In Progress"),
    }
    if (previous, target) not in valid:
        raise HTTPException(409, "Invalid incident transition")
    now = utcnow()
    extra: dict[str, Any] = {}
    if target == "Cancelled":
        if not is_admin(user) and incident.assigned_to is not None:
            raise HTTPException(403, "Creators may cancel only unassigned incidents")
        if not payload.reason:
            raise HTTPException(422, "Cancellation requires a reason")
        incident.cancelled_at = now
        extra["reason"] = payload.reason
    elif previous == "Open" and target == "In Progress":
        if incident.assigned_to is None:
            raise HTTPException(409, "Assign an engineer before starting work")
        incident.first_in_progress_at = incident.first_in_progress_at or now
        incident.acknowledged_at = incident.acknowledged_at or now
    elif target == "Blocked":
        if payload.reason not in BLOCKED_REASONS:
            raise HTTPException(422, "A valid blocked reason code is required")
        if not payload.note:
            raise HTTPException(422, "Blocking requires a note")
        note = await _add_note(db, incident, user, payload.note, payload.visibility)
        incident.blocked_reason = payload.reason
        incident.blocked_since = now
        extra.update(reason=payload.reason, note_id=str(note.id), visibility=payload.visibility)
    elif previous == "Blocked":
        extra.update(reason=incident.blocked_reason, blocked_since=_iso(incident.blocked_since))
        incident.blocked_reason = None
        incident.blocked_since = None
    elif target == "Resolved":
        if not payload.note or payload.visibility != "public":
            raise HTTPException(422, "Resolution requires a public note")
        note = await _add_note(db, incident, user, payload.note, "public")
        incident.resolved_at = now
        extra.update(
            note_id=str(note.id),
            assignee=str(incident.assigned_to),
            assigned_to=str(incident.assigned_to),
            visibility="public",
        )
    elif target == "Closed":
        incident.closed_at = now
    elif previous == "Resolved":
        if not payload.note or payload.visibility != "public":
            raise HTTPException(422, "Rejecting a resolution requires a public note")
        note = await _add_note(db, incident, user, payload.note, "public")
        extra.update(
            note_id=str(note.id),
            previous_resolved_at=_iso(incident.resolved_at),
            visibility="public",
        )
        incident.resolved_at = None
        incident.reopen_count += 1
    incident.status = target
    touch(incident)
    event(db, incident, user, "status_changed", {"from": previous, "to": target, **extra})
    if target in {"Closed", "Cancelled"}:
        await _supersede_pending(db, incident, user)
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
    if latest and latest.state == "Pending":
        raise HTTPException(409, "An escalation request is already pending")
    now = utcnow()
    if latest and now < _aware(latest.requested_at) + timedelta(hours=24):
        raise HTTPException(409, "Wait 24 hours after the prior request before escalating again")
    entry = EscalationRequest(
        incident_id=incident.id,
        requester_id=user.id,
        reason=payload.reason,
        requested_at=now,
        state="Pending",
    )
    db.add(entry)
    await db.flush()
    touch(incident)
    event(
        db,
        incident,
        user,
        "escalation_requested",
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
            EscalationRequest.state == "Pending",
        )
        .with_for_update()
    )
    if entry is None:
        raise HTTPException(409, "No pending escalation request")
    decision_note = None
    if payload.approved:
        if incident.priority == "Critical":
            if not payload.action_plan:
                raise HTTPException(422, "Critical approval requires a public action plan")
            if payload.priority not in {None, "Critical"}:
                raise HTTPException(422, "Critical escalation cannot lower priority")
            decision_note = await _add_note(db, incident, user, payload.action_plan, "public")
        else:
            if (
                payload.priority is None
                or PRIORITY_RANK[payload.priority] <= PRIORITY_RANK[incident.priority]
            ):
                raise HTTPException(422, "Approval must raise priority by at least one level")
            previous = incident.priority
            incident.priority = payload.priority
            event(
                db,
                incident,
                user,
                "priority_changed",
                {
                    "from": previous,
                    "to": incident.priority,
                    "reason": "Escalation approved",
                },
            )
            if payload.action_plan:
                decision_note = await _add_note(db, incident, user, payload.action_plan, "public")
        entry.state = "Approved"
    else:
        if not payload.decision_reason:
            raise HTTPException(422, "Rejection requires a public reason")
        if payload.priority is not None or payload.action_plan is not None:
            raise HTTPException(422, "Rejection accepts only its reason and optional reassignment")
        decision_note = await _add_note(db, incident, user, payload.decision_reason, "public")
        entry.state = "Rejected"
    if "assigned_to" in payload.model_fields_set:
        await _assign(
            db,
            incident,
            user,
            payload.assigned_to,
            "Escalation decision",
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
        "escalation_approved" if payload.approved else "escalation_rejected",
        {
            "escalation_id": str(entry.id),
            "reason": payload.decision_reason,
            "priority": incident.priority,
            "note_id": str(decision_note.id) if decision_note else None,
            "visibility": "public",
        },
    )
    return await _finish(db, incident, user, response)


@router.get("/incidents/{incident_id}/notes")
async def list_notes(incident_id: UUID, db: Db, user: Actor) -> dict[str, Any]:
    incident = await load_incident(db, user, incident_id)
    statement = select(IncidentNote).where(IncidentNote.incident_id == incident.id)
    if not can_staff(user, incident):
        statement = statement.where(IncidentNote.visibility == "public")
    notes = (await db.scalars(statement.order_by(IncidentNote.created_at, IncidentNote.id))).all()
    return {"items": [note_projection(note) for note in notes], "total": len(notes)}


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
    require_permission(payload.visibility == "public" or can_staff(user, incident))
    require_nonfinal(incident)
    check_match(incident, if_match)
    note = await _add_note(db, incident, user, payload.body, payload.visibility)
    touch(incident)
    await db.flush()
    result = note_projection(note)
    await db.commit()
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
    if note is None or (note.visibility == "internal" and not can_staff(user, incident)):
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
    require_permission(note.author_id == user.id)
    require_permission(note.visibility == "public" or can_staff(user, incident))
    require_nonfinal(incident)
    check_match(note, if_match)
    if note.hidden_at is not None:
        raise HTTPException(409, "Hidden notes cannot be edited")
    if utcnow() >= _aware(note.created_at) + timedelta(minutes=10):
        raise HTTPException(409, "The ten-minute note edit window has expired")
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
        "note_edited",
        {
            "note_id": str(note.id),
            "visibility": note.visibility,
            "previous_body": previous_body,
            "body": note.body,
        },
    )
    await db.flush()
    result = note_projection(note)
    await db.commit()
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
            "note_hidden",
            {
                "note_id": str(note.id),
                "visibility": note.visibility,
                "reason": payload.reason,
            },
        )
    await db.flush()
    result = note_projection(note)
    await db.commit()
    response.headers["ETag"] = f'"{result["updated_at"]}"'
    return result


@router.get("/incidents/{incident_id}/timeline")
async def get_timeline(incident_id: UUID, db: Db, user: Actor) -> dict[str, Any]:
    """Return sanitized events, checking each referenced note's current state."""
    incident = await load_incident(db, user, incident_id)
    staff = can_staff(user, incident)
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
    }
    items = []
    for entry in events:
        stored = entry.payload or {}
        note = by_id.get(str(stored.get("note_id")))
        if entry.type.startswith("note_") and (
            note is None or (note.visibility == "internal" and not staff)
        ):
            continue
        payload = {key: value for key, value in stored.items() if key in safe_keys}
        if stored.get("visibility") == "internal" and not staff:
            # Workflow status itself is public; its note identity/visibility is not.
            payload.pop("note_id", None)
            payload.pop("visibility", None)
        if note is not None and note.hidden_at is not None:
            payload["hidden"] = True
        # Hide reasons are retained for admin auditing, not public disclosure.
        if entry.type == "note_hidden" and not is_admin(user):
            payload.pop("reason", None)
        if entry.type in {"escalation_approved", "escalation_rejected"}:
            # Decision prose is shown through its hide-aware public note or
            # escalation projection, never duplicated in ordinary audit events.
            payload.pop("reason", None)
        items.append(
            {
                "id": str(entry.id),
                "incident_id": str(entry.incident_id),
                "actor_id": str(entry.actor_id) if entry.actor_id else None,
                "type": entry.type,
                "payload": payload,
                "created_at": _iso(entry.created_at),
                **(
                    {"note": note_projection(note)}
                    if entry.type == "note_added" and note is not None
                    else {}
                ),
            }
        )
    return {"items": items, "total": len(items)}
