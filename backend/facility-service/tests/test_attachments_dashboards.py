"""Attachments, bulk seats, default seat, and the team and company dashboards."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from facility.models import EngineerProfile, IncidentEvent, Team, utcnow
from facility.storage import set_storage

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 60
PDF = b"%PDF-1.7\n" + b"\x00" * 60


class FakeStorage:
    """In-memory stand-in for the private bucket."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def presign_post(self, key: str, content_type: str) -> dict:
        return {"url": "https://bucket.example", "fields": {"key": key}}

    def size(self, key: str) -> int | None:
        return len(self.objects[key]) if key in self.objects else None

    def head_bytes(self, key: str, count: int) -> bytes:
        return self.objects[key][:count]

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    def presign_get(self, key: str) -> str:
        return f"https://bucket.example/{key}?signed"


@pytest.fixture
def storage():
    fake = FakeStorage()
    set_storage(fake)
    yield fake
    set_storage(None)


async def upload(client, headers, incident_id, storage, data, content_type="image/jpeg", **extra):
    presigned = await client.post(
        f"/api/v1/incidents/{incident_id}/attachments/presign",
        headers=headers,
        json={"content_type": content_type, "size_bytes": len(data), **extra},
    )
    if presigned.status_code != 201:
        return presigned
    key = presigned.json()["upload"]["fields"]["key"]
    storage.objects[key] = data
    return await client.post(
        f"/api/v1/incidents/{incident_id}/attachments",
        headers=headers,
        json={"attachment_id": presigned.json()["attachment_id"]},
    )


async def test_attachment_flow_checks_signature_limit_and_visibility(
    client, db, employee, other_employee, engineer, make_incident, auth_headers, storage
):
    ticket = await make_incident()
    creator = await auth_headers(employee)
    accepted = await upload(client, creator, ticket.id, storage, JPEG)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "accepted"
    renamed = await upload(client, creator, ticket.id, storage, PDF)
    assert renamed.status_code == 422
    assert renamed.json()["error"]["code"] == "attachment_invalid"
    outsider = await upload(client, await auth_headers(other_employee), ticket.id, storage, JPEG)
    assert outsider.status_code == 404
    too_big = await client.post(
        f"/api/v1/incidents/{ticket.id}/attachments/presign",
        headers=creator,
        json={"content_type": "image/png", "size_bytes": 6 * 1024 * 1024},
    )
    assert too_big.status_code == 422
    # An image on an internal note is hidden from the creator.
    staff = await auth_headers(engineer)
    current = await client.get(f"/api/v1/incidents/{ticket.id}", headers=staff)
    note = await client.post(
        f"/api/v1/incidents/{ticket.id}/notes",
        headers={**staff, "If-Match": f'"{current.json()["updated_at"]}"'},
        json={"body": "Photo of the panel", "visibility": "internal"},
    )
    internal = await upload(
        client, staff, ticket.id, storage, JPEG, note_id=note.json()["id"]
    )
    assert internal.status_code == 200, internal.text
    employee_list = await client.get(f"/api/v1/incidents/{ticket.id}/attachments", headers=creator)
    assert [item["id"] for item in employee_list.json()["items"]] == [accepted.json()["id"]]
    staff_list = await client.get(f"/api/v1/incidents/{ticket.id}/attachments", headers=staff)
    assert staff_list.json()["total"] == 2
    hidden = await client.get(f"/api/v1/attachments/{internal.json()['id']}", headers=creator)
    assert hidden.status_code == 404
    link = await client.get(f"/api/v1/attachments/{accepted.json()['id']}", headers=creator)
    assert link.status_code == 200 and link.json()["url"].endswith("?signed")
    timeline = await client.get(f"/api/v1/incidents/{ticket.id}/timeline", headers=creator)
    added = [item for item in timeline.json()["items"] if item["type"] == "attachment_added"]
    assert len(added) == 1
    for _ in range(3):
        assert (await upload(client, creator, ticket.id, storage, JPEG)).status_code == 200
    limit = await upload(client, creator, ticket.id, storage, JPEG)
    assert limit.status_code == 409
    assert limit.json()["error"]["code"] == "attachment_limit"


async def test_attachments_unconfigured_return_503(client, employee, make_incident, auth_headers):
    ticket = await make_incident()
    result = await client.post(
        f"/api/v1/incidents/{ticket.id}/attachments/presign",
        headers=await auth_headers(employee),
        json={"content_type": "image/jpeg", "size_bytes": 10},
    )
    assert result.status_code == 503


async def test_bulk_seats_and_default_seat(client, admin, employee, reference_data, auth_headers):
    headers = await auth_headers(admin)
    floor_id = reference_data["floor"].id
    created = await client.post(
        f"/api/v1/floors/{floor_id}/seats/bulk",
        headers=headers,
        json={"prefix": "3-A-", "from": 1, "to": 40, "pad": 2},
    )
    assert created.status_code == 201, created.text
    codes = [item["code"] for item in created.json()["items"]]
    assert codes[0] == "3-A-01" and codes[-1] == "3-A-40" and len(codes) == 40
    again = await client.post(
        f"/api/v1/floors/{floor_id}/seats/bulk",
        headers=headers,
        json={"prefix": "3-A-", "from": 40, "to": 41, "pad": 2},
    )
    assert again.status_code == 409
    assert again.json()["error"]["details"]["codes"] == ["3-A-40"]
    seat_id = created.json()["items"][0]["id"]
    mine = await client.patch(
        "/api/v1/users/me", headers=await auth_headers(employee), json={"default_seat_id": seat_id}
    )
    assert mine.status_code == 200, mine.text
    assert mine.json()["default_seat_id"] == seat_id


async def test_engineers_need_exactly_two_skills(client, admin, auth_headers):
    headers = await auth_headers(admin)
    for skills in (["Furniture"], ["Furniture", "Cleaning", "HVAC and temperature"]):
        result = await client.post(
            "/api/v1/engineers",
            headers=headers,
            json={"full_name": "New Tech", "email": "tech@acme.inc", "skills": skills},
        )
        assert result.status_code == 422


async def test_team_dashboard_scope_and_totals(
    client, db, admin, make_user, engineer, make_incident, auth_headers
):
    lead = await make_user(full_name="Lead", roles=["lead"])
    team = Team(name="Desk Support", lead_user_id=lead.id)
    db.add(team)
    await db.flush()
    profile = await db.scalar(select(EngineerProfile).where(EngineerProfile.user_id == engineer.id))
    profile.team_id = team.id
    await db.commit()
    resolved_at = utcnow() - timedelta(days=1)
    ticket = await make_incident(
        status="Resolved", resolved_at=resolved_at, created_at=resolved_at - timedelta(hours=2)
    )
    db.add(
        IncidentEvent(
            incident_id=ticket.id,
            actor_id=engineer.id,
            type="status_changed",
            payload={"from": "In Progress", "to": "Resolved", "assigned_to": str(engineer.id)},
            created_at=resolved_at,
        )
    )
    await db.commit()
    await make_incident(status="Blocked", blocked_reason="Other")
    summary = await client.get(
        "/api/v1/dashboard/team?period=week&tz=America/New_York",
        headers=await auth_headers(lead),
    )
    assert summary.status_code == 200, summary.text
    body = summary.json()
    assert body["totals"]["resolved"]["value"] == 1
    assert body["totals"]["on_time_ratio"]["value"] == 1.0
    assert body["totals"]["blocked_now"]["value"] == 1
    assert body["engineers"][0]["resolved"] == 1
    assert sum(item["count"] for item in body["charts"]["resolved_over_time"]) == 1
    assert len(body["charts"]["resolved_over_time"]) in (7, 8)
    other = await make_user(full_name="Other lead", roles=["lead"])
    db.add(Team(name="Other team", lead_user_id=other.id))
    await db.commit()
    denied = await client.get(
        f"/api/v1/dashboard/team?team_id={team.id}", headers=await auth_headers(other)
    )
    assert denied.status_code == 404
    as_admin = await client.get(
        f"/api/v1/dashboard/team?team_id={team.id}&period=quarter",
        headers=await auth_headers(admin),
    )
    assert as_admin.status_code == 200
    bad_zone = await client.get(
        "/api/v1/dashboard/team?tz=Mars/Base", headers=await auth_headers(lead)
    )
    assert bad_zone.status_code == 422


async def test_admin_dashboard_company_and_routing_widgets(
    client, admin, make_incident, reference_data, auth_headers
):
    old = utcnow() - timedelta(days=60)
    await make_incident(status="Open", created_at=old)
    await make_incident(status="Open")
    result = await client.get("/api/v1/dashboard/admin", headers=await auth_headers(admin))
    assert result.status_code == 200, result.text
    body = result.json()
    # Current-state widgets include the 60-day-old open ticket (N17).
    assert sum(item["count"] for item in body["status_counts"]) == 2
    assert body["company"]["totals"]["created"]["value"] == 1
    assert body["company"]["totals"]["open_now"]["value"] == 2
    rate = body["company"]["incident_rate_by_building"][0]
    assert rate["active_seats"] == 1 and rate["rate_per_100_seats"] == 100.0
    assert body["company"]["top_problems"][0]["share"] == 1.0
    assert "routing_health" in body and "communication_health" in body
    assert body["lifecycle"]["resolved"]["median_seconds"] is None
