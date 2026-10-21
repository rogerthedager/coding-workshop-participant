"""Idempotent reference/bootstrap seed; synthetic demo records require an explicit flag."""

import argparse
import asyncio
import os
import random
from datetime import timedelta
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import func, select, text

from facility.config import settings
from facility.database import SessionLocal
from facility.enums import (
    FINAL_STATUSES,
    BlockedReason,
    EngineerAvailability,
    EngineerSkill,
    EscalationState,
    IncidentEventType,
    IncidentPriority,
    IncidentStatus,
    NoteVisibility,
    UserRole,
)
from facility.models import (
    Building,
    Category,
    CategoryGroup,
    EngineerProfile,
    EscalationRequest,
    Floor,
    Incident,
    IncidentEvent,
    IncidentNote,
    Seat,
    Team,
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


# Routing groups (design section 6): group -> top-level categories.
GROUPS = {
    "IT and AV": ["IT and AV equipment", "Network and Wi-Fi"],
    "Infrastructure": [
        "Electrical and lighting",
        "HVAC and temperature",
        "Plumbing and restrooms",
    ],
    "Workplace services": ["Furniture", "Cleaning"],
    "Security": ["Access and security"],
    "Other": ["Other"],
}

S = EngineerSkill
# Demo teams: (team, owned group, lead, [(engineer email, name, skills, availability)]).
DEMO_TEAMS = [
    (
        "IT Support",
        "IT and AV",
        ("it.lead@acme.inc", "Priya Shah"),
        [
            ("engineer@acme.inc", "Sam Rivera", [S.IT_AV_EQUIPMENT, S.NETWORK_WIFI], "Available"),
            ("it.eng2@acme.inc", "Jordan Lee", [S.NETWORK_WIFI, S.IT_AV_EQUIPMENT], "Busy"),
        ],
    ),
    (
        "Infrastructure",
        "Infrastructure",
        ("infra.lead@acme.inc", "Morgan Diaz"),
        [
            (
                "infra.eng@acme.inc",
                "Casey Nguyen",
                [S.ELECTRICAL_LIGHTING, S.HVAC_TEMPERATURE],
                "Available",
            ),
            (
                "infra.eng2@acme.inc",
                "Rowan Ellis",
                [S.PLUMBING_RESTROOMS, S.HVAC_TEMPERATURE],
                "Available",
            ),
        ],
    ),
    (
        "Workplace Services",
        "Workplace services",
        ("workplace.lead@acme.inc", "Riley Brooks"),
        [("workplace.eng@acme.inc", "Avery Kim", [S.FURNITURE, S.CLEANING], "Available")],
    ),
    (
        "Security",
        "Security",
        ("security.lead@acme.inc", "Drew Patel"),
        [
            (
                "security.eng@acme.inc",
                "Quinn Foster",
                [S.ACCESS_SECURITY, S.ELECTRICAL_LIGHTING],
                "Available",
            )
        ],
    ),
]


async def seed_groups(db) -> int:
    """Create missing routing groups and file ungrouped top-level categories into them."""
    added = 0
    for order, (name, members) in enumerate(GROUPS.items()):
        group = await db.scalar(select(CategoryGroup).where(CategoryGroup.name == name))
        if group is None:
            group = CategoryGroup(id=fixed_id("group/" + name), name=name, sort_order=order)
            db.add(group)
            await db.flush()
            added += 1
        for category in await db.scalars(
            select(Category).where(
                Category.name.in_(members),
                Category.parent_id.is_(None),
                Category.group_id.is_(None),
            )
        ):
            category.group_id = group.id
    await db.flush()
    return added


async def seed_teams(db) -> int:
    """Demo teams, leads and skilled engineers; existing records are left as they are."""
    added = 0
    password = hash_password(os.environ.get("DEMO_PASSWORD", "DemoPass123!"))
    for team_name, group_name, (lead_email, lead_name), engineers in DEMO_TEAMS:
        lead = await db.scalar(select(User).where(User.email == lead_email))
        if lead is None:
            lead = User(
                id=fixed_id(lead_email),
                email=lead_email,
                full_name=lead_name,
                roles=[UserRole.LEAD],
                password_hash=password,
            )
            db.add(lead)
            await db.flush()
            added += 1
        team = await db.scalar(select(Team).where(Team.name == team_name))
        if team is None:
            if await db.scalar(select(Team.id).where(Team.lead_user_id == lead.id)):
                continue
            team = Team(id=fixed_id("team/" + team_name), name=team_name, lead_user_id=lead.id)
            if UserRole.LEAD not in lead.roles:
                lead.roles = sorted(set(lead.roles) | {UserRole.LEAD})
            db.add(team)
            await db.flush()
            added += 1
        group = await db.scalar(select(CategoryGroup).where(CategoryGroup.name == group_name))
        if group is not None and group.team_id is None:
            group.team_id = team.id
        for email, name, skills, availability in engineers:
            user = await db.scalar(select(User).where(User.email == email))
            if user is None:
                user = User(
                    id=fixed_id(email),
                    email=email,
                    full_name=name,
                    roles=[UserRole.ENGINEER],
                    password_hash=password,
                )
                db.add(user)
                await db.flush()
                added += 1
            profile = await db.scalar(
                select(EngineerProfile).where(EngineerProfile.user_id == user.id)
            )
            if profile is None:
                profile = EngineerProfile(user_id=user.id, availability=availability)
                db.add(profile)
                added += 1
            if not profile.skills:
                profile.skills = skills
            if profile.team_id is None:
                profile.team_id = team.id
    admin = await db.scalar(select(User).where(User.email == "admin@acme.inc"))
    if admin is not None:
        profile = await db.scalar(select(EngineerProfile).where(EngineerProfile.user_id == admin.id))
        if profile is not None and not profile.skills:
            profile.skills = [S.IT_AV_EQUIPMENT, S.ELECTRICAL_LIGHTING]
    await db.flush()
    return added


def fixed_id(label: str):
    """Assign stable synthetic identifiers without colliding with application UUID4 IDs."""
    return uuid5(NAMESPACE_URL, "acme-facility-demo/" + label)


async def seed(include_demo: bool = False, incidents: int = 0) -> dict:
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
        added += await seed_groups(db)
        admins = await db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.roles.contains([UserRole.ADMIN]), User.is_active.is_(True))
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
                    roles=[UserRole.ADMIN],
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
            await db.flush()
            added += await seed_teams(db)
            if incidents:
                await db.flush()
                added += await seed_bulk_incidents(db, incidents)
        await db.commit()
        return {"inserted": added, "demo": include_demo, "incidents": incidents}


async def seed_demo(db) -> int:
    """Build recognizable demo personas and a representative workflow history once."""
    added = 0
    accounts = [
        ("admin@acme.inc", "Alex Morgan", [UserRole.ADMIN, UserRole.ENGINEER]),
        ("employee@acme.inc", "Jamie Chen", []),
        ("engineer@acme.inc", "Sam Rivera", [UserRole.ENGINEER]),
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
        if UserRole.ENGINEER in user.roles and not await db.scalar(
            select(EngineerProfile.id).where(EngineerProfile.user_id == user.id)
        ):
            db.add(EngineerProfile(user_id=user.id, availability=EngineerAvailability.AVAILABLE))
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
    statuses = [
        IncidentStatus.OPEN,
        IncidentStatus.OPEN,
        IncidentStatus.IN_PROGRESS,
        IncidentStatus.BLOCKED,
        IncidentStatus.RESOLVED,
        IncidentStatus.CLOSED,
        IncidentStatus.CANCELLED,
    ]
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
            priority=[
                IncidentPriority.CRITICAL,
                IncidentPriority.HIGH,
                IncidentPriority.MEDIUM,
                IncidentPriority.LOW,
            ][index % 4],
            requested_priority=IncidentPriority.MEDIUM,
            status=status,
            created_at=created,
            updated_at=now,
            reopen_count=0,
        )
        if status not in [IncidentStatus.OPEN, IncidentStatus.CANCELLED]:
            ticket.assigned_to = assignee.id
            ticket.assigned_at = created + timedelta(hours=1)
            ticket.acknowledged_at = created + timedelta(hours=2)
            ticket.first_in_progress_at = ticket.acknowledged_at
        if status == IncidentStatus.BLOCKED:
            ticket.blocked_reason = BlockedReason.WAITING_ON_PARTS
            ticket.blocked_since = created + timedelta(hours=3)
        if status in [IncidentStatus.RESOLVED, IncidentStatus.CLOSED]:
            ticket.resolved_at = created + timedelta(hours=5)
        if status == IncidentStatus.CLOSED:
            ticket.closed_at = created + timedelta(hours=6)
        if status == IncidentStatus.CANCELLED:
            ticket.cancelled_at = created + timedelta(hours=1)
        db.add(ticket)
        await db.flush()
        db.add(
            IncidentEvent(
                incident_id=ticket.id,
                actor_id=creator.id,
                type=IncidentEventType.CREATED,
                payload={"status": IncidentStatus.OPEN},
                created_at=created,
            )
        )
        if ticket.assigned_to:
            db.add(
                IncidentEvent(
                    incident_id=ticket.id,
                    actor_id=users["admin@acme.inc"].id,
                    type=IncidentEventType.ASSIGNED,
                    payload={"to": str(assignee.id)},
                    created_at=ticket.assigned_at,
                )
            )
            db.add(
                IncidentEvent(
                    incident_id=ticket.id,
                    actor_id=assignee.id,
                    type=IncidentEventType.STATUS_CHANGED,
                    payload={"from": IncidentStatus.OPEN, "to": IncidentStatus.IN_PROGRESS},
                    created_at=ticket.first_in_progress_at,
                )
            )
        if status in [IncidentStatus.BLOCKED, IncidentStatus.RESOLVED, IncidentStatus.CLOSED]:
            intermediate = (
                IncidentStatus.RESOLVED
                if status in [IncidentStatus.RESOLVED, IncidentStatus.CLOSED]
                else IncidentStatus.BLOCKED
            )
            db.add(
                IncidentEvent(
                    incident_id=ticket.id,
                    actor_id=assignee.id,
                    type=IncidentEventType.STATUS_CHANGED,
                    payload={
                        "from": IncidentStatus.IN_PROGRESS,
                        "to": intermediate,
                        "assigned_to": str(assignee.id),
                    },
                    created_at=ticket.resolved_at or ticket.blocked_since,
                )
            )
        if status in [IncidentStatus.CLOSED, IncidentStatus.CANCELLED]:
            db.add(
                IncidentEvent(
                    incident_id=ticket.id,
                    actor_id=creator.id,
                    type=IncidentEventType.STATUS_CHANGED,
                    payload={
                        "from": IncidentStatus.RESOLVED
                        if status == IncidentStatus.CLOSED
                        else IncidentStatus.OPEN,
                        "to": status,
                    },
                    created_at=ticket.closed_at or ticket.cancelled_at,
                )
            )
        if status != IncidentStatus.CANCELLED:
            public = IncidentNote(
                incident_id=ticket.id,
                author_id=assignee.id if ticket.assigned_to else creator.id,
                body="Display replaced and verified."
                if ticket.resolved_at
                else "The issue has been recorded; updates will appear here.",
                visibility=NoteVisibility.PUBLIC,
                created_at=ticket.resolved_at or created + timedelta(minutes=15),
            )
            db.add(public)
            await db.flush()
            db.add(
                IncidentEvent(
                    incident_id=ticket.id,
                    actor_id=public.author_id,
                    type=IncidentEventType.NOTE_ADDED,
                    payload={"note_id": str(public.id), "visibility": NoteVisibility.PUBLIC},
                    created_at=public.created_at,
                )
            )
            internal = IncidentNote(
                incident_id=ticket.id,
                author_id=users["admin@acme.inc"].id,
                body="Internal synthetic diagnostic note.",
                visibility=NoteVisibility.INTERNAL,
            )
            db.add(internal)
        if index == 0:
            db.add(
                EscalationRequest(
                    incident_id=ticket.id,
                    requester_id=creator.id,
                    reason="Work cannot continue until the display is fixed.",
                    state=EscalationState.PENDING,
                    requested_at=now - timedelta(hours=1),
                )
            )
        added += 1
    return added


# Realistic report wording per subcategory: (title, description).
BULK_TEMPLATES = {
    "Display/monitor": [
        ("Monitor flickering at desk", "The external monitor flickers every few seconds."),
        ("Second screen not detected", "The second display stays black after docking."),
    ],
    "Conference room AV": [
        ("Room projector will not turn on", "Projector shows no power light before a meeting."),
        ("No audio in video calls", "Room speakers are silent during video calls."),
    ],
    "Cables and adapters": [
        ("HDMI adapter missing", "The meeting room HDMI adapter is missing."),
        ("Frayed charging cable", "The shared USB-C cable is frayed near the plug."),
    ],
    "Docking station": [
        ("Docking station not charging", "Laptop does not charge through the dock."),
        ("Dock USB ports dead", "Keyboard and mouse stop working when plugged into the dock."),
    ],
    "Printer/scanner": [
        ("Printer paper jam", "The floor printer reports a jam in tray 2."),
        ("Scanner not sending email", "Scan-to-email jobs never arrive."),
    ],
    "Laptop/peripheral": [
        ("Keyboard keys sticking", "Several keys on the desk keyboard stick."),
        ("Mouse not responding", "The wireless mouse disconnects constantly."),
    ],
    "No connectivity": [
        ("No Wi-Fi in this area", "Laptops cannot see the corporate network here."),
        ("Cannot join corporate network", "Authentication to the office Wi-Fi fails."),
    ],
    "Slow connection": [
        ("Very slow Wi-Fi", "Video calls drop and pages load slowly."),
        ("Network lag in afternoons", "Connection gets very slow after 2 PM."),
    ],
    "Wired port dead": [
        ("Desk network port dead", "Nothing happens when a cable is plugged into the port."),
    ],
    "Desk/chair broken": [
        ("Chair will not stay up", "The chair height lever no longer holds."),
        ("Standing desk stuck", "The sit-stand desk will not move up or down."),
    ],
    "Ergonomic request": [
        ("Request for monitor arm", "Requesting a monitor arm to improve posture."),
        ("Request for footrest", "Requesting a footrest for the assigned desk."),
    ],
    "Too hot": [("Area is too hot", "Temperature feels above 27°C most of the day.")],
    "Too cold": [("Area is too cold", "Air conditioning is blowing cold air directly on desks.")],
    "Air quality": [("Stuffy air", "The room feels stuffy and has a musty smell.")],
    "Light out": [("Ceiling light out", "One ceiling panel light is completely out.")],
    "Power outlet": [("Desk outlet has no power", "Nothing plugged into the desk outlet works.")],
    "Flickering": [("Lights flickering", "Overhead lights flicker and cause headaches.")],
    "Leak": [("Water leak under sink", "Water is pooling under the kitchen sink.")],
    "Clog": [("Toilet clogged", "One restroom toilet is clogged.")],
    "Supplies": [("Restroom out of soap", "Soap dispensers are empty.")],
    "Spill": [("Coffee spill on carpet", "Large coffee spill near the kitchen area.")],
    "Trash": [("Trash bins overflowing", "Trash bins have not been emptied today.")],
    "General cleaning": [("Desk area needs cleaning", "Dust and crumbs around the shared desks.")],
    "Badge reader": [("Badge reader not working", "Badge is rejected at the entrance door.")],
    "Door/lock": [("Door lock jammed", "The meeting room door will not lock.")],
    "Lost and found": [("Lost laptop charger", "Left a laptop charger in a meeting room.")],
}

BULK_STATUS_WEIGHTS = {
    IncidentStatus.OPEN: 25,
    IncidentStatus.IN_PROGRESS: 20,
    IncidentStatus.BLOCKED: 10,
    IncidentStatus.RESOLVED: 15,
    IncidentStatus.CLOSED: 22,
    IncidentStatus.CANCELLED: 8,
}
BULK_PRIORITY_WEIGHTS = {
    IncidentPriority.LOW: 25,
    IncidentPriority.MEDIUM: 40,
    IncidentPriority.HIGH: 25,
    IncidentPriority.CRITICAL: 10,
}


async def seed_bulk_incidents(db, count: int) -> int:
    """Add `count` varied synthetic incidents on top of the demo data; safe to re-run."""
    rng = random.Random(20261021)
    users = {
        email: await db.scalar(select(User).where(User.email == email))
        for email in [
            "admin@acme.inc",
            "employee@acme.inc",
            "engineer@acme.inc",
            "employee2@acme.inc",
        ]
    }
    if any(user is None for user in users.values()):
        raise ValueError("Bulk incidents need the demo users; run with --demo")
    reporters = [users["employee@acme.inc"], users["employee2@acme.inc"]]
    engineers = [users["engineer@acme.inc"], users["admin@acme.inc"]]
    admin = users["admin@acme.inc"]
    categories = {
        c.name: c
        for c in await db.scalars(select(Category).where(Category.name.in_(BULK_TEMPLATES)))
    }
    floors = list(await db.scalars(select(Floor).where(Floor.is_active.is_(True))))
    seats = list(await db.scalars(select(Seat).where(Seat.is_active.is_(True))))
    if not categories or not floors:
        raise ValueError("Bulk incidents need categories and floors; run with --demo")
    now = utcnow()
    added = 0
    for index in range(count):
        ticket_id = fixed_id(f"bulk-incident/{index}")
        # Draw every random value first so re-runs stay deterministic per index.
        category_name = rng.choice(sorted(categories))
        title, description = rng.choice(BULK_TEMPLATES[category_name])
        floor = rng.choice(floors)
        floor_seats = [s for s in seats if s.floor_id == floor.id]
        location = rng.random()
        status = rng.choices(list(BULK_STATUS_WEIGHTS), list(BULK_STATUS_WEIGHTS.values()))[0]
        priority = rng.choices(list(BULK_PRIORITY_WEIGHTS), list(BULK_PRIORITY_WEIGHTS.values()))[0]
        requested = priority if rng.random() < 0.7 else rng.choice(list(IncidentPriority))
        reporter = rng.choice(reporters)
        engineer = rng.choice(engineers)
        age_days = rng.uniform(0.05, 3) if status == IncidentStatus.OPEN else rng.uniform(4, 90)
        steps = sorted(rng.uniform(0.2, 48) for _ in range(5))
        blocked_reason = rng.choice(list(BlockedReason))
        escalate = rng.random() < 0.15
        if await db.get(Incident, ticket_id):
            continue

        created = now - timedelta(days=age_days)
        at = [created + timedelta(hours=h) for h in steps]
        sequence = await db.scalar(text("SELECT nextval('incident_number_seq')"))
        ticket = Incident(
            id=ticket_id,
            number=f"INC-{sequence:06d}",
            title=title,
            description=description,
            category_id=categories[category_name].id,
            building_id=floor.building_id,
            # Mix of whole-building, floor-only and seat-level reports.
            floor_id=None if location < 0.15 else floor.id,
            seat_id=rng.choice(floor_seats).id if location > 0.5 and floor_seats else None,
            created_by=reporter.id,
            priority=priority,
            requested_priority=requested,
            status=status,
            created_at=created,
            reopen_count=0,
        )
        if ticket.floor_id is None:
            ticket.seat_id = None
        events = [
            (
                reporter,
                IncidentEventType.CREATED,
                {"status": IncidentStatus.OPEN, "priority": requested},
                created,
            )
        ]
        if priority != requested:
            events.append(
                (
                    admin,
                    IncidentEventType.PRIORITY_CHANGED,
                    {"from": requested, "to": priority},
                    created + timedelta(minutes=20),
                )
            )
        worked = status not in (IncidentStatus.OPEN, IncidentStatus.CANCELLED)
        if worked:
            ticket.assigned_to = engineer.id
            ticket.assigned_at = at[0]
            ticket.acknowledged_at = ticket.first_in_progress_at = at[1]
            events.append((admin, IncidentEventType.ASSIGNED, {"to": str(engineer.id)}, at[0]))
            events.append(
                (
                    engineer,
                    IncidentEventType.STATUS_CHANGED,
                    {"from": IncidentStatus.OPEN, "to": IncidentStatus.IN_PROGRESS},
                    at[1],
                )
            )
        if status == IncidentStatus.BLOCKED:
            ticket.blocked_reason = blocked_reason
            ticket.blocked_since = at[2]
            events.append(
                (
                    engineer,
                    IncidentEventType.STATUS_CHANGED,
                    {"from": IncidentStatus.IN_PROGRESS, "to": status, "reason": blocked_reason},
                    at[2],
                )
            )
        if status in (IncidentStatus.RESOLVED, IncidentStatus.CLOSED):
            ticket.resolved_at = at[3]
            events.append(
                (
                    engineer,
                    IncidentEventType.STATUS_CHANGED,
                    {"from": IncidentStatus.IN_PROGRESS, "to": IncidentStatus.RESOLVED},
                    at[3],
                )
            )
        if status == IncidentStatus.CLOSED:
            ticket.closed_at = at[4]
            events.append(
                (
                    reporter,
                    IncidentEventType.STATUS_CHANGED,
                    {"from": IncidentStatus.RESOLVED, "to": status},
                    at[4],
                )
            )
        if status == IncidentStatus.CANCELLED:
            ticket.cancelled_at = at[0]
            events.append(
                (
                    reporter,
                    IncidentEventType.STATUS_CHANGED,
                    {"from": IncidentStatus.OPEN, "to": status},
                    at[0],
                )
            )
        notes = []
        if worked:
            body = (
                "Issue fixed and verified on site."
                if ticket.resolved_at
                else f"Investigating; currently {status.lower()}."
            )
            notes.append((engineer, body, NoteVisibility.PUBLIC, ticket.resolved_at or at[1]))
            notes.append((engineer, "Internal diagnostic note.", NoteVisibility.INTERNAL, at[1]))
        ticket.updated_at = max(e[3] for e in events)
        db.add(ticket)
        await db.flush()
        for author, body, visibility, when in notes:
            note = IncidentNote(
                incident_id=ticket.id,
                author_id=author.id,
                body=body,
                visibility=visibility,
                created_at=when,
                updated_at=when,
            )
            db.add(note)
            await db.flush()
            events.append(
                (
                    author,
                    IncidentEventType.NOTE_ADDED,
                    {"note_id": str(note.id), "visibility": visibility},
                    when,
                )
            )
        # Pending escalations only make sense on unfinished, non-critical work.
        if escalate and status not in FINAL_STATUSES and priority != IncidentPriority.CRITICAL:
            reason = "This is blocking my work; please prioritise."
            escalation = EscalationRequest(
                incident_id=ticket.id,
                requester_id=reporter.id,
                reason=reason,
                state=EscalationState.PENDING,
                requested_at=min(now, created + timedelta(hours=2)),
            )
            db.add(escalation)
            await db.flush()
            events.append(
                (
                    reporter,
                    IncidentEventType.ESCALATION_REQUESTED,
                    {"escalation_id": str(escalation.id), "reason": reason},
                    escalation.requested_at,
                )
            )
        for actor, kind, payload, when in events:
            db.add(
                IncidentEvent(
                    incident_id=ticket.id,
                    actor_id=actor.id,
                    type=kind,
                    payload=payload,
                    created_at=when,
                    updated_at=when,
                )
            )
        added += 1
    return added


def main() -> None:
    """Run explicit reference/bootstrap seed, optionally adding local demo records."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true")
    parser.add_argument(
        "--incidents",
        type=int,
        default=0,
        metavar="N",
        help="also add N synthetic incident reports (implies --demo)",
    )
    args = parser.parse_args()
    print(asyncio.run(seed(include_demo=args.demo or args.incidents > 0, incidents=args.incidents)))


if __name__ == "__main__":
    main()
