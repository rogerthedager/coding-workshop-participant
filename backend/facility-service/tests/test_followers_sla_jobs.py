"""Following, duplicate close, notifications, SLA timers and the scheduled jobs."""

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from facility.jobs import run_jobs
from facility.models import Incident, IncidentEvent, IncidentFollower, Notification, utcnow
from facility.sla import sla_state


async def version(client, headers, incident_id) -> dict:
    current = await client.get(f"/api/v1/incidents/{incident_id}", headers=headers)
    assert current.status_code == 200, current.text
    return {**headers, "If-Match": f'"{current.json()["updated_at"]}"'}


async def test_similar_and_follow_give_reduced_projection(
    client, db, employee, other_employee, engineer, make_incident, reference_data, auth_headers
):
    ticket = await make_incident()
    other = await auth_headers(other_employee)
    similar = await client.get(
        "/api/v1/incidents/similar",
        headers=other,
        params={
            "category_id": str(reference_data["category"].id),
            "floor_id": str(reference_data["floor"].id),
        },
    )
    assert similar.status_code == 200, similar.text
    preview = similar.json()["items"][0]
    assert preview["id"] == str(ticket.id)
    assert "description" not in preview and "created_by" not in preview
    assert (await client.get(f"/api/v1/incidents/{ticket.id}", headers=other)).status_code == 404
    followed = await client.post(f"/api/v1/incidents/{ticket.id}/follow", headers=other)
    assert followed.status_code == 204
    view = await client.get(f"/api/v1/incidents/{ticket.id}", headers=other)
    assert view.status_code == 200
    body = view.json()
    assert body["following"] is True
    assert "description" not in body and "created_by" not in body
    note = await client.post(
        f"/api/v1/incidents/{ticket.id}/notes",
        headers={**other, "If-Match": f'"{body["updated_at"]}"'},
        json={"body": "Me too", "visibility": "public"},
    )
    assert note.status_code == 403
    mine = await client.get("/api/v1/incidents?followed=true", headers=other)
    assert mine.json()["total"] == 1
    # The follower is told about status changes, the actor is not.
    started = await client.post(
        f"/api/v1/incidents/{ticket.id}/transitions",
        headers=await version(client, await auth_headers(engineer), ticket.id),
        json={"status": "In Progress"},
    )
    assert started.status_code == 200, started.text
    count = await client.get("/api/v1/notifications/unread-count", headers=other)
    assert count.json() == {"unread": 1}
    listing = await client.get("/api/v1/notifications", headers=other)
    assert listing.json()["items"][0]["type"] == "status_changed"
    assert "In Progress" in listing.json()["items"][0]["message"]
    actor = await client.get(
        "/api/v1/notifications/unread-count", headers=await auth_headers(engineer)
    )
    assert actor.json() == {"unread": 0}
    read = await client.post("/api/v1/notifications/read-all", headers=other)
    assert read.status_code == 204
    after = await client.get("/api/v1/notifications/unread-count", headers=other)
    assert after.json() == {"unread": 0}
    left = await client.delete(f"/api/v1/incidents/{ticket.id}/follow", headers=other)
    assert left.status_code == 204


async def test_old_or_unrelated_tickets_cannot_be_followed(
    client, other_employee, make_incident, auth_headers
):
    stale = await make_incident(created_at=utcnow() - timedelta(days=8))
    result = await client.post(
        f"/api/v1/incidents/{stale.id}/follow", headers=await auth_headers(other_employee)
    )
    assert result.status_code == 404


async def test_internal_note_notifies_only_assignee(
    client, db, admin, employee, engineer, make_incident, auth_headers
):
    ticket = await make_incident()
    result = await client.post(
        f"/api/v1/incidents/{ticket.id}/notes",
        headers=await version(client, await auth_headers(admin), ticket.id),
        json={"body": "Check the breaker first", "visibility": "internal"},
    )
    assert result.status_code == 201, result.text
    rows = (await db.scalars(select(Notification))).all()
    assert [(row.user_id, row.type) for row in rows] == [(engineer.id, "note_added")]
    assert "breaker" not in rows[0].message


async def test_duplicate_close_rules(
    client, db, admin, employee, other_employee, make_incident, auth_headers
):
    target = await make_incident(created_by=other_employee.id)
    duplicate = await make_incident()
    headers = await auth_headers(admin)
    result = await client.post(
        f"/api/v1/incidents/{duplicate.id}/duplicate",
        headers=await version(client, headers, duplicate.id),
        json={"duplicate_of_id": str(target.id), "reason": "Same projector"},
    )
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "Closed"
    assert result.json()["close_kind"] == "duplicate"
    follower = await db.scalar(
        select(IncidentFollower).where(
            IncidentFollower.incident_id == target.id, IncidentFollower.user_id == employee.id
        )
    )
    assert follower is not None
    creator_view = await client.get(
        f"/api/v1/incidents/{target.id}", headers=await auth_headers(employee)
    )
    assert creator_view.status_code == 200
    third = await make_incident()
    chain = await client.post(
        f"/api/v1/incidents/{third.id}/duplicate",
        headers=await version(client, headers, third.id),
        json={"duplicate_of_id": str(duplicate.id), "reason": "Chain"},
    )
    assert chain.status_code == 409
    assert chain.json()["error"]["code"] == "invalid_duplicate_target"
    timeline = await client.get(
        f"/api/v1/incidents/{duplicate.id}/timeline", headers=await auth_headers(employee)
    )
    marked = [item for item in timeline.json()["items"] if item["type"] == "duplicate_marked"]
    assert "reason" not in marked[0]["payload"], "duplicate reasons are staff-only"


async def test_due_times_follow_priority_and_sla_filter(
    client, db, admin, employee, make_incident, auth_headers
):
    ticket = await make_incident(priority="Medium")
    assert ticket.resolve_due_at - ticket.created_at == timedelta(hours=72)
    headers = await auth_headers(admin)
    changed = await client.patch(
        f"/api/v1/incidents/{ticket.id}/priority",
        headers=await version(client, headers, ticket.id),
        json={"priority": "Critical", "reason": "Exec meeting"},
    )
    assert changed.status_code == 200, changed.text
    await db.refresh(ticket)
    assert ticket.ack_due_at - ticket.created_at == timedelta(hours=1)
    assert ticket.resolve_due_at - ticket.created_at == timedelta(hours=4)
    late = await make_incident(created_at=utcnow() - timedelta(hours=5), priority="Critical")
    assert sla_state(late).value == "breached"
    breached = await client.get("/api/v1/incidents?sla_state=breached", headers=headers)
    assert [item["id"] for item in breached.json()["items"]] == [str(late.id)]
    assert breached.json()["items"][0]["sla_state"] == "breached"
    ordered = await client.get("/api/v1/incidents?sort=sla_due&direction=asc", headers=headers)
    assert ordered.json()["items"][0]["id"] == str(late.id)


async def test_jobs_auto_close_and_alert_once(db, admin, employee, engineer, make_incident):
    sessions = async_sessionmaker(db.bind, expire_on_commit=False)
    old = await make_incident(
        status="Resolved",
        resolved_at=utcnow() - timedelta(hours=73),
        acknowledged_at=utcnow() - timedelta(hours=80),
    )
    fresh = await make_incident(status="Resolved", resolved_at=utcnow() - timedelta(hours=10))
    overdue = await make_incident(created_at=utcnow() - timedelta(hours=30), priority="High")
    first = await run_jobs(sessions)
    second = await run_jobs(sessions)
    assert first["auto_closed"] == 1 and second["auto_closed"] == 0
    await db.refresh(old)
    await db.refresh(fresh)
    assert old.status == "Closed" and old.close_kind == "auto"
    assert fresh.status == "Resolved"
    closures = await db.scalar(
        select(func.count(IncidentEvent.id)).where(
            IncidentEvent.incident_id == old.id, IncidentEvent.payload["to"].astext == "Closed"
        )
    )
    assert closures == 1
    await db.refresh(overdue)
    assert overdue.ack_breach_alerted_at is not None
    assert overdue.resolve_breach_alerted_at is not None
    alerts = (
        await db.scalars(
            select(Notification).where(
                Notification.incident_id == overdue.id, Notification.user_id == engineer.id
            )
        )
    ).all()
    assert sorted(item.type for item in alerts) == [
        "sla_at_risk",
        "sla_at_risk",
        "sla_breached",
        "sla_breached",
    ]
    assert second["sla_alerts"] == 0
    creator_notes = await db.scalar(
        select(func.count(Notification.id)).where(
            Notification.incident_id == old.id, Notification.user_id == employee.id
        )
    )
    assert creator_notes == 1
    dry = await run_jobs(sessions, dry_run=True)
    assert dry == {"status": "ok", "dry_run": True}
    remaining = await db.scalar(
        select(func.count(Incident.id)).where(Incident.status == "Resolved")
    )
    assert remaining == 1
