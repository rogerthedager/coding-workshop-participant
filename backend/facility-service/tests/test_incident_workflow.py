"""Focused PostgreSQL regressions for workflow, escalation and note boundaries."""

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from facility.models import Category, EscalationRequest, IncidentEvent, IncidentNote, utcnow


def matched(headers, record):
    """Use the record's explicit version for the next write."""
    return {**headers, "If-Match": record["updated_at"]}


async def current(client, path, headers):
    """Read a fresh authorized incident projection."""
    result = await client.get(path, headers=headers)
    assert result.status_code == 200, result.text
    return result.json()


@pytest.mark.integration
async def test_resolution_rejection_records_distinct_history_and_reopen_count(
    client,
    employee,
    engineer,
    auth_headers,
    make_incident,
):
    """Reopening keeps the earlier resolution event and requires a public note."""
    creator = await auth_headers(employee)
    technician = await auth_headers(engineer)
    item = await make_incident(status="In Progress")
    path = f"/api/v1/incidents/{item.id}"
    state = await current(client, path, technician)
    for invalid in (
        {"status": "Resolved"},
        {
            "status": "Resolved",
            "note": "Private resolution is invalid",
            "visibility": "internal",
        },
    ):
        result = await client.post(
            path + "/transitions", headers=matched(technician, state), json=invalid
        )
        assert result.status_code == 422
    result = await client.post(
        path + "/transitions",
        headers=matched(technician, state),
        json={
            "status": "Resolved",
            "note": "Reconnected and verified the HDMI adapter",
        },
    )
    assert result.status_code == 200, result.text
    resolved = result.json()
    result = await client.post(
        path + "/transitions",
        headers=matched(creator, resolved),
        json={
            "status": "In Progress",
        },
    )
    assert result.status_code == 422
    result = await client.post(
        path + "/transitions",
        headers=matched(creator, resolved),
        json={
            "status": "In Progress",
            "note": "The image still flickers during presentation",
        },
    )
    assert result.status_code == 200, result.text
    reopened = result.json()
    assert reopened["reopen_count"] == 1 and reopened["resolved_at"] is None
    timeline = (await client.get(path + "/timeline", headers=creator)).json()["items"]
    transitions = [entry for entry in timeline if entry["type"] == "status_changed"]
    assert [entry["payload"]["to"] for entry in transitions] == [
        "Resolved",
        "In Progress",
    ]
    assert transitions[0]["payload"]["assigned_to"] == str(engineer.id)
    assert transitions[1]["payload"]["previous_resolved_at"] == resolved["resolved_at"]


@pytest.mark.integration
async def test_cancel_supersedes_pending_and_final_rejects_mutations(
    client,
    employee,
    admin,
    auth_headers,
    make_incident,
    db,
):
    """Cancellation and escalation supersession are one persisted transaction."""
    creator = await auth_headers(employee)
    manager = await auth_headers(admin)
    item = await make_incident(assigned_to=None)
    path = f"/api/v1/incidents/{item.id}"
    state = await current(client, path, creator)
    result = await client.post(
        path + "/escalation",
        headers=matched(creator, state),
        json={
            "reason": "An important presentation cannot begin today",
        },
    )
    assert result.status_code == 201, result.text
    state = result.json()
    result = await client.post(
        path + "/transitions",
        headers=matched(creator, state),
        json={
            "status": "Cancelled",
            "reason": "A different room now meets this requirement",
        },
    )
    assert result.status_code == 200, result.text
    state = result.json()
    assert state["latest_escalation"]["state"] == "Superseded"
    assert state["cancelled_at"] is not None
    attempts = [
        ("patch", path, creator, {"title": "Final title cannot change"}),
        ("post", path + "/notes", creator, {"body": "Final note cannot be added"}),
        (
            "post",
            path + "/escalation",
            creator,
            {"reason": "Final escalation cannot proceed"},
        ),
        (
            "patch",
            path + "/priority",
            manager,
            {"priority": "High", "reason": "Invalid final mutation"},
        ),
        (
            "post",
            path + "/assign",
            manager,
            {"assigned_to": None, "reason": "Invalid final unassignment"},
        ),
        ("post", path + "/transitions", creator, {"status": "Closed"}),
    ]
    for method, endpoint, headers, payload in attempts:
        rejected = await client.request(
            method, endpoint, headers=matched(headers, state), json=payload
        )
        assert rejected.status_code == 409, (endpoint, rejected.text)
    assert await db.scalar(select(func.count()).select_from(EscalationRequest)) == 1


@pytest.mark.integration
async def test_assignee_creator_requires_explicit_resolution_then_confirmation(
    client,
    engineer,
    auth_headers,
    make_incident,
):
    """Additive creator permission never skips the separate Resolved state."""
    headers = await auth_headers(engineer)
    item = await make_incident(
        created_by=engineer.id, assigned_to=engineer.id, status="In Progress"
    )
    path = f"/api/v1/incidents/{item.id}"
    state = await current(client, path, headers)
    invalid = await client.post(
        path + "/transitions",
        headers=matched(headers, state),
        json={"status": "Closed"},
    )
    assert invalid.status_code == 409
    resolved = await client.post(
        path + "/transitions",
        headers=matched(headers, state),
        json={
            "status": "Resolved",
            "note": "The cable was replaced and output verified",
        },
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == "Resolved"
    closed = await client.post(
        path + "/transitions",
        headers=matched(headers, resolved.json()),
        json={"status": "Closed"},
    )
    assert closed.status_code == 200, closed.text
    timeline = (await client.get(path + "/timeline", headers=headers)).json()["items"]
    assert [event["payload"]["to"] for event in timeline if event["type"] == "status_changed"] == [
        "Resolved",
        "Closed",
    ]


@pytest.mark.integration
async def test_critical_action_plan_and_rejection_are_hide_aware(
    client,
    db,
    employee,
    admin,
    auth_headers,
    make_incident,
):
    """Hiding an escalation note also redacts duplicated decision projections."""
    creator = await auth_headers(employee)
    manager = await auth_headers(admin)
    for approved, secret in [
        (True, "CRITICAL-PUBLIC-ACTION-PLAN"),
        (False, "PUBLIC-REJECTION-REASON"),
    ]:
        item = await make_incident(priority="Critical")
        path = f"/api/v1/incidents/{item.id}"
        state = await current(client, path, creator)
        pending = await client.post(
            path + "/escalation",
            headers=matched(creator, state),
            json={
                "reason": "This requires immediate management involvement",
            },
        )
        assert pending.status_code == 201, pending.text
        state = pending.json()
        if approved:
            missing = await client.post(
                path + "/escalation/decision",
                headers=matched(manager, state),
                json={"approved": True},
            )
            assert missing.status_code == 422
        decision = await client.post(
            path + "/escalation/decision",
            headers=matched(manager, state),
            json={
                "approved": approved,
                **({"action_plan": secret} if approved else {"decision_reason": secret}),
            },
        )
        assert decision.status_code == 200, decision.text
        notes = (await client.get(path + "/notes", headers=manager)).json()["items"]
        note = next(entry for entry in notes if entry.get("body") == secret)
        hidden = await client.post(
            f"/api/v1/notes/{note['id']}/hide",
            headers=matched(manager, note),
            json={"reason": "Confidential details"},
        )
        assert hidden.status_code == 200, hidden.text
        for headers in (creator, manager):
            for suffix in ("", "/timeline", "/notes"):
                result = await client.get(path + suffix, headers=headers)
                assert result.status_code == 200
                assert secret not in result.text
            listing = await client.get("/api/v1/incidents", headers=headers)
            assert secret not in listing.text
        saved = await db.get(IncidentNote, UUID(note["id"]))
        assert saved.body == secret and saved.hidden_at is not None


@pytest.mark.integration
async def test_escalation_cooldown_uses_request_time_and_assignment_is_atomic(
    client,
    db,
    employee,
    admin,
    engineer,
    auth_headers,
    make_incident,
):
    """Rejected requests cannot be retried immediately or partially reassign work."""
    creator = await auth_headers(employee)
    manager = await auth_headers(admin)
    item = await make_incident(assigned_to=None)
    path = f"/api/v1/incidents/{item.id}"
    state = await current(client, path, creator)
    result = await client.post(
        path + "/escalation",
        headers=matched(creator, state),
        json={"reason": "Meeting capacity is critically affected today"},
    )
    assert result.status_code == 201
    state = result.json()
    invalid = await client.post(
        path + "/escalation/decision",
        headers=matched(manager, state),
        json={
            "approved": True,
            "priority": "High",
            "assigned_to": str(employee.id),
        },
    )
    assert invalid.status_code == 422
    fresh = await current(client, path, manager)
    assert fresh["priority"] == "Medium"
    assert fresh["assigned_to"] is None
    assert fresh["latest_escalation"]["state"] == "Pending"
    result = await client.post(
        path + "/escalation/decision",
        headers=matched(manager, fresh),
        json={
            "approved": False,
            "decision_reason": "Workaround is available pending the maintenance window",
            "assigned_to": str(engineer.id),
        },
    )
    assert result.status_code == 200, result.text
    state = result.json()
    cooldown = await client.post(
        path + "/escalation",
        headers=matched(creator, state),
        json={"reason": "Need to submit another urgent request now"},
    )
    assert cooldown.status_code == 409
    request = await db.scalar(
        select(EscalationRequest).where(EscalationRequest.incident_id == item.id)
    )
    request.requested_at = utcnow() - timedelta(hours=24, seconds=1)
    await db.commit()
    state = await current(client, path, creator)
    again = await client.post(
        path + "/escalation",
        headers=matched(creator, state),
        json={"reason": "The next daily escalation remains necessary"},
    )
    assert again.status_code == 201, again.text


@pytest.mark.integration
async def test_assignment_rules_and_blocking_resume_timestamp_history(
    client,
    admin,
    engineer,
    other_engineer,
    auth_headers,
    make_incident,
):
    """Work in progress stays assigned and blocked intervals remain in audit."""
    manager = await auth_headers(admin)
    technician = await auth_headers(engineer)
    item = await make_incident(status="In Progress", assigned_at=utcnow() - timedelta(hours=1))
    path = f"/api/v1/incidents/{item.id}"
    state = await current(client, path, manager)
    invalid = await client.post(
        path + "/assign",
        headers=matched(manager, state),
        json={"assigned_to": None, "reason": "Attempted unassignment"},
    )
    assert invalid.status_code == 409
    missing_reason = await client.post(
        path + "/assign",
        headers=matched(manager, state),
        json={"assigned_to": str(other_engineer.id)},
    )
    assert missing_reason.status_code == 422
    blocked = await client.post(
        path + "/transitions",
        headers=matched(technician, state),
        json={
            "status": "Blocked",
            "reason": "Waiting on parts",
            "note": "An adapter is ordered",
            "visibility": "internal",
        },
    )
    assert blocked.status_code == 200, blocked.text
    first_assigned = blocked.json()["assigned_at"]
    reassigned = await client.post(
        path + "/assign",
        headers=matched(manager, blocked.json()),
        json={
            "assigned_to": str(other_engineer.id),
            "reason": "Shift handover",
        },
    )
    assert reassigned.status_code == 200, reassigned.text
    assert reassigned.json()["assigned_at"] == first_assigned
    resumed = await client.post(
        path + "/transitions",
        headers=matched(manager, reassigned.json()),
        json={"status": "In Progress"},
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["blocked_reason"] is None and resumed.json()["blocked_since"] is None
    timeline = (await client.get(path + "/timeline", headers=manager)).json()["items"]
    resume_event = next(
        entry
        for entry in timeline
        if entry["type"] == "status_changed" and entry["payload"]["from"] == "Blocked"
    )
    assert resume_event["payload"]["blocked_since"] == blocked.json()["blocked_since"]


@pytest.mark.integration
async def test_note_exact_ten_minute_boundary_and_final_hide_exception(
    client,
    db,
    employee,
    admin,
    auth_headers,
    make_incident,
    monkeypatch,
):
    """Author edits stop at ten minutes; audited admin hiding remains allowed."""
    creator = await auth_headers(employee)
    manager = await auth_headers(admin)
    item = await make_incident(assigned_to=None)
    path = f"/api/v1/incidents/{item.id}"
    state = await current(client, path, creator)
    added = await client.post(
        path + "/notes",
        headers=matched(creator, state),
        json={"body": "Employee correction before deadline"},
    )
    assert added.status_code == 201
    note = added.json()
    stored = await db.get(IncidentNote, UUID(note["id"]))
    boundary = stored.created_at + timedelta(minutes=10)
    monkeypatch.setattr(
        "facility.routers.incidents.utcnow",
        lambda: boundary - timedelta(microseconds=1),
    )
    corrected = await client.patch(
        f"/api/v1/notes/{note['id']}",
        headers=matched(creator, note),
        json={"body": "Last instant correction"},
    )
    assert corrected.status_code == 200, corrected.text
    monkeypatch.setattr("facility.routers.incidents.utcnow", lambda: boundary)
    expired = await client.patch(
        f"/api/v1/notes/{note['id']}",
        headers=matched(creator, corrected.json()),
        json={"body": "Exactly ten minutes is too late"},
    )
    assert expired.status_code == 409
    state = await current(client, path, creator)
    cancelled = await client.post(
        path + "/transitions",
        headers=matched(creator, state),
        json={"status": "Cancelled", "reason": "Reported in error"},
    )
    assert cancelled.status_code == 200
    hidden = await client.post(
        f"/api/v1/notes/{note['id']}/hide",
        headers=matched(manager, corrected.json()),
        json={"reason": "Audit-only redaction on final incident"},
    )
    assert hidden.status_code == 200, hidden.text
    repeat = await client.post(
        f"/api/v1/notes/{note['id']}/hide",
        headers=matched(manager, hidden.json()),
        json={"reason": "Idempotent retry"},
    )
    assert repeat.status_code == 200
    assert (
        await db.scalar(
            select(func.count())
            .select_from(IncidentEvent)
            .where(IncidentEvent.type == "note_hidden")
        )
        == 1
    )


@pytest.mark.integration
async def test_persona_scope_filters_reproduce_dashboard_ticket_cohorts(
    client,
    employee,
    admin,
    engineer,
    auth_headers,
    make_incident,
):
    """A staff user's employee dashboard links stay limited to their own reports."""
    await make_incident(created_by=engineer.id, assigned_to=None)
    await make_incident(created_by=employee.id, assigned_to=engineer.id)
    await make_incident(created_by=admin.id, assigned_to=None)
    technician = await auth_headers(engineer)
    mine = await client.get("/api/v1/incidents?scope=mine", headers=technician)
    assert mine.status_code == 200, mine.text
    assert mine.json()["total"] == 1
    assert mine.json()["items"][0]["created_by"] == str(engineer.id)
    assigned = await client.get("/api/v1/incidents?scope=assigned", headers=technician)
    assert assigned.status_code == 200
    assert assigned.json()["total"] == 1
    assert assigned.json()["items"][0]["assigned_to"] == str(engineer.id)
    all_visible = await client.get("/api/v1/incidents", headers=technician)
    assert all_visible.json()["total"] == 2
    manager = await auth_headers(admin)
    own_admin = await client.get("/api/v1/incidents?scope=mine", headers=manager)
    assert own_admin.json()["total"] == 1
    forbidden = await client.get(
        "/api/v1/incidents?scope=assigned", headers=await auth_headers(employee)
    )
    assert forbidden.status_code == 403


@pytest.mark.integration
async def test_search_category_descendants_location_dates_and_priority_age_order(
    client,
    db,
    admin,
    employee,
    auth_headers,
    make_incident,
    reference_data,
):
    """Dashboard filters combine without losing hierarchy, ownership or triage order."""
    root = reference_data["category"]
    child = Category(name="Projector equipment", parent_id=root.id, is_active=True)
    db.add(child)
    await db.commit()
    now = utcnow()
    oldest = await make_incident(
        category_id=child.id,
        priority="High",
        assigned_to=None,
        created_at=now - timedelta(days=2),
        title="Alpha projector failure",
    )
    newer = await make_incident(
        category_id=root.id,
        priority="High",
        assigned_to=None,
        seat_id=None,
        created_at=now - timedelta(days=1),
        title="Beta projector failure",
    )
    critical = await make_incident(priority="Critical", created_at=now)
    manager = await auth_headers(admin)
    parent_filtered = await client.get(
        "/api/v1/incidents",
        headers=manager,
        params={"category_id": str(root.id), "sort": "priority", "direction": "desc"},
    )
    assert parent_filtered.status_code == 200, parent_filtered.text
    assert [item["id"] for item in parent_filtered.json()["items"]] == [
        str(critical.id),
        str(oldest.id),
        str(newer.id),
    ]
    exact = await client.get(
        "/api/v1/incidents", headers=manager, params={"q": oldest.number.lower()}
    )
    assert exact.json()["total"] == 1
    unassigned = await client.get("/api/v1/incidents?assigned_to=unassigned", headers=manager)
    assert unassigned.json()["total"] == 2
    located = await client.get(
        "/api/v1/incidents",
        headers=manager,
        params={
            "seat_id": str(reference_data["seat"].id),
            "priority": "High",
        },
    )
    assert located.json()["total"] == 1
    dated = await client.get(
        "/api/v1/incidents",
        headers=manager,
        params={
            "date_from": (now - timedelta(days=1, hours=1)).isoformat(),
            "date_to": (now - timedelta(hours=1)).isoformat(),
        },
    )
    assert dated.json()["total"] == 1
    assert dated.json()["items"][0]["id"] == str(newer.id)
    for params in (
        {"unsupported_filter": "value"},
        {"date_from": "2026-10-20T00:00:00"},
        {"date_from": now.isoformat(), "date_to": (now - timedelta(days=1)).isoformat()},
        {"unassigned": True, "assigned_to": str(employee.id)},
    ):
        invalid = await client.get("/api/v1/incidents", headers=manager, params=params)
        assert invalid.status_code == 422, invalid.text


@pytest.mark.integration
async def test_inactive_historical_category_does_not_block_unrelated_detail_edits(
    client,
    db,
    employee,
    auth_headers,
    make_incident,
    reference_data,
):
    """Retained history survives category deactivation without enabling new selection."""
    creator = await auth_headers(employee)
    item = await make_incident(assigned_to=None)
    path = f"/api/v1/incidents/{item.id}"
    category = reference_data["category"]
    category.is_active = False
    await db.commit()
    state = await current(client, path, creator)
    title_change = await client.patch(
        path,
        headers=matched(creator, state),
        json={
            "title": "Updated details retain historical category",
        },
    )
    assert title_change.status_code == 200, title_change.text
    assert title_change.json()["category_id"] == str(category.id)
    location_change = await client.patch(
        path, headers=matched(creator, title_change.json()), json={"seat_id": None}
    )
    assert location_change.status_code == 200, location_change.text
    assert location_change.json()["category_id"] == str(category.id)
    new_selection = await client.patch(
        path,
        headers=matched(creator, location_change.json()),
        json={"category_id": str(category.id)},
    )
    assert new_selection.status_code == 422


@pytest.mark.integration
async def test_nested_note_errors_cannot_reveal_existence_of_an_unreadable_parent(
    client,
    employee,
    other_employee,
    auth_headers,
    make_incident,
):
    """Missing notes and notes on another employee's ticket share the same error."""
    creator = await auth_headers(employee)
    outsider = await auth_headers(other_employee)
    item = await make_incident(assigned_to=None)
    state = await current(client, f"/api/v1/incidents/{item.id}", creator)
    added = await client.post(
        f"/api/v1/incidents/{item.id}/notes",
        headers=matched(creator, state),
        json={"body": "Visible only to the authorized incident audience"},
    )
    assert added.status_code == 201
    responses = []
    for note_id in (added.json()["id"], str(uuid4())):
        response = await client.patch(
            f"/api/v1/notes/{note_id}",
            headers=matched(outsider, added.json()),
            json={"body": "Unauthorized attempt"},
        )
        assert response.status_code == 404
        responses.append(response.json()["error"]["message"])
    assert responses == ["Note not found", "Note not found"]
