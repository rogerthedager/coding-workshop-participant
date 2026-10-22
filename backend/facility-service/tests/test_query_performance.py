"""Response-time guards: list pages use a fixed number of queries, and hot
queries are able to use their PostgreSQL indexes."""

from contextlib import contextmanager
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.dialects.postgresql import distinct_on

from facility.enums import (
    FINAL_STATUSES,
    NON_FINAL_STATUSES,
    IncidentEventType,
    IncidentStatus,
    NoteVisibility,
)
from facility.metrics import latest_resolver
from facility.models import (
    EngineerProfile,
    EscalationRequest,
    Incident,
    IncidentEvent,
    IncidentNote,
    Team,
    utcnow,
)
from facility.routers import dashboards
from facility.routers.incidents import search_clause
from facility.sla import ACTIVE_STATUSES

RESOLVER = latest_resolver()


def matched(headers, record):
    """Use the record's explicit version for the next write."""
    return {**headers, "If-Match": record["updated_at"]}


@contextmanager
def count_queries(db):
    """Count SQL statements sent through the test engine inside the block."""
    statements: list[str] = []

    def record(_conn, _cursor, statement, *_args):
        statements.append(statement)

    engine = db.bind.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


async def escalate_and_reject(client, ticket_id, creator, manager, reason):
    """Give a ticket a decided escalation with its own public decision note."""
    path = f"/api/v1/incidents/{ticket_id}"
    state = (await client.get(path, headers=creator)).json()
    pending = await client.post(
        path + "/escalation",
        headers=matched(creator, state),
        json={"reason": "This needs management attention today"},
    )
    assert pending.status_code == 201, pending.text
    decided = await client.post(
        path + "/escalation/decision",
        headers=matched(manager, pending.json()),
        json={"approved": False, "decision_reason": reason},
    )
    assert decided.status_code == 200, decided.text


@pytest.mark.integration
async def test_incident_list_query_count_does_not_grow_with_page_size(
    client, db, employee, other_employee, admin, auth_headers, make_incident
):
    """A 6-row page costs the same queries as a 1-row page, and rows keep their own data."""
    creator = await auth_headers(employee)
    follower = await auth_headers(other_employee)
    manager = await auth_headers(admin)
    tickets = [await make_incident(title=f"Projector fault {index}") for index in range(6)]
    reasons = {}
    for index, ticket in enumerate(tickets):
        # Newest first: the 1-row page (ticket 5) has a decision, like the full page.
        if index % 2 == 1:
            reasons[str(ticket.id)] = f"Workaround documented for ticket {index}"
            await escalate_and_reject(client, ticket.id, creator, manager, reasons[str(ticket.id)])
        if index >= 3:
            followed = await client.post(f"/api/v1/incidents/{ticket.id}/follow", headers=follower)
            assert followed.status_code == 204, followed.text

    for headers in (creator, follower, manager):
        counts = []
        for size in (1, 6):
            with count_queries(db) as statements:
                listing = await client.get(
                    "/api/v1/incidents", params={"page_size": size}, headers=headers
                )
            assert listing.status_code == 200, listing.text
            counts.append(len(statements))
        assert counts[0] == counts[1], counts

        items = listing.json()["items"]
        assert items
        for item in items:
            # The batched page projection must equal the single-ticket projection.
            detail = await client.get(f"/api/v1/incidents/{item['id']}", headers=headers)
            assert detail.status_code == 200
            assert item == detail.json()
            if headers is not follower:
                expected = reasons.get(item["id"])
                actual = (item["latest_escalation"] or {}).get("decision_reason")
                assert actual == expected
    follower_items = (await client.get("/api/v1/incidents", headers=follower)).json()["items"]
    assert {item["id"] for item in follower_items} == {str(t.id) for t in tickets[3:]}
    assert all("description" not in item for item in follower_items)
    assert all(str(employee.id) not in item["people"] for item in follower_items)


async def plan(db, statement) -> str:
    """EXPLAIN a statement with sequential scans disabled, so index use is visible
    even on tiny test tables. Must be called inside the caller's transaction."""
    sql = str(statement.compile(dialect=db.bind.dialect, compile_kwargs={"literal_binds": True}))
    rows = await db.execute(text("EXPLAIN " + sql))
    return "\n".join(row[0] for row in rows)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("query", "indexes"),
    [
        ("projector", {"ix_incidents_search"}),
        ("INC-000001", {"ix_incidents_search", "uq_incidents_number"}),
        ("inc-1", {"ix_incidents_search", "uq_incidents_number"}),
    ],
)
async def test_search_uses_number_and_text_indexes(db, make_incident, query, indexes):
    """Every branch of the search OR is indexable, so no table scan is needed."""
    await make_incident()
    await db.execute(text("SET LOCAL enable_seqscan = off"))
    explained = await plan(db, select(Incident.id).where(search_clause(query)))
    assert "Seq Scan" not in explained, explained
    for name in indexes:
        assert name in explained, explained
    await db.rollback()


@pytest.mark.integration
async def test_search_still_finds_by_number_and_text(
    client, employee, auth_headers, make_incident
):
    """Removing the upper(number) branch keeps every supported way of searching."""
    creator = await auth_headers(employee)
    ticket = await make_incident(title="Broken window latch")
    other = await make_incident(title="Projector cable missing")
    digits = ticket.number.removeprefix("INC-").lstrip("0")
    for query in (ticket.number, ticket.number.lower(), f"inc-{digits}", digits, "latch"):
        result = await client.get("/api/v1/incidents", params={"q": query}, headers=creator)
        assert result.status_code == 200
        ids = {item["id"] for item in result.json()["items"]}
        assert str(ticket.id) in ids and str(other.id) not in ids, query


def hot_queries():
    """The statements behind the slow paths, each with the index meant to serve it."""
    now = utcnow()
    return [
        (
            "latest escalation per ticket",
            select(EscalationRequest)
            .where(EscalationRequest.incident_id.in_([uuid4(), uuid4()]))
            .order_by(
                EscalationRequest.incident_id,
                EscalationRequest.requested_at.desc(),
                EscalationRequest.id.desc(),
            )
            .ext(distinct_on(EscalationRequest.incident_id)),
            "ix_escalation_requests_latest",
        ),
        (
            "last public update per ticket",
            select(IncidentNote.incident_id, func.max(IncidentNote.created_at))
            .where(
                IncidentNote.visibility == NoteVisibility.PUBLIC,
                IncidentNote.hidden_at.is_(None),
            )
            .group_by(IncidentNote.incident_id),
            "ix_incident_notes_public",
        ),
        (
            "status transitions in a report range",
            select(func.count(IncidentEvent.id)).where(
                IncidentEvent.type == IncidentEventType.STATUS_CHANGED,
                IncidentEvent.created_at >= now - timedelta(days=7),
                IncidentEvent.created_at < now,
            ),
            "ix_incident_events_type_created",
        ),
        (
            "latest resolution per ticket",
            select(IncidentEvent.incident_id, func.max(IncidentEvent.created_at))
            .where(
                IncidentEvent.type == IncidentEventType.STATUS_CHANGED,
                IncidentEvent.payload["to"].astext == IncidentStatus.RESOLVED,
            )
            .group_by(IncidentEvent.incident_id),
            "ix_incident_events_resolved",
        ),
        (
            "latest resolver of each resolved ticket",
            select(Incident.id, RESOLVER.c.resolver)
            .join(RESOLVER, RESOLVER.c.incident_id == Incident.id)
            .where(Incident.resolved_at.is_not(None)),
            "ix_incident_events_resolved",
        ),
        (
            "open load of one engineer",
            select(func.count(Incident.id)).where(
                Incident.assigned_to == uuid4(),
                Incident.status.in_(NON_FINAL_STATUSES),
            ),
            "ix_incidents_assignee_status",
        ),
        (
            "workload by engineer and status",
            select(Incident.assigned_to, Incident.status, func.count())
            .where(Incident.status.not_in(FINAL_STATUSES))
            .group_by(Incident.assigned_to, Incident.status),
            "ix_incidents_assignee_status",
        ),
        (
            "SLA acknowledgment breach scan",
            select(Incident.id).where(
                Incident.ack_breach_alerted_at.is_(None),
                Incident.status.in_(ACTIVE_STATUSES),
                Incident.acknowledged_at.is_(None),
                func.now() > Incident.ack_due_at,
            ),
            "ix_incidents_active_ack_due",
        ),
        (
            "SLA resolution breach scan",
            select(Incident.id).where(
                Incident.resolve_breach_alerted_at.is_(None),
                Incident.status.in_(ACTIVE_STATUSES),
                Incident.resolved_at.is_(None),
                func.now() > Incident.resolve_due_at,
            ),
            "ix_incidents_active_resolve_due",
        ),
    ]


@pytest.mark.integration
@pytest.mark.parametrize(
    ("label", "statement", "index"), hot_queries(), ids=[item[0] for item in hot_queries()]
)
async def test_hot_queries_can_use_their_index(db, label, statement, index):
    """Each slow path has an index PostgreSQL can choose instead of a table scan."""
    await db.execute(text("SET LOCAL enable_seqscan = off"))
    explained = await plan(db, statement)
    assert index in explained, f"{label}:\n{explained}"
    await db.rollback()


async def resolve_event(db, ticket, engineer, at):
    """Record a resolve transition crediting `engineer`."""
    db.add(
        IncidentEvent(
            incident_id=ticket.id,
            actor_id=engineer.id,
            type=IncidentEventType.STATUS_CHANGED,
            payload={"from": "In Progress", "to": "Resolved", "assigned_to": str(engineer.id)},
            created_at=at,
        )
    )


@pytest.mark.integration
async def test_time_by_team_credits_latest_resolver_in_one_query(
    client, db, admin, make_user, make_incident, auth_headers
):
    """Per-team stats come from one grouped query, credited to each ticket's latest resolver."""
    headers = await auth_headers(admin)

    async def new_team(name):
        lead = await make_user(full_name=f"{name} lead", roles=["lead"])
        return Team(name=name, lead_user_id=lead.id)

    first, second, idle = [await new_team(name) for name in ("Alpha desk", "Beta desk", "Gamma desk")]
    db.add_all([first, second, idle])
    await db.flush()
    alpha = await make_user(full_name="Alpha Engineer", roles=["engineer"])
    beta = await make_user(full_name="Beta Engineer", roles=["engineer"])
    for user, team in ((alpha, first), (beta, second)):
        profile = await db.scalar(select(EngineerProfile).where(EngineerProfile.user_id == user.id))
        profile.team_id = team.id
    now = utcnow()
    # Resolved by Alpha, reopened, then resolved again by Beta: Beta's team gets it.
    reopened = await make_incident(
        status="Resolved", created_at=now - timedelta(hours=10), resolved_at=now - timedelta(hours=1)
    )
    await resolve_event(db, reopened, alpha, now - timedelta(hours=5))
    await resolve_event(db, reopened, beta, now - timedelta(hours=1))
    single = await make_incident(
        status="Resolved", created_at=now - timedelta(hours=4), resolved_at=now - timedelta(hours=2)
    )
    await resolve_event(db, single, alpha, now - timedelta(hours=2))
    await db.commit()

    with count_queries(db) as statements:
        result = await client.get("/api/v1/dashboard/admin", headers=headers)
    assert result.status_code == 200, result.text
    teams = {row["name"]: row for row in result.json()["company"]["time_by_team"]}
    assert teams["Alpha desk"]["count"] == 1
    assert teams["Alpha desk"]["mean_seconds"] == pytest.approx(2 * 3600)
    assert teams["Beta desk"]["count"] == 1
    assert teams["Beta desk"]["mean_seconds"] == pytest.approx(9 * 3600)
    assert teams["Gamma desk"] == {
        "team_id": str(idle.id),
        "name": "Gamma desk",
        "count": 0,
        "mean_seconds": None,
        "median_seconds": None,
        "on_time_ratio": None,
    }

    # More teams must not mean more queries.
    db.add_all([await new_team(f"Extra desk {index}") for index in range(5)])
    await db.commit()
    with count_queries(db) as more:
        again = await client.get("/api/v1/dashboard/admin", headers=headers)
    assert again.status_code == 200, again.text
    assert len(more) == len(statements)


@pytest.mark.integration
async def test_admin_communication_lists_only_the_most_stale(
    client, db, admin, make_incident, auth_headers, monkeypatch
):
    """The company-wide list is capped, never-updated tickets first."""
    monkeypatch.setattr(dashboards, "ADMIN_COMMUNICATION_LIMIT", 2)
    tickets = [await make_incident(title=f"Stale check {index}") for index in range(3)]
    for index, ticket in enumerate(tickets[1:]):
        db.add(
            IncidentNote(
                incident_id=ticket.id,
                author_id=admin.id,
                body="Update for the reporter",
                visibility=NoteVisibility.PUBLIC,
                created_at=utcnow() - timedelta(hours=5 - index),
            )
        )
    await db.commit()
    result = await client.get("/api/v1/dashboard/admin", headers=await auth_headers(admin))
    assert result.status_code == 200, result.text
    listed = result.json()["communication"]
    assert [row["id"] for row in listed] == [str(tickets[0].id), str(tickets[1].id)]
    assert listed[0]["last_public_update"] is None
