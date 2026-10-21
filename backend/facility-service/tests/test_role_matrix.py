"""Exercise each incident action and reference-data route across real user roles."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select

from facility.models import EngineerProfile, EscalationRequest, IncidentNote

INCIDENT_ACTIONS = [
    "create",
    "list",
    "read",
    "edit",
    "assign",
    "priority",
    "start",
    "escalate",
    "decide",
    "notes",
    "add_note",
    "internal_note",
    "edit_note",
    "hide_note",
    "timeline",
]


@pytest.mark.parametrize("action", INCIDENT_ACTIONS)
async def test_incident_action_role_and_ownership_matrix(
    action,
    client,
    db,
    employee,
    other_employee,
    admin,
    engineer,
    other_engineer,
    make_user,
    make_incident,
    auth_headers,
):
    """Check valid operations for creator, outsiders, assignee, admin and combined roles."""
    combined = await make_user(roles=["admin", "engineer"])
    actors = {
        "creator": employee,
        "other_employee": other_employee,
        "assigned_engineer": engineer,
        "unassigned_engineer": other_engineer,
        "admin": admin,
        "combined": combined,
    }
    for role, user in actors.items():
        headers = await auth_headers(user)
        incident = await make_incident()
        path = f"/api/v1/incidents/{incident.id}"
        method, target, payload = "GET", path, None
        headers["If-Match"] = incident.updated_at.isoformat()
        if action == "create":
            method, target, payload = (
                "POST",
                "/api/v1/incidents",
                {
                    "title": "Each role can report a workplace issue",
                    "description": "A valid self-service incident from this role.",
                    "building_id": str(incident.building_id),
                    "floor_id": str(incident.floor_id),
                    "seat_id": str(incident.seat_id),
                    "category_id": str(incident.category_id),
                },
            )
        elif action == "list":
            target = "/api/v1/incidents"
        elif action == "edit":
            method, payload = "PATCH", {"title": "A corrected incident title"}
        elif action == "assign":
            method, target, payload = (
                "POST",
                path + "/assign",
                {
                    "assigned_to": str(other_engineer.id),
                    "reason": "Balancing the available engineering workload",
                },
            )
        elif action == "priority":
            method, target, payload = (
                "PATCH",
                path + "/priority",
                {"priority": "High", "reason": "Meeting blocked"},
            )
        elif action == "start":
            method, target, payload = (
                "POST",
                path + "/transitions",
                {"status": "In Progress"},
            )
        elif action == "escalate":
            method, target, payload = (
                "POST",
                path + "/escalation",
                {"reason": "Customer workshop is blocked"},
            )
        elif action == "decide":
            db.add(
                EscalationRequest(
                    incident_id=incident.id,
                    requester_id=employee.id,
                    reason="Workshop blocked",
                )
            )
            await db.commit()
            method, target, payload = (
                "POST",
                path + "/escalation/decision",
                {
                    "approved": False,
                    "decision_reason": "Current priority fits the verified impact",
                },
            )
        elif action in {"notes", "timeline"}:
            target = path + "/" + action
        elif action in {"add_note", "internal_note"}:
            method, target, payload = (
                "POST",
                path + "/notes",
                {
                    "body": "Status update for the incident",
                    "visibility": "internal" if action == "internal_note" else "public",
                },
            )
        elif action in {"edit_note", "hide_note"}:
            note = IncidentNote(
                incident_id=incident.id,
                author_id=employee.id,
                body="Original note",
                visibility="public",
            )
            db.add(note)
            await db.commit()
            headers["If-Match"] = note.updated_at.isoformat()
            target = f"/api/v1/notes/{note.id}"
            method, payload = "PATCH", {"body": "Corrected note"}
            if action == "hide_note":
                method, target, payload = (
                    "POST",
                    target + "/hide",
                    {"reason": "Contains personal information"},
                )
        response = await client.request(method, target, headers=headers, json=payload)
        outsider = role in {"other_employee", "unassigned_engineer"}
        administrative = role in {"admin", "combined"}
        staff = administrative or role == "assigned_engineer"
        if action in {"create", "list"}:
            expected = 201 if action == "create" else 200
        elif outsider:
            expected = 404
        elif action in {"assign", "priority", "decide", "hide_note"}:
            expected = 200 if administrative else 403
        elif action in {"start", "internal_note"}:
            expected = (201 if action == "internal_note" else 200) if staff else 403
        elif action == "escalate":
            expected = 201 if role == "creator" else 403
        elif action == "edit_note":
            expected = 200 if role == "creator" else 403
        else:
            expected = 201 if action == "add_note" else 200
        assert response.status_code == expected, (
            action,
            role,
            expected,
            response.status_code,
            response.text,
        )


REFERENCE_ACTIONS = [
    ("GET", "/buildings", None),
    ("POST", "/buildings", {"name": "Extra Building", "code": "EXTRA"}),
    ("PATCH", "/buildings/{building}", {"name": "Renamed Building"}),
    ("GET", "/buildings/{building}/floors", None),
    ("POST", "/buildings/{building}/floors", {"name": "Floor Two", "level_number": 2}),
    ("PATCH", "/floors/{floor}", {"name": "Renamed Floor"}),
    ("GET", "/floors/{floor}/seats", None),
    ("POST", "/floors/{floor}/seats", {"code": "N-102"}),
    ("PATCH", "/seats/{seat}", {"code": "N-NEW"}),
    ("GET", "/categories", None),
    ("POST", "/categories", {"name": "Extra category"}),
    ("PATCH", "/categories/{category}", {"name": "Renamed Category"}),
    ("GET", "/locations/tree", None),
]


@pytest.mark.parametrize("method,path,payload", REFERENCE_ACTIONS)
async def test_reference_route_role_matrix(
    method,
    path,
    payload,
    client,
    reference_data,
    employee,
    other_employee,
    engineer,
    other_engineer,
    admin,
    make_user,
    auth_headers,
):
    """Every authenticated role can read reference data; only administrators can edit."""
    identifiers = {key: str(value.id) for key, value in reference_data.items()}
    target = "/api/v1" + path.format(**identifiers)
    combined = await make_user(roles=["admin", "engineer"])
    for index, user in enumerate(
        (employee, other_employee, engineer, other_engineer, admin, combined)
    ):
        body = dict(payload) if payload else None
        if body and method == "POST":
            for field in ("name", "code"):
                if field in body:
                    body[field] += str(index)
            if "level_number" in body:
                body["level_number"] += index
        result = await client.request(method, target, json=body, headers=await auth_headers(user))
        expected = 200 if method == "GET" else (201 if method == "POST" else 200)
        if method != "GET" and "admin" not in user.roles:
            expected = 403
        assert result.status_code == expected, (user.roles, target, result.text)


async def test_every_protected_route_rejects_anonymous_requests(client):
    """Keep the authentication guard on every HTTP operation registered in the app."""
    public = {
        "/api/v1/auth/register",
        "/api/v1/auth/login",
        "/api/v1/auth/refresh",
        "/api/v1/auth/logout",
    }
    checked = 0
    for path, operations in client_app_schema().items():
        if not path.startswith("/api/v1/") or path in public:
            continue
        concrete = path
        for segment in path.split("/"):
            if segment.startswith("{"):
                concrete = concrete.replace(segment, str(uuid4()))
        for method in operations:
            if method not in {"get", "post", "patch", "put", "delete"}:
                continue
            result = await client.request(method.upper(), concrete, json={})
            assert result.status_code == 401, (
                method,
                path,
                result.status_code,
                result.text,
            )
            checked += 1
    assert checked >= 35


def client_app_schema():
    """Read route definitions from the actual application instead of duplicating them."""
    from facility.main import app

    return app.openapi()["paths"]


PEOPLE_ACTIONS = [
    "users",
    "self",
    "update_user",
    "engineers",
    "create_engineer",
    "engineer",
    "update_engineer",
    "availability",
    "employee_dashboard",
    "engineer_dashboard",
    "admin_dashboard",
    "me",
    "change_password",
    "refresh",
    "logout",
]


@pytest.mark.parametrize("action", PEOPLE_ACTIONS)
async def test_people_dashboard_and_session_role_matrix(
    action,
    client,
    db,
    employee,
    other_employee,
    admin,
    engineer,
    other_engineer,
    make_user,
    auth_headers,
):
    """Exercise directory, dashboard and session operations for every role combination."""
    combined = await make_user(roles=["admin", "engineer"])
    profile = await db.scalar(select(EngineerProfile).where(EngineerProfile.user_id == engineer.id))
    profile_id = str(profile.id)
    target_user = str(other_employee.id)
    actors = [employee, other_employee, admin, engineer, other_engineer, combined]
    for index, user in enumerate(actors):
        headers = await auth_headers(user)
        method, path, payload = "GET", "/users", None
        if action == "self":
            method, path, payload = "PATCH", "/users/me", {"full_name": "Updated display name"}
        elif action == "update_user":
            method, path, payload = (
                "PATCH",
                f"/users/{target_user}",
                {"full_name": "Updated account name"},
            )
        elif action == "engineers":
            path = "/engineers"
        elif action == "create_engineer":
            method, path, payload = (
                "POST",
                "/engineers",
                {
                    "email": f"matrix-engineer-{index}@acme.inc",
                    "full_name": "Matrix Engineer",
                    "phone": "555-0102",
                    "temporary_password": "TemporaryPass-991!",
                },
            )
        elif action == "engineer":
            path = f"/engineers/{profile_id}"
        elif action == "update_engineer":
            method, path, payload = "PATCH", f"/engineers/{profile_id}", {"phone": "555-0103"}
        elif action == "availability":
            method, path, payload = "PATCH", "/engineers/me/availability", {"availability": "Busy"}
        elif action.endswith("_dashboard"):
            path = "/dashboard/" + action.removesuffix("_dashboard")
        elif action == "me":
            path = "/auth/me"
        elif action == "change_password":
            method, path, payload = (
                "POST",
                "/auth/change-password",
                {
                    "current_password": "Valid-Passphrase-734!",
                    "new_password": "NewPassphrase-999!",
                },
            )
        elif action in {"refresh", "logout"}:
            method, path = "POST", "/auth/" + action
        response = await client.request(method, "/api/v1" + path, headers=headers, json=payload)
        is_admin = "admin" in user.roles
        is_engineer = "engineer" in user.roles
        if action in {
            "users",
            "update_user",
            "create_engineer",
            "update_engineer",
            "admin_dashboard",
        }:
            expected = (201 if action == "create_engineer" else 200) if is_admin else 403
        elif action in {"engineers", "engineer"}:
            expected = 200 if is_admin or is_engineer else 403
        elif action in {"availability", "engineer_dashboard"}:
            expected = 200 if is_engineer else 403
        elif action == "logout":
            expected = 204
        else:
            expected = 200
        assert response.status_code == expected, (action, user.roles, expected, response.text)
