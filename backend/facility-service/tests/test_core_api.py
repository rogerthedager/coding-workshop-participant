"""Real PostgreSQL acceptance tests for the employee/admin/engineer lifecycle."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from facility.models import IncidentEvent, IncidentNote, utcnow


def version(headers: dict, item: dict) -> dict:
    """Attach the optimistic version returned by the preceding API response."""
    return {**headers, "If-Match": item["updated_at"]}


async def detail(client, incident_id, headers):
    """Read the latest incident version and fail close to an unexpected response."""
    result = await client.get(f"/api/v1/incidents/{incident_id}", headers=headers)
    assert result.status_code == 200, result.text
    return result.json()


@pytest.mark.integration
async def test_full_employee_admin_engineer_lifecycle(
    client,
    db,
    employee,
    admin,
    engineer,
    auth_headers,
    reference_data,
):
    """Create, assign, block, unblock, resolve and employee-confirm one incident."""
    creator = await auth_headers(employee)
    manager = await auth_headers(admin)
    technician = await auth_headers(engineer)
    result = await client.post(
        "/api/v1/incidents",
        headers=creator,
        json={
            "title": "Meeting room projector is broken",
            "description": "The HDMI input remains blank after reconnecting the cable.",
            "building_id": str(reference_data["building"].id),
            "floor_id": str(reference_data["floor"].id),
            "seat_id": str(reference_data["seat"].id),
            "category_id": str(reference_data["category"].id),
            "requested_priority": "High",
        },
    )
    assert result.status_code == 201, result.text
    current = result.json()
    incident_id = current["id"]
    assert current["status"] == "Open"
    result = await client.post(
        f"/api/v1/incidents/{incident_id}/assign",
        headers=version(manager, current),
        json={"assigned_to": str(engineer.id)},
    )
    assert result.status_code == 200, result.text
    current = result.json()
    assert current["assigned_at"] is not None
    for status, reason in [
        ("In Progress", None),
        ("Blocked", "Waiting on parts"),
        ("In Progress", "The replacement adapter has arrived"),
        ("Resolved", "Replaced the adapter and verified the HDMI input"),
    ]:
        result = await client.post(
            f"/api/v1/incidents/{incident_id}/transitions",
            headers=version(technician, current),
            json={"status": status, "reason": reason, "note": reason},
        )
        assert result.status_code == 200, result.text
        current = result.json()
        assert current["status"] == status
    assert current["acknowledged_at"] is not None
    assert current["resolved_at"] is not None
    result = await client.post(
        f"/api/v1/incidents/{incident_id}/transitions",
        headers=version(creator, current),
        json={"status": "Closed", "reason": "Verified the projector works"},
    )
    assert result.status_code == 200, result.text
    assert result.json()["closed_at"] is not None
    assert await db.scalar(select(func.count()).select_from(IncidentEvent)) >= 6
    blocked = await client.post(
        f"/api/v1/incidents/{incident_id}/notes",
        headers=version(creator, result.json()),
        json={"body": "Late change"},
    )
    assert blocked.status_code == 409


@pytest.mark.integration
async def test_versions_hierarchy_and_assignment_guards(
    client,
    db,
    employee,
    admin,
    engineer,
    auth_headers,
    reference_data,
    make_incident,
):
    """Reject stale writes, forged locations and unavailable or invalid assignees."""
    creator = await auth_headers(employee)
    manager = await auth_headers(admin)
    item = await make_incident(assigned_to=None)
    current = await detail(client, item.id, creator)
    path = f"/api/v1/incidents/{item.id}"
    missing = await client.patch(path, headers=creator, json={"title": "Updated projector"})
    assert missing.status_code == 428
    modified = await client.patch(
        path,
        headers=version(creator, current),
        json={
            "title": "Updated projector issue",
        },
    )
    assert modified.status_code == 200, modified.text
    stale = await client.patch(
        path,
        headers=version(creator, current),
        json={
            "title": "This stale change must fail",
        },
    )
    assert stale.status_code == 409
    current = modified.json()
    invalid = await client.post(
        f"{path}/assign",
        headers=version(manager, current),
        json={"assigned_to": str(employee.id)},
    )
    assert invalid.status_code in {409, 422}
    await db.refresh(item)
    assert item.assigned_to is None
    created = await client.post(
        "/api/v1/incidents",
        headers=creator,
        json={
            "title": "Invalid location hierarchy",
            "description": "This seat is not in that floor.",
            "building_id": str(reference_data["building"].id),
            "floor_id": str(uuid4()),
            "seat_id": str(reference_data["seat"].id),
            "category_id": str(reference_data["category"].id),
        },
    )
    assert created.status_code in {404, 409, 422}


@pytest.mark.integration
async def test_note_visibility_edit_window_and_hide_retains_audit(
    client,
    db,
    employee,
    admin,
    engineer,
    auth_headers,
    make_incident,
):
    """Prevent internal/hidden note leakage while retaining the original record."""
    creator = await auth_headers(employee)
    manager = await auth_headers(admin)
    technician = await auth_headers(engineer)
    incident = await make_incident()
    current = await detail(client, incident.id, technician)
    result = await client.post(
        f"/api/v1/incidents/{incident.id}/notes",
        headers=version(technician, current),
        json={"body": "INTERNAL-ADAPTER-STOCK", "visibility": "internal"},
    )
    assert result.status_code == 201, result.text
    internal = result.json()
    creator_notes = await client.get(f"/api/v1/incidents/{incident.id}/notes", headers=creator)
    assert "INTERNAL-ADAPTER-STOCK" not in creator_notes.text
    timeline = await client.get(f"/api/v1/incidents/{incident.id}/timeline", headers=creator)
    assert "INTERNAL-ADAPTER-STOCK" not in timeline.text
    current = await detail(client, incident.id, creator)
    result = await client.post(
        f"/api/v1/incidents/{incident.id}/notes",
        headers=version(creator, current),
        json={"body": "PUBLIC-ORIGINAL-NOTE", "visibility": "public"},
    )
    assert result.status_code == 201, result.text
    public = result.json()
    edited = await client.patch(
        f"/api/v1/notes/{public['id']}",
        headers=version(creator, public),
        json={"body": "PUBLIC-EDITED-NOTE"},
    )
    assert edited.status_code == 200, edited.text
    hidden = await client.post(
        f"/api/v1/notes/{public['id']}/hide",
        headers=version(manager, edited.json()),
        json={"reason": "Contains confidential employee information"},
    )
    assert hidden.status_code == 200, hidden.text
    for suffix in ("notes", "timeline"):
        listing = await client.get(f"/api/v1/incidents/{incident.id}/{suffix}", headers=creator)
        assert "PUBLIC-EDITED-NOTE" not in listing.text
        assert "INTERNAL-ADAPTER-STOCK" not in listing.text
    saved = await db.get(IncidentNote, public["id"])
    assert saved is not None and saved.body == "PUBLIC-EDITED-NOTE"
    assert saved.hidden_at is not None and saved.hidden_by == admin.id
    old_note = await db.get(IncidentNote, internal["id"])
    old_note.created_at = utcnow() - timedelta(minutes=16)
    await db.commit()
    await db.refresh(old_note)
    too_late = await client.patch(
        f"/api/v1/notes/{internal['id']}",
        headers={**technician, "If-Match": old_note.updated_at.isoformat()},
        json={"body": "Outside the correction window"},
    )
    assert too_late.status_code == 409


@pytest.mark.integration
async def test_escalation_deduplicates_and_requires_greater_priority(
    client,
    employee,
    admin,
    auth_headers,
    make_incident,
):
    """Escalation approvals must make a meaningful change and remain unique."""
    creator = await auth_headers(employee)
    manager = await auth_headers(admin)
    incident = await make_incident(priority="Medium")
    path = f"/api/v1/incidents/{incident.id}"
    current = await detail(client, incident.id, creator)
    pending = await client.post(
        f"{path}/escalation",
        headers=version(creator, current),
        json={"reason": "This blocks the entire customer workshop"},
    )
    assert pending.status_code in {200, 201}, pending.text
    current = await detail(client, incident.id, creator)
    duplicate = await client.post(
        f"{path}/escalation",
        headers=version(creator, current),
        json={"reason": "Repeated request must not create a second pending escalation"},
    )
    assert duplicate.status_code == 409
    current = await detail(client, incident.id, manager)
    invalid = await client.post(
        f"{path}/escalation/decision",
        headers=version(manager, current),
        json={"approved": True, "priority": "Medium", "decision_reason": "No increase"},
    )
    assert invalid.status_code in {409, 422}
    current = await detail(client, incident.id, manager)
    approved = await client.post(
        f"{path}/escalation/decision",
        headers=version(manager, current),
        json={
            "approved": True,
            "priority": "High",
            "decision_reason": "Business impact verified",
        },
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["priority"] == "High"
    assert approved.json()["latest_escalation"]["state"] == "Approved"


@pytest.mark.integration
async def test_search_filter_and_ownership_are_applied_together(
    client,
    employee,
    other_employee,
    admin,
    auth_headers,
    make_incident,
):
    """Search must neither reveal another employee's matches nor ignore filters."""
    await make_incident(title="Unique projector alpha", priority="High")
    await make_incident(title="Unique projector beta", priority="Low")
    await make_incident(
        title="Unique projector confidential",
        created_by=other_employee.id,
        priority="High",
    )
    own = await client.get(
        "/api/v1/incidents?q=Unique%20projector&priority=High",
        headers=await auth_headers(employee),
    )
    assert own.status_code == 200, own.text
    assert own.json()["total"] == 1
    assert own.json()["items"][0]["title"] == "Unique projector alpha"
    all_matches = await client.get(
        "/api/v1/incidents?q=Unique%20projector&priority=High",
        headers=await auth_headers(admin),
    )
    assert all_matches.status_code == 200
    assert all_matches.json()["total"] == 2
    bad_page = await client.get("/api/v1/incidents?page=0", headers=await auth_headers(employee))
    assert bad_page.status_code == 422
