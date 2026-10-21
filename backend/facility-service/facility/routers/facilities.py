"""Facility hierarchy and category administration; all records are retained."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from facility.database import get_db
from facility.dependencies import current_user, require_admin
from facility.models import Building, Category, Floor, Incident, Seat, User

router = APIRouter(tags=["facilities"], dependencies=[Depends(current_user)])
FINAL = ["Closed", "Cancelled"]


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


class SeatUpdate(Input):
    """Editable seat fields."""

    code: str | None = Field(default=None, min_length=1, max_length=40)
    is_active: bool | None = None


class CategoryCreate(Input):
    """A top-level or child category."""

    name: str = Field(min_length=1, max_length=120)
    parent_id: UUID | None = None
    sort_order: int = Field(default=0, ge=0, le=10000)


class CategoryUpdate(Input):
    """Category parent is immutable after creation."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    sort_order: int | None = Field(default=None, ge=0, le=10000)
    is_active: bool | None = None


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
        select(func.count()).select_from(Incident).where(condition, Incident.status.not_in(FINAL))
    )
    if count:
        raise HTTPException(409, {"message": "Location has non-final incidents", "count": count})


def update_fields(item: Any, payload: BaseModel) -> None:
    """Apply supplied fields; reject null on required attributes."""
    for key, value in payload.model_dump(exclude_unset=True).items():
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
    condition = Building.name.ilike(f"%{q}%") | Building.code.ilike(f"%{q}%")
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
    item = Seat(floor_id=floor_id, **payload.model_dump())
    db.add(item)
    await db.commit()
    return record(item)


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
    update_fields(item, payload)
    await db.commit()
    return record(item)
