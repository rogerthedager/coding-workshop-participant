"""Facility hierarchy and category administration; all records are retained."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from facility.database import get_db
from facility.dependencies import current_user, require_admin
from facility.enums import FINAL_STATUSES
from facility.errors import fail
from facility.models import Building, Category, CategoryGroup, Floor, Incident, Seat, User

router = APIRouter(tags=["facilities"], dependencies=[Depends(current_user)])


class Input(BaseModel):
    """Reject misspelled or unauthorized input fields."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class BuildingCreate(Input):
    """A new active building."""

    name: str = Field(min_length=1, max_length=120)
    code: str = Field(min_length=1, max_length=30)
    address: str | None = Field(default=None, max_length=500)


class BuildingUpdate(Input):
    """Editable building attributes."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    code: str | None = Field(default=None, min_length=1, max_length=30)
    address: str | None = Field(default=None, max_length=500)
    is_active: bool | None = None


class FloorCreate(Input):
    """A floor with a unique level within a building."""

    name: str = Field(min_length=1, max_length=120)
    level_number: int = Field(ge=-50, le=200)


class FloorUpdate(Input):
    """Editable floor fields; parent remains immutable."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    level_number: int | None = Field(default=None, ge=-50, le=200)
    is_active: bool | None = None


class SeatCreate(Input):
    """A seat within a floor."""

    code: str = Field(min_length=1, max_length=40)


class SeatBulkCreate(Input):
    """Create prefix + zero-padded numbers for every number in [from, to]."""

    prefix: str = Field(default="", max_length=30)
    start: int = Field(alias="from", ge=0, le=99999)
    end: int = Field(alias="to", ge=0, le=99999)
    pad: int = Field(default=2, ge=0, le=6)


class SeatUpdate(Input):
    """Editable seat fields."""

    code: str | None = Field(default=None, min_length=1, max_length=40)
    is_active: bool | None = None


class CategoryCreate(Input):
    """A top-level or child category."""

    name: str = Field(min_length=1, max_length=120)
    parent_id: UUID | None = None
    group_id: UUID | None = None
    sort_order: int = Field(default=0, ge=0, le=10000)


class CategoryUpdate(Input):
    """Category parent is immutable after creation."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    sort_order: int | None = Field(default=None, ge=0, le=10000)
    is_active: bool | None = None
    group_id: UUID | None = None


def record(value: Any) -> dict:
    """Serialize reference data without ORM lazy loading."""
    return {column.name: getattr(value, column.name) for column in value.__table__.columns}


async def get_locked(db: AsyncSession, model: Any, item_id: UUID) -> Any:
    """Find and lock a reference record until the transaction completes."""
    item = await db.scalar(
        select(model)
        .where(model.id == item_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if item is None:
        raise HTTPException(404, "Record not found")
    return item


async def guard_location(db: AsyncSession, condition: Any) -> None:
    """Disallow location deactivation while any non-final ticket references it."""
    count = await db.scalar(
        select(func.count())
        .select_from(Incident)
        .where(condition, Incident.status.not_in(FINAL_STATUSES))
    )
    if count:
        raise fail(409, "has_open_incidents", "Location has non-final incidents", count=count)


async def ensure_unique(
    db: AsyncSession, column: Any, value: str | None, *scope: Any, exclude: UUID | None = None
) -> None:
    """Case-insensitive uniqueness, as the design specifies (`lower(code)` indexes)."""
    if value is None:
        return
    statement = select(column.class_.id).where(func.lower(column) == value.lower(), *scope)
    if exclude is not None:
        statement = statement.where(column.class_.id != exclude)
    if await db.scalar(statement.limit(1)):
        raise fail(409, "duplicate", f"That {column.key} is already in use", field=column.key)


def siblings(parent_id: UUID | None) -> Any:
    """Categories that share a parent (or are all top-level)."""
    return Category.parent_id.is_(None) if parent_id is None else Category.parent_id == parent_id


def like_pattern(text_value: str) -> str:
    """Treat % and _ in search text literally."""
    escaped = text_value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


async def check_group(db: AsyncSession, group_id: UUID | None) -> None:
    """A category may only join an existing, active category group."""
    if group_id is None:
        return
    group = await db.get(CategoryGroup, group_id)
    if group is None:
        raise HTTPException(422, "Category group not found")
    if not group.is_active:
        raise fail(422, "group_inactive", "Choose an active category group")


def update_fields(item: Any, payload: BaseModel, skip: frozenset | set = frozenset()) -> None:
    """Apply supplied fields; reject null on required attributes."""
    for key, value in payload.model_dump(exclude_unset=True).items():
        if key in skip:
            continue
        if value is None and key != "address":
            raise HTTPException(422, f"{key} cannot be null")
        setattr(item, key, value)


@router.get("/buildings")
async def list_buildings(
    q: str = "", page: int = 1, page_size: int = 25, db: AsyncSession = Depends(get_db)
) -> dict:
    """List buildings, including inactive records for historical administration."""
    if page < 1 or not 1 <= page_size <= 100:
        raise HTTPException(422, "Invalid pagination")
    pattern = like_pattern(q)
    condition = Building.name.ilike(pattern, escape="\\") | Building.code.ilike(
        pattern, escape="\\"
    )
    total = await db.scalar(select(func.count()).select_from(Building).where(condition))
    rows = (
        await db.scalars(
            select(Building)
            .where(condition)
            .order_by(Building.name, Building.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return {
        "items": [record(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post("/buildings", status_code=201)
async def create_building(
    payload: BuildingCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
) -> dict:
    """Create a building without overwriting existing reference data."""
    await ensure_unique(db, Building.name, payload.name)
    await ensure_unique(db, Building.code, payload.code)
    item = Building(**payload.model_dump())
    db.add(item)
    await db.commit()
    return record(item)


@router.patch("/buildings/{item_id}")
async def patch_building(
    item_id: UUID,
    payload: BuildingUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
) -> dict:
    """Edit or logically deactivate a building."""
    item = await get_locked(db, Building, item_id)
    if payload.is_active is False:
        await guard_location(db, Incident.building_id == item_id)
    await ensure_unique(db, Building.name, payload.name, exclude=item_id)
    await ensure_unique(db, Building.code, payload.code, exclude=item_id)
    update_fields(item, payload)
    await db.commit()
    return record(item)


@router.get("/buildings/{building_id}/floors")
async def list_floors(building_id: UUID, db: AsyncSession = Depends(get_db)) -> dict:
    """Read floor reference records for a building."""
    if await db.get(Building, building_id) is None:
        raise HTTPException(404, "Building not found")
    rows = (
        await db.scalars(
            select(Floor)
            .where(Floor.building_id == building_id)
            .order_by(Floor.level_number, Floor.id)
        )
    ).all()
    return {
        "items": [record(row) for row in rows],
        "total": len(rows),
        "page": 1,
        "page_size": len(rows),
    }


@router.post("/buildings/{building_id}/floors", status_code=201)
async def create_floor(
    building_id: UUID,
    payload: FloorCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
) -> dict:
    """Add a floor only beneath an active building."""
    parent = await get_locked(db, Building, building_id)
    if not parent.is_active:
        raise HTTPException(409, "Building is inactive")
    item = Floor(building_id=building_id, **payload.model_dump())
    db.add(item)
    await db.commit()
    return record(item)


@router.patch("/floors/{item_id}")
async def patch_floor(
    item_id: UUID,
    payload: FloorUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
) -> dict:
    """Update a floor, locking its ancestor before its own row."""
    existing = await db.get(Floor, item_id)
    if existing is None:
        raise HTTPException(404, "Floor not found")
    parent = await get_locked(db, Building, existing.building_id)
    item = await get_locked(db, Floor, item_id)
    if payload.is_active is True and not parent.is_active:
        raise HTTPException(409, "Reactivate building first")
    if payload.is_active is False:
        await guard_location(db, Incident.floor_id == item_id)
    update_fields(item, payload)
    await db.commit()
    return record(item)


@router.get("/floors/{floor_id}/seats")
async def list_seats(floor_id: UUID, db: AsyncSession = Depends(get_db)) -> dict:
    """Read seats for a known floor."""
    if await db.get(Floor, floor_id) is None:
        raise HTTPException(404, "Floor not found")
    rows = (
        await db.scalars(select(Seat).where(Seat.floor_id == floor_id).order_by(Seat.code, Seat.id))
    ).all()
    return {
        "items": [record(row) for row in rows],
        "total": len(rows),
        "page": 1,
        "page_size": len(rows),
    }


@router.post("/floors/{floor_id}/seats", status_code=201)
async def create_seat(
    floor_id: UUID,
    payload: SeatCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
) -> dict:
    """Add a seat under active ancestors."""
    existing = await db.get(Floor, floor_id)
    if existing is None:
        raise HTTPException(404, "Floor not found")
    building = await get_locked(db, Building, existing.building_id)
    floor = await get_locked(db, Floor, floor_id)
    if not building.is_active or not floor.is_active:
        raise HTTPException(409, "Location is inactive")
    await ensure_unique(db, Seat.code, payload.code, Seat.floor_id == floor_id)
    item = Seat(floor_id=floor_id, **payload.model_dump())
    db.add(item)
    await db.commit()
    return record(item)


@router.post("/floors/{floor_id}/seats/bulk", status_code=201)
async def bulk_create_seats(
    floor_id: UUID,
    payload: SeatBulkCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
) -> dict:
    """Create up to 200 seats at once; any collision creates nothing."""
    if payload.end < payload.start or payload.end - payload.start + 1 > 200:
        raise HTTPException(422, "Use from <= to and at most 200 seats per call")
    existing = await db.get(Floor, floor_id)
    if existing is None:
        raise HTTPException(404, "Floor not found")
    building = await get_locked(db, Building, existing.building_id)
    floor = await get_locked(db, Floor, floor_id)
    if not building.is_active or not floor.is_active:
        raise HTTPException(409, "Location is inactive")
    codes = [
        f"{payload.prefix}{number:0{payload.pad}d}"
        for number in range(payload.start, payload.end + 1)
    ]
    if any(len(code) > 40 for code in codes):
        raise HTTPException(422, "Seat codes must be at most 40 characters")
    taken = (
        await db.scalars(
            select(Seat.code).where(
                Seat.floor_id == floor_id,
                func.lower(Seat.code).in_([code.lower() for code in codes]),
            )
        )
    ).all()
    if taken:
        raise fail(409, "duplicate", "Some seat codes already exist", codes=sorted(taken))
    items = [Seat(floor_id=floor_id, code=code) for code in codes]
    db.add_all(items)
    await db.commit()
    return {"items": [record(item) for item in items], "total": len(items)}


@router.patch("/seats/{item_id}")
async def patch_seat(
    item_id: UUID,
    payload: SeatUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
) -> dict:
    """Update or deactivate a seat without moving its parent."""
    existing = await db.get(Seat, item_id)
    if existing is None:
        raise HTTPException(404, "Seat not found")
    floor = await db.get(Floor, existing.floor_id)
    building = await get_locked(db, Building, floor.building_id)
    floor = await get_locked(db, Floor, floor.id)
    item = await get_locked(db, Seat, item_id)
    if payload.is_active is True and (not building.is_active or not floor.is_active):
        raise HTTPException(409, "Reactivate ancestors first")
    if payload.is_active is False:
        await guard_location(db, Incident.seat_id == item_id)
    await ensure_unique(
        db, Seat.code, payload.code, Seat.floor_id == item.floor_id, exclude=item_id
    )
    update_fields(item, payload)
    await db.commit()
    return record(item)


@router.get("/locations/tree")
async def location_tree(active_only: bool = True, db: AsyncSession = Depends(get_db)) -> dict:
    """Build the selector tree with inactive subtrees excluded by default."""
    buildings = (await db.scalars(select(Building).order_by(Building.name))).all()
    floors = (await db.scalars(select(Floor).order_by(Floor.level_number))).all()
    seats = (await db.scalars(select(Seat).order_by(Seat.code))).all()
    output = []
    for building in buildings:
        if active_only and not building.is_active:
            continue
        item = record(building)
        item["floors"] = []
        for floor in floors:
            if floor.building_id != building.id or (active_only and not floor.is_active):
                continue
            child = record(floor)
            child["seats"] = [
                record(seat)
                for seat in seats
                if seat.floor_id == floor.id and (not active_only or seat.is_active)
            ]
            item["floors"].append(child)
        output.append(item)
    return {"items": output, "total": len(output), "page": 1, "page_size": len(output)}


@router.get("/categories")
async def list_categories(active_only: bool = False, db: AsyncSession = Depends(get_db)) -> dict:
    """Return categories with optional active-ancestor filtering."""
    rows = (
        await db.scalars(select(Category).order_by(Category.sort_order, Category.name, Category.id))
    ).all()
    active_parents = {row.id for row in rows if row.is_active and row.parent_id is None}
    items = [
        record(row)
        for row in rows
        if not active_only
        or (row.is_active and (row.parent_id is None or row.parent_id in active_parents))
    ]
    return {"items": items, "total": len(items), "page": 1, "page_size": len(items)}


@router.post("/categories", status_code=201)
async def create_category(
    payload: CategoryCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
) -> dict:
    """Create a category with at most two hierarchy levels."""
    if payload.parent_id:
        parent = await get_locked(db, Category, payload.parent_id)
        if parent.parent_id is not None or not parent.is_active:
            raise HTTPException(409, "Parent must be an active top-level category")
        if payload.group_id is not None:
            raise HTTPException(422, "Subcategories inherit their parent's group")
    await check_group(db, payload.group_id)
    await ensure_unique(db, Category.name, payload.name, siblings(payload.parent_id))
    item = Category(**payload.model_dump())
    db.add(item)
    await db.commit()
    return record(item)


@router.patch("/categories/{item_id}")
async def patch_category(
    item_id: UUID,
    payload: CategoryUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
) -> dict:
    """Edit or deactivate a category, preserving historical references."""
    existing = await db.get(Category, item_id)
    if existing is None:
        raise HTTPException(404, "Category not found")
    if existing.parent_id:
        parent = await get_locked(db, Category, existing.parent_id)
        if payload.is_active is True and not parent.is_active:
            raise HTTPException(409, "Reactivate parent category first")
    item = await get_locked(db, Category, item_id)
    await ensure_unique(
        db, Category.name, payload.name, siblings(item.parent_id), exclude=item_id
    )
    if "group_id" in payload.model_fields_set:
        if item.parent_id is not None:
            raise HTTPException(422, "Subcategories inherit their parent's group")
        await check_group(db, payload.group_id)
        item.group_id = payload.group_id
    update_fields(item, payload.model_copy(update={"group_id": None}), skip={"group_id"})
    await db.commit()
    return record(item)
