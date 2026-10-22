"""Showcase seed: tops every feature up to at least 10 records on top of the demo seed.

Runs after `seed_demo`, `seed_teams` and `seed_bulk_incidents`. Every record has a fixed
id, so re-running only fills gaps and never duplicates or overwrites edited data.
"""

import os
import struct
import zlib
from collections.abc import Callable
from datetime import timedelta

from sqlalchemy import select

from facility.enums import (
    AttachmentStatus,
    AttachmentType,
    EngineerAvailability,
    EngineerSkill,
    EscalationState,
    IncidentEventType,
    IncidentStatus,
    NoteVisibility,
    NotificationType,
    UserRole,
)
from facility.models import (
    Attachment,
    Building,
    CategoryGroup,
    EngineerProfile,
    EscalationRequest,
    Floor,
    Incident,
    IncidentEvent,
    IncidentFollower,
    Notification,
    Seat,
    Team,
    User,
    utcnow,
)
from facility.security import hash_password

S = EngineerSkill
A = EngineerAvailability

# Eight more sites on top of the demo's HQ1 and WEST: (code, name, address).
BUILDINGS = [
    ("NYC", "New York Tower", "200 Park Ave, New York, NY"),
    ("BOS", "Boston Innovation Hub", "1 Seaport Blvd, Boston, MA"),
    ("CHI", "Chicago Loop Office", "233 S Wacker Dr, Chicago, IL"),
    ("ATL", "Atlanta Midtown", "1100 Peachtree St, Atlanta, GA"),
    ("DAL", "Dallas Operations Center", "2200 Ross Ave, Dallas, TX"),
    ("SEA", "Seattle Lakeside", "400 Fairview Ave, Seattle, WA"),
    ("DEN", "Denver Data Center", "1801 California St, Denver, CO"),
    ("LON", "London Riverside", "1 Canada Square, London"),
]
FLOORS_PER_BUILDING = 2
SEATS_PER_FLOOR = 4

# Ten more reporters (employees with no extra roles).
REPORTERS = [
    ("lena.ortiz@acme.inc", "Lena Ortiz"),
    ("marcus.bell@acme.inc", "Marcus Bell"),
    ("nina.kowalski@acme.inc", "Nina Kowalski"),
    ("omar.haddad@acme.inc", "Omar Haddad"),
    ("grace.liu@acme.inc", "Grace Liu"),
    ("ethan.brooks@acme.inc", "Ethan Brooks"),
    ("sofia.rossi@acme.inc", "Sofia Rossi"),
    ("daniel.kim@acme.inc", "Daniel Kim"),
    ("chloe.martin@acme.inc", "Chloe Martin"),
    ("victor.nunez@acme.inc", "Victor Nunez"),
]

# Six more teams (demo has four): (team, owned routing group or None, lead, engineers).
TEAMS = [
    (
        "General Maintenance",
        "Other",
        ("maint.lead@acme.inc", "Harper Wells"),
        [
            (
                "maint.eng@acme.inc",
                "Logan Price",
                [S.FURNITURE, S.ELECTRICAL_LIGHTING],
                A.AVAILABLE,
            ),
            ("maint.eng2@acme.inc", "Mia Turner", [S.PLUMBING_RESTROOMS, S.CLEANING], A.BUSY),
        ],
    ),
    (
        "Network Operations",
        None,
        ("netops.lead@acme.inc", "Ian Walsh"),
        [
            ("netops.eng@acme.inc", "Zoe Adams", [S.NETWORK_WIFI], A.AVAILABLE),
            (
                "netops.eng2@acme.inc",
                "Ben Carter",
                [S.NETWORK_WIFI, S.IT_AV_EQUIPMENT],
                A.AVAILABLE,
            ),
        ],
    ),
    (
        "Audio Visual",
        None,
        ("av.lead@acme.inc", "Elena Petrova"),
        [
            ("av.eng@acme.inc", "Noah Fischer", [S.IT_AV_EQUIPMENT], A.AVAILABLE),
            (
                "av.eng2@acme.inc",
                "Lily Evans",
                [S.IT_AV_EQUIPMENT, S.ELECTRICAL_LIGHTING],
                A.OFF_DUTY,
            ),
        ],
    ),
    (
        "Climate Control",
        None,
        ("hvac.lead@acme.inc", "Oscar Lindgren"),
        [
            ("hvac.eng@acme.inc", "Ava Mitchell", [S.HVAC_TEMPERATURE], A.AVAILABLE),
            (
                "hvac.eng2@acme.inc",
                "Jack Morales",
                [S.HVAC_TEMPERATURE, S.ELECTRICAL_LIGHTING],
                A.BUSY,
            ),
        ],
    ),
    (
        "Custodial Services",
        None,
        ("custodial.lead@acme.inc", "Fatima Rahman"),
        [
            ("custodial.eng@acme.inc", "Leo Gomez", [S.CLEANING], A.AVAILABLE),
            (
                "custodial.eng2@acme.inc",
                "Ella Wright",
                [S.CLEANING, S.PLUMBING_RESTROOMS],
                A.AVAILABLE,
            ),
        ],
    ),
    (
        "Physical Security",
        None,
        ("physec.lead@acme.inc", "Victor Hale"),
        [
            ("physec.eng@acme.inc", "Ruby Chen", [S.ACCESS_SECURITY], A.AVAILABLE),
            (
                "physec.eng2@acme.inc",
                "Max Novak",
                [S.ACCESS_SECURITY, S.ELECTRICAL_LIGHTING],
                A.BUSY,
            ),
        ],
    ),
]

FOLLOWERS = 15
NOTIFICATIONS = 15
DECIDED_ESCALATIONS = 10
ATTACHMENTS = 12


def tiny_png(red: int, green: int, blue: int, size: int = 16) -> bytes:
    """A valid solid-colour PNG (passes the API's magic-byte check)."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    row = b"\x00" + bytes([red, green, blue]) * size
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(row * size))
        + chunk(b"IEND", b"")
    )


async def _user(db, fixed_id, email: str, name: str, roles: list, password_hash: str):
    user = await db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(
            id=fixed_id(email),
            email=email,
            full_name=name,
            roles=roles,
            password_hash=password_hash,
        )
        db.add(user)
        await db.flush()
        return user, 1
    return user, 0


async def seed_showcase(
    db,
    fixed_id: Callable,
    put_object: Callable[[str, bytes, str], None] | None = None,
) -> int:
    """Top buildings, people, teams, followers, notifications, escalations and photos up to 10+."""
    added = 0
    password = hash_password(os.environ.get("DEMO_PASSWORD", "DemoPass123!"))
    now = utcnow()

    # Locations: 8 buildings x 2 floors x 4 seats.
    building_ids = []
    for code, name, address in BUILDINGS:
        building = await db.scalar(select(Building).where(Building.code == code))
        if building is None:
            building = Building(
                id=fixed_id("building/" + code), name=name, code=code, address=address
            )
            db.add(building)
            await db.flush()
            added += 1
        building_ids.append(building.id)
        for level in range(1, FLOORS_PER_BUILDING + 1):
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
            for number in range(1, SEATS_PER_FLOOR + 1):
                seat_code = f"{level}-B-{number:02d}"
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
    seats = list(await db.scalars(select(Seat).where(Seat.is_active.is_(True)).order_by(Seat.code)))

    # Reporters, each with a default seat so they can report without picking a location.
    reporters = []
    for index, (email, name) in enumerate(REPORTERS):
        user, new = await _user(db, fixed_id, email, name, [], password)
        added += new
        if new and seats:
            user.default_seat_id = seats[index % len(seats)].id
        reporters.append(user)

    # Teams with leads and engineers.
    for index, (team_name, group_name, (lead_email, lead_name), engineers) in enumerate(TEAMS):
        lead, new = await _user(db, fixed_id, lead_email, lead_name, [UserRole.LEAD], password)
        added += new
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
        if group_name:
            group = await db.scalar(select(CategoryGroup).where(CategoryGroup.name == group_name))
            if group is not None and group.team_id is None:
                group.team_id = team.id
        for email, name, skills, availability in engineers:
            user, new = await _user(db, fixed_id, email, name, [UserRole.ENGINEER], password)
            added += new
            profile = await db.scalar(
                select(EngineerProfile).where(EngineerProfile.user_id == user.id)
            )
            if profile is None:
                db.add(
                    EngineerProfile(
                        user_id=user.id,
                        availability=availability,
                        skills=skills,
                        team_id=team.id,
                        home_building_ids=[building_ids[index % len(building_ids)]],
                    )
                )
                added += 1
    await db.flush()

    # Work on the bulk incidents (stable order by ticket number).
    tickets = list(
        await db.scalars(
            select(Incident)
            .where(Incident.id.in_([fixed_id(f"bulk-incident/{i}") for i in range(200)]))
            .order_by(Incident.number)
        )
    )
    if not tickets or not reporters:
        return added
    admin = await db.scalar(select(User).where(User.email == "admin@acme.inc"))

    # Followers: colleagues watching tickets they did not report.
    for index, ticket in enumerate(tickets[:FOLLOWERS]):
        follower = reporters[index % len(reporters)]
        if follower.id == ticket.created_by:
            follower = reporters[(index + 1) % len(reporters)]
        if not await db.get(IncidentFollower, (ticket.id, follower.id)):
            db.add(
                IncidentFollower(
                    incident_id=ticket.id, user_id=follower.id, created_at=ticket.created_at
                )
            )
            added += 1

    # Notifications for reporters, the first few already read.
    kinds = [
        (NotificationType.ASSIGNED, "An engineer has been assigned to {number}."),
        (NotificationType.STATUS_CHANGED, "{number} is now {status}."),
        (NotificationType.NOTE_ADDED, "A new update was posted on {number}."),
    ]
    for index, ticket in enumerate(tickets[:NOTIFICATIONS]):
        item_id = fixed_id(f"showcase/notification/{index}")
        if await db.get(Notification, item_id):
            continue
        kind, template = kinds[index % len(kinds)]
        db.add(
            Notification(
                id=item_id,
                user_id=ticket.created_by,
                incident_id=ticket.id,
                type=kind,
                message=template.format(number=ticket.number, status=ticket.status),
                read_at=now - timedelta(hours=1) if index < 5 else None,
                created_at=now - timedelta(hours=index + 2),
            )
        )
        added += 1

    # Decided escalations (the bulk seed only creates pending ones).
    worked = [t for t in tickets if t.status != IncidentStatus.CANCELLED]
    for index, ticket in enumerate(worked[-DECIDED_ESCALATIONS:]):
        item_id = fixed_id(f"showcase/escalation/{index}")
        if await db.get(EscalationRequest, item_id) or admin is None:
            continue
        approved = index % 2 == 0
        requested = ticket.created_at + timedelta(hours=1)
        decided = requested + timedelta(hours=2)
        escalation = EscalationRequest(
            id=item_id,
            incident_id=ticket.id,
            requester_id=ticket.created_by,
            reason="This is affecting the whole team; please prioritise.",
            state=EscalationState.APPROVED if approved else EscalationState.REJECTED,
            requested_at=requested,
            decided_by=admin.id,
            decided_at=decided,
            decision_reason="Business impact confirmed."
            if approved
            else "Current priority is appropriate.",
            action_plan="Engineer dispatched within the hour." if approved else None,
        )
        db.add(escalation)
        await db.flush()
        for kind, when in [
            (IncidentEventType.ESCALATION_REQUESTED, requested),
            (
                IncidentEventType.ESCALATION_APPROVED
                if approved
                else IncidentEventType.ESCALATION_REJECTED,
                decided,
            ),
        ]:
            db.add(
                IncidentEvent(
                    incident_id=ticket.id,
                    actor_id=ticket.created_by
                    if kind == IncidentEventType.ESCALATION_REQUESTED
                    else admin.id,
                    type=kind,
                    payload={"escalation_id": str(escalation.id)},
                    created_at=when,
                    updated_at=when,
                )
            )
        added += 1

    # Photo attachments: only when there is a bucket to put the images in.
    if put_object is not None:
        colours = [(220, 60, 60), (60, 160, 90), (50, 110, 210), (230, 170, 40)]
        for index, ticket in enumerate(tickets[:ATTACHMENTS]):
            item_id = fixed_id(f"showcase/attachment/{index}")
            if await db.get(Attachment, item_id):
                continue
            body = tiny_png(*colours[index % len(colours)])
            key = f"incidents/{ticket.id}/{item_id}"
            put_object(key, body, AttachmentType.PNG.value)
            db.add(
                Attachment(
                    id=item_id,
                    incident_id=ticket.id,
                    uploaded_by=ticket.created_by,
                    s3_key=key,
                    content_type=AttachmentType.PNG,
                    size_bytes=len(body),
                    status=AttachmentStatus.ACCEPTED,
                )
            )
            await db.flush()
            db.add(
                IncidentEvent(
                    incident_id=ticket.id,
                    actor_id=ticket.created_by,
                    type=IncidentEventType.ATTACHMENT_ADDED,
                    payload={
                        "attachment_id": str(item_id),
                        "note_id": None,
                        "visibility": NoteVisibility.PUBLIC,
                    },
                    created_at=ticket.created_at + timedelta(minutes=5),
                    updated_at=ticket.created_at + timedelta(minutes=5),
                )
            )
            added += 1
    await db.flush()
    return added
