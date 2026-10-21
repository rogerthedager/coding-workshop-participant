"""Team routing at creation, return to triage, lead access and ranked suggestions."""

from typing import Any

import pytest_asyncio
from sqlalchemy import select

from facility.models import (
    Category,
    CategoryGroup,
    EngineerProfile,
    Notification,
    Team,
)


@pytest_asyncio.fixture
async def network(db, reference_data) -> dict[str, Any]:
    """A Network top-level category with a child, in a group owned by no team yet."""
    group = CategoryGroup(name="IT and AV", sort_order=0)
    db.add(group)
    await db.flush()
    top = Category(name="Network and Wi-Fi", sort_order=1, group_id=group.id)
    db.add(top)
    await db.flush()
    child = Category(name="Slow connection", parent_id=top.id, sort_order=0)
    db.add(child)
    await db.commit()
    return {**reference_data, "group": group, "top": top, "child": child}


async def set_profile(db, user, **values) -> EngineerProfile:
    profile = await db.scalar(select(EngineerProfile).where(EngineerProfile.user_id == user.id))
    for key, value in values.items():
        setattr(profile, key, value)
    await db.commit()
    return profile


@pytest_asyncio.fixture
async def it_team(db, make_user, network, engineer, other_engineer) -> dict[str, Any]:
    """IT Support team owning the IT group: lead plus two engineers."""
    lead = await make_user(full_name="IT Lead", roles=["lead"])
    team = Team(name="IT Support", lead_user_id=lead.id)
    db.add(team)
    await db.flush()
    network["group"].team_id = team.id
    await db.commit()
    await set_profile(
        db, engineer, team_id=team.id, skills=["Network and Wi-Fi", "IT and AV equipment"]
    )
    await set_profile(db, other_engineer, team_id=team.id, skills=["Furniture", "Cleaning"])
    return {**network, "team": team, "lead": lead}


def report(data, category) -> dict:
    return {
        "title": "Wi-Fi drops every few minutes",
        "description": "The wireless connection on this floor keeps dropping.",
        "category_id": str(category.id),
        "building_id": str(data["building"].id),
        "floor_id": str(data["floor"].id),
    }


async def test_new_ticket_routes_to_owning_teams_best_engineer(
    client, db, employee, engineer, it_team, auth_headers
):
    result = await client.post(
        "/api/v1/incidents",
        headers=await auth_headers(employee),
        json=report(it_team, it_team["child"]),
    )
    assert result.status_code == 201, result.text
    body = result.json()
    assert body["routing"]["status"] == "assigned"
    assert body["routing"]["assignee"]["id"] == str(engineer.id)
    assert body["assigned_to"] == str(engineer.id)
    assert body["routed_team_id"] == str(it_team["team"].id)
    assert body["triage_reason"] is None
    assert body["acknowledged_at"] is None, "routing is not acknowledgment"
    notes = (await db.scalars(select(Notification).where(Notification.user_id == engineer.id))).all()
    assert [item.type for item in notes] == ["assigned"]


async def test_unrouted_group_and_no_engineer_go_to_triage(
    client, db, employee, engineer, other_engineer, it_team, reference_data, auth_headers
):
    headers = await auth_headers(employee)
    # The fixture category has no group: admin triage.
    loose = await client.post(
        "/api/v1/incidents", headers=headers, json=report(it_team, reference_data["category"])
    )
    assert loose.json()["routing"] == {"status": "triage", "reason": "unrouted_group"}
    await set_profile(db, engineer, availability="Off-duty")
    await set_profile(db, other_engineer, availability="Off-duty")
    nobody = await client.post(
        "/api/v1/incidents", headers=headers, json=report(it_team, it_team["child"])
    )
    assert nobody.json()["routing"]["status"] == "triage"
    assert nobody.json()["routing"]["reason"] == "no_engineer"
    assert nobody.json()["assigned_to"] is None
    admin_filter = await client.get(
        "/api/v1/incidents?triage_reason=no_engineer", headers=headers
    )
    assert admin_filter.status_code == 403


async def test_return_to_triage_rules(
    client, db, admin, employee, engineer, other_engineer, it_team, auth_headers
):
    created = await client.post(
        "/api/v1/incidents",
        headers=await auth_headers(employee),
        json=report(it_team, it_team["child"]),
    )
    ticket = created.json()
    path = f"/api/v1/incidents/{ticket['id']}/return-to-triage"
    version = {"If-Match": f'"{ticket["updated_at"]}"'}
    payload = {"reason": "This is a cabling job for facilities"}
    outsider = await client.post(
        path, headers={**await auth_headers(other_engineer), **version}, json=payload
    )
    assert outsider.status_code == 404
    creator = await client.post(
        path, headers={**await auth_headers(employee), **version}, json=payload
    )
    assert creator.status_code == 403
    returned = await client.post(
        path, headers={**await auth_headers(engineer), **version}, json=payload
    )
    assert returned.status_code == 200, returned.text
    body = returned.json()
    assert body["assigned_to"] is None
    assert body["triage_reason"] == "returned"
    assert body["route_return_count"] == 1
    assert body["routed_team_id"] == str(it_team["team"].id)
    alerts = (await db.scalars(select(Notification).where(Notification.user_id == admin.id))).all()
    assert [item.type for item in alerts] == ["returned_to_triage"]
    # The returning engineer no longer reads it; In Progress cannot be returned.
    gone = await client.get(
        f"/api/v1/incidents/{ticket['id']}", headers=await auth_headers(engineer)
    )
    assert gone.status_code == 404
    admin_headers = await auth_headers(admin)
    assign = await client.post(
        f"/api/v1/incidents/{ticket['id']}/assign",
        headers={**admin_headers, "If-Match": f'"{body["updated_at"]}"'},
        json={"assigned_to": str(engineer.id)},
    )
    assert assign.status_code == 200, assign.text
    assert assign.json()["triage_reason"] is None
    started = await client.post(
        f"/api/v1/incidents/{ticket['id']}/transitions",
        headers={**await auth_headers(engineer), "If-Match": f'"{assign.json()["updated_at"]}"'},
        json={"status": "In Progress"},
    )
    assert started.status_code == 200, started.text
    late = await client.post(
        path,
        headers={**await auth_headers(engineer), "If-Match": f'"{started.json()["updated_at"]}"'},
        json=payload,
    )
    assert late.status_code == 409
    assert late.json()["error"]["code"] == "not_returnable"


async def test_lead_reads_team_tickets_but_only_returns(
    client, db, make_user, employee, engineer, it_team, auth_headers
):
    created = await client.post(
        "/api/v1/incidents",
        headers=await auth_headers(employee),
        json=report(it_team, it_team["child"]),
    )
    ticket = created.json()
    engineer_headers = await auth_headers(engineer)
    note = await client.post(
        f"/api/v1/incidents/{ticket['id']}/notes",
        headers={**engineer_headers, "If-Match": f'"{ticket["updated_at"]}"'},
        json={"body": "Internal: check the access point", "visibility": "internal"},
    )
    assert note.status_code == 201, note.text
    lead_headers = await auth_headers(it_team["lead"])
    detail = await client.get(f"/api/v1/incidents/{ticket['id']}", headers=lead_headers)
    assert detail.status_code == 200
    assert detail.json()["permissions"]["team_lead"] is True
    notes = await client.get(f"/api/v1/incidents/{ticket['id']}/notes", headers=lead_headers)
    assert [item["visibility"] for item in notes.json()["items"]] == ["internal"]
    current = detail.json()["updated_at"]
    start = await client.post(
        f"/api/v1/incidents/{ticket['id']}/transitions",
        headers={**lead_headers, "If-Match": f'"{current}"'},
        json={"status": "In Progress"},
    )
    assert start.status_code == 403
    public = await client.post(
        f"/api/v1/incidents/{ticket['id']}/notes",
        headers={**lead_headers, "If-Match": f'"{current}"'},
        json={"body": "Lead comment", "visibility": "public"},
    )
    assert public.status_code == 403
    listed = await client.get(
        f"/api/v1/incidents?team_id={it_team['team'].id}", headers=lead_headers
    )
    assert listed.json()["total"] == 1
    other_lead = await make_user(full_name="Other lead", roles=["lead"])
    db.add(Team(name="Facilities", lead_user_id=other_lead.id))
    await db.commit()
    hidden = await client.get(
        f"/api/v1/incidents/{ticket['id']}", headers=await auth_headers(other_lead)
    )
    assert hidden.status_code == 404
    returned = await client.post(
        f"/api/v1/incidents/{ticket['id']}/return-to-triage",
        headers={**lead_headers, "If-Match": f'"{current}"'},
        json={"reason": "Belongs to the facilities team"},
    )
    assert returned.status_code == 200, returned.text
    told = (await db.scalars(select(Notification).where(Notification.user_id == engineer.id))).all()
    assert "returned_to_triage" in [item.type for item in told]


async def test_engineer_suggestions_rank_skill_then_availability(
    client, db, make_user, admin, employee, it_team, auth_headers, make_incident
):
    busy_skill = await make_user(full_name="Busy Network", roles=["engineer"])
    available_skill = await make_user(full_name="Available Network", roles=["engineer"])
    no_skill = await make_user(full_name="Aaron No Skill", roles=["engineer"])
    await set_profile(
        db, busy_skill, availability="Busy", skills=["Network and Wi-Fi", "Furniture"]
    )
    await set_profile(
        db, available_skill, availability="Available", skills=["Network and Wi-Fi", "Cleaning"]
    )
    await set_profile(db, no_skill, availability="Available", skills=["Furniture", "Cleaning"])
    await make_incident(assigned_to=available_skill.id, category_id=it_team["child"].id)
    ticket = await make_incident(assigned_to=None, category_id=it_team["child"].id)
    result = await client.get(
        f"/api/v1/incidents/{ticket.id}/engineer-suggestions", headers=await auth_headers(admin)
    )
    assert result.status_code == 200, result.text
    order = [item["full_name"] for item in result.json()["items"]]
    assert order.index("Available Network") < order.index("Busy Network")
    assert order.index("Busy Network") < order.index("Aaron No Skill")
    first = result.json()["items"][0]
    assert first["skill_match"] is True and "skill" in first["match_reasons"]
    denied = await client.get(
        f"/api/v1/incidents/{ticket.id}/engineer-suggestions",
        headers=await auth_headers(employee),
    )
    assert denied.status_code == 403


async def test_team_admin_endpoints(client, db, admin, make_user, engineer, network, auth_headers):
    headers = await auth_headers(admin)
    lead = await make_user(full_name="New lead")
    created = await client.post(
        "/api/v1/teams", headers=headers, json={"name": "Network Ops", "lead_user_id": str(lead.id)}
    )
    assert created.status_code == 201, created.text
    team_id = created.json()["id"]
    members = await client.put(
        f"/api/v1/teams/{team_id}/members",
        headers=headers,
        json={"engineer_user_ids": [str(engineer.id)]},
    )
    assert members.status_code == 200, members.text
    assert [item["id"] for item in members.json()["members"]] == [str(engineer.id)]
    group = await client.patch(
        f"/api/v1/category-groups/{network['group'].id}", headers=headers, json={"team_id": team_id}
    )
    assert group.status_code == 200
    other_lead = await make_user(full_name="Second lead")
    second = await client.post(
        "/api/v1/teams", headers=headers, json={"name": "Desk", "lead_user_id": str(other_lead.id)}
    )
    moved = await client.put(
        f"/api/v1/teams/{second.json()['id']}/members",
        headers=headers,
        json={"engineer_user_ids": [str(engineer.id)]},
    )
    assert moved.status_code == 409
    assert moved.json()["error"]["code"] == "engineer_in_other_team"
    demote = await client.patch(
        f"/api/v1/users/{lead.id}", headers=headers, json={"roles": []}
    )
    assert demote.status_code == 409
    assert demote.json()["error"]["code"] == "team_needs_lead"
    me = await client.get("/api/v1/auth/me", headers=await auth_headers(lead))
    assert me.json()["leads_team"]["id"] == team_id
    assert "lead" in me.json()["roles"]
