"""Verify real migrations, seed repeatability and the deployable HTTP adapter."""

from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from facility.models import Category, Incident, IncidentNote, User, utcnow


@pytest.mark.integration
async def test_frozen_migration_runs_on_empty_schema_twice(db):
    """Exercise the checked-in migration rather than relying only on metadata.create_all."""
    schema = "acme_migration_" + uuid4().hex
    connection = await db.connection()
    original_schema = await connection.scalar(text("SELECT current_schema()"))
    await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    try:
        await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        config = Config(str(Path(__file__).parents[1] / "alembic.ini"))

        def migrate(sync_connection):
            config.attributes["connection"] = sync_connection
            command.upgrade(config, "head")
            command.upgrade(config, "head")

        await connection.run_sync(migrate)
        assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == "0002"
        assert await connection.scalar(text("SELECT nextval('incident_number_seq')")) == 1
        tables = await connection.scalar(
            text("SELECT count(*) FROM information_schema.tables WHERE table_schema=:schema"),
            {"schema": schema},
        )
        assert tables == 12  # eleven entities plus the Alembic version table
        indexes = await connection.scalar(
            text(
                "SELECT count(*) FROM pg_indexes WHERE schemaname=:schema AND indexname='ix_incidents_search'"
            ),
            {"schema": schema},
        )
        assert indexes == 1
    finally:
        await connection.execute(text(f'SET LOCAL search_path TO "{original_schema}"'))
        await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await db.commit()


@pytest.mark.integration
async def test_reference_and_demo_seed_are_idempotent(db, monkeypatch):
    """Repeated setup retains existing credentials, notes and user-edited records."""
    from facility import seed as module

    monkeypatch.setattr(module, "SessionLocal", async_sessionmaker(db.bind, expire_on_commit=False))
    monkeypatch.setattr(
        module.settings, "database_url", "postgresql+asyncpg://local/acme_facility_dev"
    )
    monkeypatch.setattr(module.settings, "is_local", True)
    monkeypatch.delenv("SEED_ADMIN_EMAIL", raising=False)
    first = await module.seed(include_demo=True)
    assert first["inserted"] > 0
    await db.rollback()
    person = await db.scalar(select(User).where(User.email == "employee@acme.inc"))
    original_hash = person.password_hash
    person.full_name = "Keep my edited name"
    await db.commit()
    notes = await db.scalar(select(func.count()).select_from(IncidentNote))
    tickets = await db.scalar(select(func.count()).select_from(Incident))
    categories = await db.scalar(select(func.count()).select_from(Category))
    assert (await module.seed(include_demo=True))["inserted"] == 0
    await db.refresh(person)
    assert person.full_name == "Keep my edited name"
    assert person.password_hash == original_hash
    assert await db.scalar(select(func.count()).select_from(IncidentNote)) == notes
    assert await db.scalar(select(func.count()).select_from(Incident)) == tickets
    assert await db.scalar(select(func.count()).select_from(Category)) == categories


@pytest.mark.integration
async def test_dashboard_metrics_sql_and_hidden_communication(
    client,
    db,
    admin,
    employee,
    engineer,
    auth_headers,
    make_incident,
):
    """Counts and lifecycle sample sizes agree with known persisted records."""
    now = utcnow()
    ticket = await make_incident(
        created_by=employee.id,
        assigned_to=engineer.id,
        status="Blocked",
        blocked_reason="Waiting on parts",
        blocked_since=now - timedelta(hours=1),
        created_at=now - timedelta(hours=4),
        assigned_at=now - timedelta(hours=3),
        acknowledged_at=now - timedelta(hours=2),
    )
    db.add_all(
        [
            IncidentNote(
                incident_id=ticket.id,
                author_id=engineer.id,
                body="public-safe",
                visibility="public",
            ),
            IncidentNote(
                incident_id=ticket.id,
                author_id=engineer.id,
                body="never-visible-in-report",
                visibility="internal",
            ),
            IncidentNote(
                incident_id=ticket.id,
                author_id=engineer.id,
                body="hidden-body",
                visibility="public",
                hidden_at=now,
            ),
        ]
    )
    await db.commit()
    manager = await auth_headers(admin)
    report = await client.get("/api/v1/dashboard/admin", headers=manager)
    assert report.status_code == 200, report.text
    result = report.json()
    assert sum(row["count"] for row in result["status_counts"]) == 1
    assert result["lifecycle"]["assigned"]["sample_count"] == 1
    assert result["lifecycle"]["assigned"]["mean_seconds"] == pytest.approx(3600)
    assert result["lifecycle"]["resolved"]["sample_count"] == 0
    assert result["blocked"][0]["blocked_reason"] == "Waiting on parts"
    creator = await auth_headers(employee)
    public = await client.get("/api/v1/dashboard/employee", headers=creator)
    assert "public-safe" in public.text
    assert "never-visible-in-report" not in public.text and "hidden-body" not in public.text


def test_workshop_http_adapter_preserves_path_and_auth(monkeypatch):
    """The additive entrypoint preserves authentication and If-Match through prefix stripping."""
    import function

    captured = {}

    def adapter(event, context):
        captured.update(event)
        return {"statusCode": 200}

    monkeypatch.setattr(function, "adapter", adapter)
    event = {
        "rawPath": "/api/facility-service/api/v1/incidents",
        "headers": {
            "authorization": "Bearer test",
            "if-match": "version",
            "cookie": "refresh_token=test",
        },
        "requestContext": {"http": {"path": "/api/facility-service/api/v1/incidents"}},
    }
    assert function.handler(event, None)["statusCode"] == 200
    assert captured["rawPath"] == "/api/v1/incidents"
    assert captured["requestContext"]["http"]["path"] == "/api/v1/incidents"
    assert captured["headers"] == event["headers"]
    assert event["rawPath"].startswith("/api/facility-service")
    with pytest.raises(ValueError, match="HTTP events only"):
        function.handler({"operation": "migrate"}, None)


def test_migration_not_reachable_from_http(monkeypatch):
    """No public function event can invoke schema or seed operations."""
    import migration_handler

    with pytest.raises(ValueError, match="Control-plane"):
        migration_handler.handler({"operation": "migrate", "requestContext": {}}, None)


@pytest.mark.integration
async def test_timeline_merges_current_public_notes_and_events(
    client,
    employee,
    admin,
    auth_headers,
    make_incident,
):
    """A single timeline response carries current note content and hide-safe tombstones."""
    item = await make_incident()
    creator = await auth_headers(employee)
    response = await client.post(
        f"/api/v1/incidents/{item.id}/notes",
        headers={**creator, "If-Match": item.updated_at.isoformat()},
        json={"body": "Current public timeline content"},
    )
    assert response.status_code == 201, response.text
    note = response.json()
    timeline = await client.get(f"/api/v1/incidents/{item.id}/timeline", headers=creator)
    entries = timeline.json()["items"]
    merged = [entry["note"] for entry in entries if entry["type"] == "note_added"]
    assert merged[0]["body"] == "Current public timeline content"
    manager = await auth_headers(admin)
    hidden = await client.post(
        f"/api/v1/notes/{note['id']}/hide",
        headers={**manager, "If-Match": note["updated_at"]},
        json={"reason": "Audit correction"},
    )
    assert hidden.status_code == 200, hidden.text
    timeline = await client.get(f"/api/v1/incidents/{item.id}/timeline", headers=creator)
    assert "Current public timeline content" not in timeline.text
    note_item = next(
        entry["note"] for entry in timeline.json()["items"] if entry["type"] == "note_added"
    )
    assert note_item["hidden"] is True and "body" not in note_item
