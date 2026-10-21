"""Idempotent reference/bootstrap seed; synthetic demo records require an explicit flag."""

import argparse
import asyncio
import os
from datetime import timedelta
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import func, select, text

from facility.config import settings
from facility.database import SessionLocal
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
from facility.security import hash_password

CATEGORIES = {
    "IT and AV equipment": [
        "Display/monitor",
        "Conference room AV",
        "Cables and adapters",
        "Docking station",
        "Printer/scanner",
        "Laptop/peripheral",
    ],
    "Network and Wi-Fi": ["No connectivity", "Slow connection", "Wired port dead"],
    "Furniture": ["Desk/chair broken", "Ergonomic request"],
    "HVAC and temperature": ["Too hot", "Too cold", "Air quality"],
    "Electrical and lighting": ["Light out", "Power outlet", "Flickering"],
    "Plumbing and restrooms": ["Leak", "Clog", "Supplies"],
    "Cleaning": ["Spill", "Trash", "General cleaning"],
    "Access and security": ["Badge reader", "Door/lock", "Lost and found"],
    "Other": [],
}


def fixed_id(label: str):
    """Assign stable synthetic identifiers without colliding with application UUID4 IDs."""
    return uuid5(NAMESPACE_URL, "acme-facility-demo/" + label)


async def seed(include_demo: bool = False) -> dict:
    """Insert missing categories/admin and optionally demo data without resetting records."""
    async with SessionLocal() as db:
        await db.execute(text("SELECT pg_advisory_xact_lock(624030120)"))
        added = 0
        for order, (name, children) in enumerate(CATEGORIES.items()):
            parent = await db.scalar(
                select(Category).where(Category.name == name, Category.parent_id.is_(None))
            )
            if parent is None:
                parent = Category(id=fixed_id("category/" + name), name=name, sort_order=order)
                db.add(parent)
                await db.flush()
                added += 1
            for child_order, child_name in enumerate(children):
                existing = await db.scalar(
                    select(Category).where(
                        Category.parent_id == parent.id, Category.name == child_name
                    )
                )
                if existing is None:
                    db.add(
                        Category(
                            id=fixed_id("category/" + name + "/" + child_name),
                            name=child_name,
                            parent_id=parent.id,
                            sort_order=child_order,
                        )
                    )
                    added += 1
        await db.flush()
        admins = await db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.roles.contains(["admin"]), User.is_active.is_(True))
        )
        email = os.environ.get("SEED_ADMIN_EMAIL", "").strip().lower()
        password = os.environ.get("SEED_ADMIN_PASSWORD", "")
        if not admins and email:
            import re

            if not re.fullmatch(r"[^@\s]+@acme\.inc", email, re.IGNORECASE):
                raise ValueError("SEED_ADMIN_EMAIL must use @acme.inc")
            if await db.scalar(select(User.id).where(User.email == email)):
                raise ValueError("Bootstrap email already exists; no automatic privilege elevation")
            if (
                len(password) < 10
                or not any(c.isalpha() for c in password)
                or not any(c.isdigit() for c in password)
                or len(password.encode()) > 72
            ):
                raise ValueError("SEED_ADMIN_PASSWORD does not satisfy password rules")
            db.add(
                User(
                    email=email,
                    full_name="Facility Administrator",
                    roles=["admin"],
                    password_hash=hash_password(password),
                )
            )
            added += 1
        if include_demo:
            if not settings.is_local or not settings.resolved_database_url().endswith(
                "/acme_facility_dev"
            ):
                raise ValueError("Demo seed is restricted to local acme_facility_dev")
            added += await seed_demo(db)
        await db.commit()
        return {"inserted": added, "demo": include_demo}


async def seed_demo(db) -> int:
    """Build recognizable demo personas and a representative workflow history once."""
    added = 0
    accounts = [
        ("admin@acme.inc", "Alex Morgan", ["admin", "engineer"]),
        ("employee@acme.inc", "Jamie Chen", []),
        ("engineer@acme.inc", "Sam Rivera", ["engineer"]),
        ("employee2@acme.inc", "Taylor Park", []),
    ]
    users = {}
    for email, name, roles in accounts:
        user = await db.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(
                id=fixed_id(email),
                email=email,
                full_name=name,
                roles=roles,
                password_hash=hash_password(os.environ.get("DEMO_PASSWORD", "DemoPass123!")),
            )
            db.add(user)
            await db.flush()
            added += 1
        users[email] = user
        if "engineer" in user.roles and not await db.scalar(
            select(EngineerProfile.id).where(EngineerProfile.user_id == user.id)
        ):
            db.add(EngineerProfile(user_id=user.id, availability="Available"))
            added += 1
    for code, name in [("HQ1", "ACME Headquarters"), ("WEST", "West Campus")]:
        building = await db.scalar(select(Building).where(Building.code == code))
        if building is None:
            building = Building(
                id=fixed_id("building/" + code),
                name=name,
                code=code,
                address="Synthetic workshop address",
            )
            db.add(building)
            await db.flush()
            added += 1
        for level in [1, 2]:
            floor = await db.scalar(
                select(Floor).where(Floor.building_id == building.id, Floor.level_number == level)
            )
            if floor is None:
                floor = Floor(
                    id=fixed_id(f"floor/{code}/{level}"),
                    building_id=building.id,
                    level_number=level,
                    name=f"Floor {level}",
                )
                db.add(floor)
                await db.flush()
                added += 1
            for number in range(1, 4):
                seat_code = f"{level}-A-{number:02d}"
                if not await db.scalar(
                    select(Seat.id).where(Seat.floor_id == floor.id, Seat.code == seat_code)
                ):
                    db.add(
                        Seat(
                            id=fixed_id(f"seat/{code}/{seat_code}"),
                            floor_id=floor.id,
                            code=seat_code,
                        )
                    )
                    added += 1
    await db.flush()
    floor = await db.get(Floor, fixed_id("floor/HQ1/1"))
    # Respect existing user-edited reference data: a missing synthetic floor skips tickets.
    if floor is None:
        return added
    category = await db.scalar(select(Category).where(Category.name == "Display/monitor"))
    creator = users["employee@acme.inc"]
    assignee = users["engineer@acme.inc"]
    now = utcnow()
    statuses = ["Open", "Open", "In Progress", "Blocked", "Resolved", "Closed", "Cancelled"]
    for index, status in enumerate(statuses):
        ticket_id = fixed_id(f"incident/{index}")
        if await db.get(Incident, ticket_id):
            continue
        created = now - timedelta(days=3, hours=index)
        sequence = await db.scalar(text("SELECT nextval('incident_number_seq')"))
        ticket = Incident(
            id=ticket_id,
            number=f"INC-{sequence:06d}",
            title=f"Demo: workspace display issue {index + 1}",
            description="Synthetic workshop incident: the desk display needs inspection.",
            category_id=category.id,
            building_id=floor.building_id,
            floor_id=floor.id,
            seat_id=fixed_id("seat/HQ1/1-A-01"),
            created_by=creator.id,
            priority=["Critical", "High", "Medium", "Low"][index % 4],
            requested_priority="Medium",
            status=status,
            created_at=created,
            updated_at=now,
            reopen_count=0,
        )
        if status not in ["Open", "Cancelled"]:
            ticket.assigned_to = assignee.id
            ticket.assigned_at = created + timedelta(hours=1)
            ticket.acknowledged_at = created + timedelta(hours=2)
            ticket.first_in_progress_at = ticket.acknowledged_at
        if status == "Blocked":
            ticket.blocked_reason = "Waiting on parts"
            ticket.blocked_since = created + timedelta(hours=3)
        if status in ["Resolved", "Closed"]:
            ticket.resolved_at = created + timedelta(hours=5)
        if status == "Closed":
            ticket.closed_at = created + timedelta(hours=6)
        if status == "Cancelled":
            ticket.cancelled_at = created + timedelta(hours=1)
        db.add(ticket)
        await db.flush()
        db.add(
            IncidentEvent(
                incident_id=ticket.id,
                actor_id=creator.id,
                type="created",
                payload={"status": "Open"},
                created_at=created,
            )
        )
        if ticket.assigned_to:
            db.add(
                IncidentEvent(
                    incident_id=ticket.id,
                    actor_id=users["admin@acme.inc"].id,
                    type="assigned",
                    payload={"to": str(assignee.id)},
                    created_at=ticket.assigned_at,
                )
            )
            db.add(
                IncidentEvent(
                    incident_id=ticket.id,
                    actor_id=assignee.id,
                    type="status_changed",
                    payload={"from": "Open", "to": "In Progress"},
                    created_at=ticket.first_in_progress_at,
                )
            )
        if status in ["Blocked", "Resolved", "Closed"]:
            intermediate = "Resolved" if status in ["Resolved", "Closed"] else "Blocked"
            db.add(
                IncidentEvent(
                    incident_id=ticket.id,
                    actor_id=assignee.id,
                    type="status_changed",
                    payload={
                        "from": "In Progress",
                        "to": intermediate,
                        "assigned_to": str(assignee.id),
                    },
                    created_at=ticket.resolved_at or ticket.blocked_since,
                )
            )
        if status in ["Closed", "Cancelled"]:
            db.add(
                IncidentEvent(
                    incident_id=ticket.id,
                    actor_id=creator.id,
                    type="status_changed",
                    payload={"from": "Resolved" if status == "Closed" else "Open", "to": status},
                    created_at=ticket.closed_at or ticket.cancelled_at,
                )
            )
        if status != "Cancelled":
            public = IncidentNote(
                incident_id=ticket.id,
                author_id=assignee.id if ticket.assigned_to else creator.id,
                body="Display replaced and verified."
                if ticket.resolved_at
                else "The issue has been recorded; updates will appear here.",
                visibility="public",
                created_at=ticket.resolved_at or created + timedelta(minutes=15),
            )
            db.add(public)
            await db.flush()
            db.add(
                IncidentEvent(
                    incident_id=ticket.id,
                    actor_id=public.author_id,
                    type="note_added",
                    payload={"note_id": str(public.id), "visibility": "public"},
                    created_at=public.created_at,
                )
            )
            internal = IncidentNote(
                incident_id=ticket.id,
                author_id=users["admin@acme.inc"].id,
                body="Internal synthetic diagnostic note.",
                visibility="internal",
            )
            db.add(internal)
        if index == 0:
            db.add(
                EscalationRequest(
                    incident_id=ticket.id,
                    requester_id=creator.id,
                    reason="Work cannot continue until the display is fixed.",
                    state="Pending",
                    requested_at=now - timedelta(hours=1),
                )
            )
        added += 1
    return added


def main() -> None:
    """Run explicit reference/bootstrap seed, optionally adding local demo records."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args()
    print(asyncio.run(seed(include_demo=args.demo)))


if __name__ == "__main__":
    main()
