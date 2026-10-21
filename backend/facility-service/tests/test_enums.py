"""Domain enums remain typed across PostgreSQL, HTTP inputs and serialized output."""

import json

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, StatementError

from facility.enums import (
    AssigneeFilter,
    BlockedReason,
    EngineerAvailability,
    EscalationFilter,
    EscalationState,
    IncidentEventType,
    IncidentPriority,
    IncidentScope,
    IncidentSort,
    IncidentStatus,
    NoteVisibility,
    SortDirection,
    UserRole,
)
from facility.main import app
from facility.models import (
    EngineerProfile,
    EscalationRequest,
    Incident,
    IncidentEvent,
    IncidentNote,
    User,
)


@pytest.mark.integration
async def test_all_scalar_enum_values_roundtrip_as_members(db, employee, engineer, make_incident):
    """Every supported domain value is read as its enum while SQL retains old strings."""
    incident = await make_incident()
    profile = await db.scalar(select(EngineerProfile).where(EngineerProfile.user_id == engineer.id))
    note = IncidentNote(incident_id=incident.id, author_id=employee.id, body="Enum roundtrip note")
    audit = IncidentEvent(
        incident_id=incident.id, actor_id=employee.id, type=IncidentEventType.CREATED
    )
    escalation = EscalationRequest(
        incident_id=incident.id, requester_id=employee.id, reason="A valid escalation reason"
    )
    db.add_all([note, audit, escalation])
    await db.commit()
    for instance, field, enum in [
        (incident, "status", IncidentStatus),
        (incident, "priority", IncidentPriority),
        (incident, "requested_priority", IncidentPriority),
        (incident, "blocked_reason", BlockedReason),
        (profile, "availability", EngineerAvailability),
        (note, "visibility", NoteVisibility),
        (audit, "type", IncidentEventType),
        (escalation, "state", EscalationState),
    ]:
        for member in enum:
            # Legacy callers supplying a valid string must also acquire the domain type.
            setattr(instance, field, member.value)
            await db.commit()
            await db.refresh(instance)
            assert getattr(instance, field) is member
            stored = await db.scalar(
                text(f'SELECT "{field}" FROM "{instance.__tablename__}" WHERE id=:id'),
                {"id": instance.id},
            )
            assert type(stored) is str and stored == member.value
    incident.blocked_reason = None
    await db.commit()
    await db.refresh(incident)
    assert incident.blocked_reason is None


@pytest.mark.integration
async def test_roles_roundtrip_as_enum_members_in_jsonb(db, employee):
    """Roles retain their JSON array contract, including implicit Employee's empty array."""
    for supplied, expected in [
        ([], []),
        ([UserRole.ADMIN], [UserRole.ADMIN]),
        (["engineer", "admin"], [UserRole.ENGINEER, UserRole.ADMIN]),
    ]:
        employee.roles = supplied
        await db.commit()
        await db.refresh(employee)
        assert employee.roles == expected
        assert all(type(role) is UserRole for role in employee.roles)
        raw = (
            await db.execute(
                text("SELECT roles, pg_typeof(roles)::text FROM users WHERE id=:id"),
                {"id": employee.id},
            )
        ).one()
        assert raw[0] == [member.value for member in expected]
        assert raw[1] == "jsonb"
    # Keep the JSONB contains operator available for existing authorization queries.
    found = await db.scalar(select(User).where(User.roles.contains([UserRole.ADMIN])))
    assert found is employee


@pytest.mark.parametrize(
    ("model", "field", "invalid"),
    [
        (Incident, "status", "IN_PROGRESS"),
        (Incident, "priority", "Urgent"),
        (Incident, "requested_priority", "medium"),
        (Incident, "blocked_reason", "Vendor part unavailable"),
        (EngineerProfile, "availability", "Away"),
        (IncidentNote, "visibility", "private"),
        (IncidentEvent, "type", "unknown_event"),
        (EscalationRequest, "state", "none"),
        (User, "roles", ["employee"]),
        (User, "roles", "admin"),
        (User, "roles", [None]),
    ],
)
def test_orm_rejects_invalid_domain_assignments(model, field, invalid):
    """Service code cannot accidentally introduce unsupported workflow or role values."""
    with pytest.raises((ValueError, TypeError, LookupError, StatementError)):
        model(**{field: invalid})


@pytest.mark.integration
async def test_postgresql_rejects_invalid_values_bypassing_orm(
    db, employee, engineer, make_incident
):
    """Database CHECKs protect every enum even when a client bypasses Python validation."""
    incident = await make_incident()
    profile = await db.scalar(select(EngineerProfile).where(EngineerProfile.user_id == engineer.id))
    note = IncidentNote(
        incident_id=incident.id, author_id=employee.id, body="Database constraint test"
    )
    audit = IncidentEvent(
        incident_id=incident.id, actor_id=employee.id, type=IncidentEventType.CREATED
    )
    escalation = EscalationRequest(
        incident_id=incident.id, requester_id=employee.id, reason="Database constraint test"
    )
    db.add_all([note, audit, escalation])
    await db.commit()
    for instance, field, invalid in [
        (incident, "status", "IN_PROGRESS"),
        (incident, "priority", "Urgent"),
        (incident, "requested_priority", "medium"),
        (incident, "blocked_reason", "Vendor part unavailable"),
        (profile, "availability", "Away"),
        (note, "visibility", "private"),
        (audit, "type", "unknown_event"),
        (escalation, "state", "none"),
        (employee, "roles", json.dumps(["administrator"])),
        (employee, "roles", json.dumps([1])),
        (employee, "roles", json.dumps("admin")),
    ]:
        value = "CAST(:value AS jsonb)" if field == "roles" else ":value"
        with pytest.raises(IntegrityError) as rejected:
            async with db.begin_nested():
                await db.execute(
                    text(f'UPDATE "{instance.__tablename__}" SET "{field}"={value} WHERE id=:id'),
                    {"value": invalid, "id": instance.id},
                )
        assert rejected.value.orig.sqlstate == "23514"  # CHECK violation, not a cast error.


def _refs(value):
    """Collect schema references without depending on nullable/union schema ordering."""
    if isinstance(value, dict):
        return (
            {value["$ref"]}
            if "$ref" in value
            else set().union(*(_refs(item) for item in value.values()))
        )
    if isinstance(value, list):
        return set().union(*(_refs(item) for item in value))
    return set()


def test_openapi_exposes_shared_domain_enums_and_query_sentinels():
    """Clients generate one shared enum per domain instead of separate literal unions."""
    document = app.openapi()
    schemas = document["components"]["schemas"]
    for enum in [
        IncidentStatus,
        IncidentPriority,
        NoteVisibility,
        UserRole,
        EngineerAvailability,
        EscalationState,
        IncidentScope,
        IncidentSort,
        SortDirection,
        AssigneeFilter,
        EscalationFilter,
    ]:
        assert schemas[enum.__name__]["enum"] == [member.value for member in enum]
    for payload, field, enum in [
        ("IncidentCreate", "requested_priority", IncidentPriority),
        ("PriorityChange", "priority", IncidentPriority),
        ("Transition", "status", IncidentStatus),
        ("Transition", "visibility", NoteVisibility),
        ("NoteCreate", "visibility", NoteVisibility),
        ("UserUpdate", "roles", UserRole),
        ("AvailabilityUpdate", "availability", EngineerAvailability),
    ]:
        assert f"#/components/schemas/{enum.__name__}" in _refs(
            schemas[payload]["properties"][field]
        )
    parameters = {
        item["name"]: item["schema"]
        for item in document["paths"]["/api/v1/incidents"]["get"]["parameters"]
    }
    for name, enums in [
        ("status", [IncidentStatus]),
        ("priority", [IncidentPriority]),
        ("scope", [IncidentScope]),
        ("sort", [IncidentSort]),
        ("direction", [SortDirection]),
        ("assigned_to", [AssigneeFilter]),
        ("escalation", [EscalationState, EscalationFilter]),
    ]:
        for enum in enums:
            assert f"#/components/schemas/{enum.__name__}" in _refs(parameters[name])


@pytest.mark.integration
async def test_existing_http_wire_values_and_query_sentinels(
    client, db, employee, admin, engineer, auth_headers, make_incident
):
    """Enum names never leak into JSON, and absence sentinels remain valid query values."""
    ticket = await make_incident(
        status=IncidentStatus.IN_PROGRESS,
        priority=IncidentPriority.HIGH,
        requested_priority=IncidentPriority.LOW,
        assigned_to=None,
    )
    another = await make_incident(priority=IncidentPriority.CRITICAL)
    db.add(
        EscalationRequest(
            incident_id=another.id,
            requester_id=employee.id,
            reason="Waiting on an urgent response",
            state=EscalationState.PENDING,
        )
    )
    await db.commit()
    manager = await auth_headers(admin)
    detail = await client.get(f"/api/v1/incidents/{ticket.id}", headers=manager)
    assert detail.status_code == 200, detail.text
    assert detail.json()["status"] == "In Progress"
    assert detail.json()["priority"] == "High"
    assert detail.json()["requested_priority"] == "Low"
    filtered = await client.get(
        "/api/v1/incidents",
        params={"assigned_to": "unassigned", "escalation": "none", "scope": "all"},
        headers=manager,
    )
    assert filtered.status_code == 200, filtered.text
    assert [item["id"] for item in filtered.json()["items"]] == [str(ticket.id)]
    pending = await client.get(
        "/api/v1/incidents", params={"escalation": "Pending"}, headers=manager
    )
    assert [item["id"] for item in pending.json()["items"]] == [str(another.id)]
    ordered = await client.get(
        "/api/v1/incidents", params={"sort": "priority", "direction": "desc"}, headers=manager
    )
    assert ordered.status_code == 200, ordered.text
    assert [item["priority"] for item in ordered.json()["items"]] == ["Critical", "High"]
    directory = await client.get("/api/v1/engineers", headers=manager)
    assert directory.status_code == 200, directory.text
    profile = next(
        item for item in directory.json()["items"] if item["user_id"] == str(engineer.id)
    )
    assert profile["availability"] == "Available"
    assert profile["user"]["roles"] == ["engineer"]


@pytest.mark.integration
async def test_http_rejects_invalid_enum_inputs(
    client, admin, engineer, employee, auth_headers, make_incident, reference_data
):
    """Validation rejects enum names, misspellings and unsupported roles before mutation."""
    ticket = await make_incident()
    manager = await auth_headers(admin)
    for key, invalid in [
        ("status", "IN_PROGRESS"),
        ("priority", "Urgent"),
        ("scope", "everyone"),
        ("sort", "status"),
        ("direction", "descending"),
        ("assigned_to", "nobody"),
        ("escalation", "None"),
    ]:
        result = await client.get("/api/v1/incidents", params={key: invalid}, headers=manager)
        assert result.status_code == 422, (key, result.text)
        assert result.json()["error"]["code"] == "validation_error"
    for method, path, body, headers in [
        ("post", f"/api/v1/incidents/{ticket.id}/transitions", {"status": "IN_PROGRESS"}, manager),
        (
            "patch",
            f"/api/v1/incidents/{ticket.id}/priority",
            {"priority": "Urgent", "reason": "Raise priority"},
            manager,
        ),
        (
            "post",
            f"/api/v1/incidents/{ticket.id}/notes",
            {"body": "Invalid audience", "visibility": "private"},
            manager,
        ),
        ("patch", f"/api/v1/users/{employee.id}", {"roles": ["employee"]}, manager),
        (
            "patch",
            "/api/v1/engineers/me/availability",
            {"availability": "Away"},
            await auth_headers(engineer),
        ),
        (
            "post",
            "/api/v1/incidents",
            {
                "title": "Invalid requested priority",
                "description": "Reject an invalid priority.",
                "category_id": str(reference_data["category"].id),
                "building_id": str(reference_data["building"].id),
                "floor_id": str(reference_data["floor"].id),
                "requested_priority": "Normal",
            },
            manager,
        ),
    ]:
        result = await client.request(
            method, path, json=body, headers={**headers, "If-Match": ticket.updated_at.isoformat()}
        )
        assert result.status_code == 422, (path, result.text)
