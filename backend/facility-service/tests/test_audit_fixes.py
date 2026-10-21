"""Fixes from the MVP audit: error codes, sessions, default seat, cancel, follower privacy, CSV."""

import csv
import io

import httpx

from facility.config import settings
from facility.main import app

ORIGIN = {"Origin": "http://evil.example"}


async def version(client, headers, incident_id) -> dict:
    current = await client.get(f"/api/v1/incidents/{incident_id}", headers=headers)
    assert current.status_code == 200, current.text
    return {**headers, "If-Match": f'"{current.json()["updated_at"]}"'}


async def test_expired_access_token_has_its_own_code(client, employee, auth_headers, monkeypatch):
    monkeypatch.setattr(settings, "access_minutes", -5)
    headers = await auth_headers(employee)
    result = await client.get("/api/v1/auth/me", headers=headers)
    assert result.status_code == 401
    assert result.json()["error"]["code"] == "token_expired"
    bad = await client.get("/api/v1/auth/me", headers={"Authorization": "Bearer nonsense"})
    assert bad.json()["error"]["code"] == "unauthenticated"


async def test_forced_password_change_keeps_session(client, make_user, auth_headers):
    user = await make_user(must_change_password=True)
    headers = await auth_headers(user)
    # Refresh must work during the forced flow, or a page reload signs the user out.
    refreshed = await client.post("/api/v1/auth/refresh")
    assert refreshed.status_code == 200, refreshed.text
    headers = {"Authorization": f"Bearer {refreshed.json()['access_token']}"}
    wrong = await client.post(
        "/api/v1/auth/change-password",
        headers=headers,
        json={"current_password": "NotThePassword1", "new_password": "BrandNewPass123"},
    )
    assert wrong.status_code == 422
    assert wrong.json()["error"]["code"] == "invalid_current_password"
    # The session is still usable after a wrong attempt.
    assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 200


async def test_register_codes_and_origin(client):
    body = {"email": "dupe.check@acme.inc", "full_name": "Dupe", "password": "Password1234"}
    assert (await client.post("/api/v1/auth/register", json=body)).status_code == 201
    again = await client.post("/api/v1/auth/register", json=body)
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "duplicate_email"
    other = {**body, "email": "cross.site@acme.inc"}
    blocked = await client.post("/api/v1/auth/register", json=other, headers=ORIGIN)
    assert blocked.status_code == 403


async def test_change_password_checks_origin(client, employee, auth_headers):
    headers = {**await auth_headers(employee), **ORIGIN}
    result = await client.post(
        "/api/v1/auth/change-password",
        headers=headers,
        json={"current_password": "x", "new_password": "BrandNewPass123"},
    )
    assert result.status_code == 403


async def test_wrong_skill_count_is_skills_invalid(client, admin, auth_headers):
    result = await client.post(
        "/api/v1/engineers",
        headers=await auth_headers(admin),
        json={
            "full_name": "One Skill",
            "email": "one.skill@acme.inc",
            "skills": ["Furniture"],
        },
    )
    assert result.status_code == 422
    assert result.json()["error"]["code"] == "skills_invalid"


async def test_create_uses_default_seat(client, db, employee, auth_headers, reference_data):
    headers = await auth_headers(employee)
    body = {
        "title": "Desk lamp flickers",
        "description": "The lamp flickers every few seconds.",
        "category_id": str(reference_data["category"].id),
    }
    missing = await client.post("/api/v1/incidents", headers=headers, json=body)
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "location_required"
    employee.default_seat_id = reference_data["seat"].id
    await db.merge(employee)
    await db.commit()
    created = await client.post("/api/v1/incidents", headers=headers, json=body)
    assert created.status_code == 201, created.text
    ticket = created.json()
    assert ticket["seat_id"] == str(reference_data["seat"].id)
    assert ticket["floor_id"] == str(reference_data["floor"].id)
    assert ticket["building_id"] == str(reference_data["building"].id)


async def test_creator_cancels_assigned_open_ticket(
    client, employee, make_incident, auth_headers
):
    ticket = await make_incident()
    assert ticket.assigned_to is not None
    headers = await auth_headers(employee)
    cancelled = await client.post(
        f"/api/v1/incidents/{ticket.id}/transitions",
        headers=await version(client, headers, ticket.id),
        json={"status": "Cancelled", "reason": "Fixed it myself"},
    )
    assert cancelled.status_code == 200, cancelled.text


async def test_follower_cannot_identify_creator(
    client, employee, other_employee, make_incident, auth_headers
):
    ticket = await make_incident()
    creator = await auth_headers(employee)
    note = await client.post(
        f"/api/v1/incidents/{ticket.id}/notes",
        headers=await version(client, creator, ticket.id),
        json={"body": "Still broken this morning", "visibility": "public"},
    )
    assert note.status_code == 201, note.text
    follower = await auth_headers(other_employee)
    assert (
        await client.post(f"/api/v1/incidents/{ticket.id}/follow", headers=follower)
    ).status_code == 204
    creator_id = str(employee.id)
    notes = await client.get(f"/api/v1/incidents/{ticket.id}/notes", headers=follower)
    timeline = await client.get(f"/api/v1/incidents/{ticket.id}/timeline", headers=follower)
    assert notes.status_code == 200 and timeline.status_code == 200
    assert creator_id not in notes.text
    assert creator_id not in timeline.text
    # The creator still sees their own identity.
    own = await client.get(f"/api/v1/incidents/{ticket.id}/notes", headers=creator)
    assert creator_id in own.text


async def test_csv_export_is_filtered_and_escaped(
    client, employee, other_employee, make_incident, auth_headers
):
    mine = await make_incident(title="=HYPERLINK(evil)")
    await make_incident(created_by=other_employee.id, title="Somebody else's ticket")
    result = await client.get(
        "/api/v1/incidents/export.csv",
        headers=await auth_headers(employee),
        params={"status": "Open"},
    )
    assert result.status_code == 200, result.text
    assert result.headers["content-type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(result.text)))
    assert rows[0][0] == "number"
    assert [row[0] for row in rows[1:]] == [mine.number]
    assert rows[1][1] == "'=HYPERLINK(evil)"
    bogus = await client.get(
        "/api/v1/incidents/export.csv", headers=await auth_headers(employee), params={"x": "1"}
    )
    assert bogus.status_code == 422


async def test_unexpected_error_returns_request_id():
    async def boom() -> None:
        raise RuntimeError("secret detail")

    app.add_api_route("/__test_boom", boom)
    try:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            result = await c.get("/__test_boom")
    finally:
        app.router.routes = [r for r in app.router.routes if getattr(r, "path", "") != "/__test_boom"]
    assert result.status_code == 500
    request_id = result.headers.get("X-Request-ID")
    assert request_id
    assert result.json()["error"]["details"]["request_id"] == request_id
    assert "secret detail" not in result.text


async def test_login_spends_bcrypt_time_for_unknown_accounts(client, monkeypatch):
    from facility.routers import auth

    calls = []
    real = auth.verify_password
    monkeypatch.setattr(auth, "verify_password", lambda p, h: calls.append(h) or real(p, h))
    result = await client.post(
        "/api/v1/auth/login", json={"email": "nobody.here@acme.inc", "password": "Password1234"}
    )
    assert result.status_code == 401
    assert len(calls) == 1


async def test_facility_codes_are_case_insensitive_and_search_is_literal(
    client, admin, auth_headers, reference_data
):
    headers = await auth_headers(admin)
    floor = reference_data["floor"].id
    seat = await client.post(f"/api/v1/floors/{floor}/seats", headers=headers, json={"code": "n-101"})
    assert seat.status_code == 409
    assert seat.json()["error"]["code"] == "duplicate"
    building = await client.post(
        "/api/v1/buildings", headers=headers, json={"name": "south campus", "code": "north"}
    )
    assert building.status_code == 409
    literal = await client.get("/api/v1/buildings", headers=headers, params={"q": "%"})
    assert literal.json()["total"] == 0
    found = await client.get("/api/v1/buildings", headers=headers, params={"q": "north"})
    assert found.json()["total"] == 1


async def test_location_guard_code(client, admin, auth_headers, make_incident, reference_data):
    await make_incident()
    result = await client.patch(
        f"/api/v1/seats/{reference_data['seat'].id}",
        headers=await auth_headers(admin),
        json={"is_active": False},
    )
    assert result.status_code == 409
    error = result.json()["error"]
    assert error["code"] == "has_open_incidents"
    assert error["details"]["count"] == 1


async def test_category_cannot_join_inactive_group(client, db, admin, auth_headers):
    from facility.models import CategoryGroup

    group = CategoryGroup(name="Retired group", is_active=False, sort_order=0)
    db.add(group)
    await db.commit()
    result = await client.post(
        "/api/v1/categories",
        headers=await auth_headers(admin),
        json={"name": "New top level", "group_id": str(group.id)},
    )
    assert result.status_code == 422
    assert result.json()["error"]["code"] == "group_inactive"


async def test_finalize_before_upload_keeps_attachment_pending(
    client, employee, make_incident, auth_headers
):
    from facility.storage import set_storage
    from tests.test_attachments_dashboards import JPEG, FakeStorage

    fake = FakeStorage()
    set_storage(fake)
    try:
        ticket = await make_incident()
        headers = await auth_headers(employee)
        presigned = await client.post(
            f"/api/v1/incidents/{ticket.id}/attachments/presign",
            headers=headers,
            json={"content_type": "image/jpeg", "size_bytes": len(JPEG)},
        )
        assert presigned.status_code == 201, presigned.text
        body = {"attachment_id": presigned.json()["attachment_id"]}
        early = await client.post(f"/api/v1/incidents/{ticket.id}/attachments", headers=headers, json=body)
        assert early.status_code == 409
        assert early.json()["error"]["code"] == "upload_incomplete"
        fake.objects[presigned.json()["upload"]["fields"]["key"]] = JPEG
        later = await client.post(f"/api/v1/incidents/{ticket.id}/attachments", headers=headers, json=body)
        assert later.status_code == 200, later.text
    finally:
        set_storage(None)
