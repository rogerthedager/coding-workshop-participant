"""Second review round: routing re-check, SLA re-arm, local week, teams, cleanup, links."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from facility.jobs import cleanup_uploads
from facility.models import Attachment, Incident, User, utcnow
from facility.routing import still_eligible

# Shared fixtures; pytest injects them by name.
from tests.test_routing_teams import it_team, network  # noqa: F401


async def version(client, headers, incident_id) -> dict:
    current = await client.get(f"/api/v1/incidents/{incident_id}", headers=headers)
    return {**headers, "If-Match": f'"{current.json()["updated_at"]}"'}


async def test_routing_rechecks_engineer_after_locking(db, engineer, it_team):  # noqa: F811
    team_id = it_team["team"].id
    assert await still_eligible(db, engineer.id, team_id) is not None
    engineer.is_active = False
    await db.merge(engineer)
    await db.commit()
    assert await still_eligible(db, engineer.id, team_id) is None


async def test_priority_change_rearms_sla_alerts(client, db, admin, make_incident, auth_headers):
    ticket = await make_incident(priority="Critical")
    ticket.set_due_times()
    ticket.ack_breach_alerted_at = utcnow()
    ticket.resolve_at_risk_alerted_at = utcnow()
    await db.commit()
    headers = await auth_headers(admin)
    changed = await client.patch(
        f"/api/v1/incidents/{ticket.id}/priority",
        headers=await version(client, headers, ticket.id),
        json={"priority": "Medium", "reason": "Not urgent after all"},
    )
    assert changed.status_code == 200, changed.text
    stored = await db.scalar(
        select(Incident).where(Incident.id == ticket.id).execution_options(populate_existing=True)
    )
    assert stored.ack_breach_alerted_at is None
    assert stored.resolve_at_risk_alerted_at is None


async def test_engineer_week_is_local_and_bad_tz_is_422(client, engineer, auth_headers):
    headers = await auth_headers(engineer)
    result = await client.get(
        "/api/v1/dashboard/engineer", headers=headers, params={"tz": "Pacific/Kiritimati"}
    )
    assert result.status_code == 200, result.text
    start = datetime.fromisoformat(result.json()["resolved_this_week"]["from"])
    local = start.astimezone(ZoneInfo("Pacific/Kiritimati"))
    assert (local.weekday(), local.hour, local.minute) == (0, 0, 0)
    long_tz = await client.get(
        "/api/v1/dashboard/team", headers=headers, params={"tz": "x" * 300}
    )
    assert long_tz.status_code in (403, 422)
    bad = await client.get("/api/v1/dashboard/engineer", headers=headers, params={"tz": "Nope/Zone"})
    assert bad.status_code == 422


async def test_team_ignores_deactivated_members(
    client, db, admin, engineer, it_team, auth_headers  # noqa: F811
):
    headers = await auth_headers(admin)
    team_id = it_team["team"].id
    before = await client.get(f"/api/v1/teams/{team_id}", headers=headers)
    assert str(engineer.id) in {m["id"] for m in before.json()["members"]}
    user = await db.get(User, engineer.id)
    user.is_active = False
    await db.commit()
    after = await client.get(f"/api/v1/teams/{team_id}", headers=headers)
    assert str(engineer.id) not in {m["id"] for m in after.json()["members"]}


async def test_team_reactivation_needs_active_lead(client, db, admin, make_user, auth_headers):
    headers = await auth_headers(admin)
    lead = await make_user(full_name="Lead Person")
    created = await client.post(
        "/api/v1/teams", headers=headers, json={"name": "Night shift", "lead_user_id": str(lead.id)}
    )
    assert created.status_code == 201, created.text
    team_id = created.json()["id"]
    closed = await client.patch(f"/api/v1/teams/{team_id}", headers=headers, json={"is_active": False})
    assert closed.status_code == 200, closed.text
    stored = await db.get(User, lead.id)
    stored.is_active = False
    await db.commit()
    reopened = await client.patch(f"/api/v1/teams/{team_id}", headers=headers, json={"is_active": True})
    assert reopened.status_code == 409
    assert reopened.json()["error"]["code"] == "team_needs_lead"


async def test_cleanup_skips_accepted_rows(db, employee, make_incident):
    ticket = await make_incident()
    old = utcnow() - timedelta(hours=2)
    pending = Attachment(
        incident_id=ticket.id, uploaded_by=employee.id, s3_key="incidents/a/pending",
        content_type="image/jpeg", status="pending", size_bytes=10, created_at=old,
    )
    accepted = Attachment(
        incident_id=ticket.id, uploaded_by=employee.id, s3_key="incidents/a/accepted",
        content_type="image/jpeg", status="accepted", size_bytes=10, created_at=old,
    )
    db.add_all([pending, accepted])
    await db.commit()

    class Recorder:
        deleted: list[str] = []

        def delete(self, key: str) -> None:
            self.deleted.append(key)

    recorder = Recorder()
    assert await cleanup_uploads(db, recorder) == 1
    assert recorder.deleted == ["incidents/a/pending"]
    keys = (await db.scalars(select(Attachment.s3_key))).all()
    assert keys == ["incidents/a/accepted"]


async def test_admin_dashboard_links_use_exclusive_end(client, admin, make_incident, auth_headers):
    await make_incident()
    result = await client.get("/api/v1/dashboard/admin", headers=await auth_headers(admin))
    assert result.status_code == 200, result.text
    assert "date_to" not in result.text


async def test_responses_carry_names_but_hide_reporter_from_followers(
    client, employee, other_employee, engineer, make_incident, auth_headers
):
    ticket = await make_incident()
    creator = await auth_headers(employee)
    note = await client.post(
        f"/api/v1/incidents/{ticket.id}/notes",
        headers=await version(client, creator, ticket.id),
        json={"body": "Still broken", "visibility": "public"},
    )
    assert note.status_code == 201, note.text
    staff = await auth_headers(engineer)
    detail = (await client.get(f"/api/v1/incidents/{ticket.id}", headers=staff)).json()
    assert detail["people"][str(employee.id)] == "Employee One"
    assert detail["people"][str(engineer.id)] == "Engineer One"
    notes = (await client.get(f"/api/v1/incidents/{ticket.id}/notes", headers=staff)).json()
    assert notes["people"][str(employee.id)] == "Employee One"
    listed = (await client.get("/api/v1/incidents", headers=staff)).json()["items"][0]
    assert listed["people"][str(engineer.id)] == "Engineer One"

    follower = await auth_headers(other_employee)
    assert (
        await client.post(f"/api/v1/incidents/{ticket.id}/follow", headers=follower)
    ).status_code == 204
    for path in ("", "/notes", "/timeline"):
        body = await client.get(f"/api/v1/incidents/{ticket.id}{path}", headers=follower)
        assert str(employee.id) not in body.text
        assert "Employee One" not in body.text


async def test_sibling_category_names_are_unique(client, admin, auth_headers, reference_data):
    headers = await auth_headers(admin)
    parent = str(reference_data["category"].id)
    first = await client.post(
        "/api/v1/categories", headers=headers, json={"name": "Desk lamp", "parent_id": parent}
    )
    assert first.status_code == 201, first.text
    again = await client.post(
        "/api/v1/categories", headers=headers, json={"name": "desk LAMP", "parent_id": parent}
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "duplicate"


async def test_number_search_accepts_short_forms(client, employee, make_incident, auth_headers):
    ticket = await make_incident()
    digits = str(int(ticket.number.removeprefix("INC-")))
    headers = await auth_headers(employee)
    for query in (ticket.number, ticket.number.lower(), f"inc-{digits}", digits, f"00{digits}"):
        found = await client.get("/api/v1/incidents", headers=headers, params={"q": query})
        assert [item["id"] for item in found.json()["items"]] == [str(ticket.id)], query


async def test_triage_widget_links_reproduce_their_counts(
    client, admin, employee, engineer, it_team, make_incident, auth_headers  # noqa: F811
):
    from tests.test_routing_teams import report

    headers = await auth_headers(admin)
    # One ticket with no triage reason (admin unassigned it) and one returned by the team.
    await make_incident(assigned_to=None)
    created = await client.post(
        "/api/v1/incidents",
        headers=await auth_headers(employee),
        json=report(it_team, it_team["child"]),
    )
    ticket = created.json()
    returned = await client.post(
        f"/api/v1/incidents/{ticket['id']}/return-to-triage",
        headers={**await auth_headers(engineer), "If-Match": f'"{ticket["updated_at"]}"'},
        json={"reason": "This is a cabling job for facilities"},
    )
    assert returned.status_code == 200, returned.text
    board = (await client.get("/api/v1/dashboard/admin", headers=headers)).json()["routing_health"]
    widgets = board["triage_queue"] + board["returned_by_team"]
    assert {item.get("reason") for item in board["triage_queue"]} >= {None, "returned"}
    assert board["returned_by_team"], board
    for item in widgets:
        listed = await client.get(item["href"], headers=headers)
        assert listed.status_code == 200, (item, listed.text)
        assert listed.json()["total"] == item["count"], item


async def test_temporary_password_block_has_its_own_code(client, make_user, auth_headers):
    user = await make_user(must_change_password=True)
    blocked = await client.get("/api/v1/incidents", headers=await auth_headers(user))
    assert blocked.status_code == 403
    error = blocked.json()["error"]
    assert error["code"] == "password_change_required"
    assert "message" not in error["details"]
