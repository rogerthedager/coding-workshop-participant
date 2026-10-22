"""Showcase seed: every feature gets 10+ records, re-runs add nothing, cloud needs opt-in."""

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from facility.enums import EscalationState
from facility.models import (
    Attachment,
    Building,
    Category,
    EngineerProfile,
    EscalationRequest,
    Floor,
    Incident,
    IncidentEvent,
    IncidentFollower,
    IncidentNote,
    Notification,
    Seat,
    Team,
    User,
)
from facility.storage import signature_matches


@pytest.fixture
def seed_module(db, monkeypatch):
    from facility import seed as module

    monkeypatch.setattr(module, "SessionLocal", async_sessionmaker(db.bind, expire_on_commit=False))
    monkeypatch.delenv("SEED_ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("FACILITY_ALLOW_DEMO_SEED", raising=False)
    return module


def cloud(module, monkeypatch):
    monkeypatch.setattr(module.settings, "is_local", False)
    monkeypatch.setattr(module.settings, "database_url", "postgresql+asyncpg://aws/codingworkshop")


@pytest.mark.integration
async def test_showcase_fills_every_feature_and_is_idempotent(db, seed_module, monkeypatch):
    cloud(seed_module, monkeypatch)
    monkeypatch.setenv("FACILITY_ALLOW_DEMO_SEED", "true")
    monkeypatch.setenv("DEMO_PASSWORD", "Showcase-Pass-2026")
    stored = {}

    def put(key, body, content_type):
        stored[key] = (body, content_type)

    first = await seed_module.seed(incidents=120, include_demo=True, showcase=True, put_object=put)
    assert first["inserted"] > 0
    await db.rollback()

    async def count(model, *where):
        return await db.scalar(select(func.count()).select_from(model).where(*where))

    for model in [
        Building,
        Floor,
        Seat,
        Category,
        User,
        Team,
        EngineerProfile,
        Incident,
        IncidentNote,
        IncidentEvent,
        IncidentFollower,
        Notification,
        Attachment,
    ]:
        assert await count(model) >= 10, model.__name__
    assert await count(EscalationRequest, EscalationRequest.state != EscalationState.PENDING) >= 10
    assert await count(EscalationRequest, EscalationRequest.state == EscalationState.PENDING) >= 1

    # Every photo row has a stored object that passes the API's image check.
    keys = list(await db.scalars(select(Attachment.s3_key)))
    assert set(keys) <= set(stored)
    assert all(signature_matches("image/png", body[:32]) for body, _ in stored.values())

    totals = {m: await count(m) for m in [User, Incident, Notification, Attachment, Team]}
    assert (
        await seed_module.seed(incidents=120, include_demo=True, showcase=True, put_object=put)
    )["inserted"] == 0
    for model, total in totals.items():
        assert await count(model) == total


@pytest.mark.integration
async def test_cloud_demo_seed_requires_opt_in_and_strong_password(seed_module, monkeypatch):
    cloud(seed_module, monkeypatch)
    with pytest.raises(ValueError, match="restricted"):
        await seed_module.seed(include_demo=True)
    monkeypatch.setenv("FACILITY_ALLOW_DEMO_SEED", "true")
    monkeypatch.setenv("DEMO_PASSWORD", "DemoPass123!")
    with pytest.raises(ValueError, match="DEMO_PASSWORD"):
        await seed_module.seed(include_demo=True)
    monkeypatch.setenv("DEMO_PASSWORD", "short1")
    with pytest.raises(ValueError, match="DEMO_PASSWORD"):
        await seed_module.seed(include_demo=True)


@pytest.mark.integration
async def test_showcase_without_bucket_skips_photos(db, seed_module, monkeypatch):
    monkeypatch.setattr(seed_module.settings, "is_local", True)
    monkeypatch.setattr(
        seed_module.settings, "database_url", "postgresql+asyncpg://local/acme_facility_dev"
    )
    await seed_module.seed(incidents=30, include_demo=True, showcase=True)
    await db.rollback()
    assert await db.scalar(select(func.count()).select_from(Attachment)) == 0
    assert await db.scalar(select(func.count()).select_from(Team)) >= 10
    user = await db.scalar(select(User).where(User.email == "lena.ortiz@acme.inc"))
    assert user.default_seat_id is not None


@pytest.mark.integration
async def test_seeded_data_renders_through_the_api(db, client, seed_module, monkeypatch):
    """Every main screen's endpoint serves the showcase data for each role."""
    from facility.storage import set_storage
    from tests.test_attachments_dashboards import FakeStorage

    cloud(seed_module, monkeypatch)
    monkeypatch.setenv("FACILITY_ALLOW_DEMO_SEED", "true")
    password = "Showcase-Pass-2026"
    monkeypatch.setenv("DEMO_PASSWORD", password)
    storage = FakeStorage()
    await seed_module.seed(
        incidents=120,
        include_demo=True,
        showcase=True,
        put_object=lambda key, body, kind: storage.objects.__setitem__(key, body),
    )
    await db.rollback()
    set_storage(storage)
    try:

        async def login(email):
            result = await client.post(
                "/api/v1/auth/login", json={"email": email, "password": password}
            )
            assert result.status_code == 200, (email, result.text)
            return {"Authorization": f"Bearer {result.json()['access_token']}"}

        admin = await login("admin@acme.inc")
        common = [
            "/auth/me",
            "/incidents",
            "/notifications",
            "/notifications/unread-count",
            "/buildings",
            "/categories",
            "/locations/tree",
        ]
        by_role = {
            "admin@acme.inc": common
            + ["/dashboard/admin", "/users", "/engineers", "/teams", "/category-groups"],
            "maint.lead@acme.inc": common + ["/dashboard/team"],
            "engineer@acme.inc": common + ["/dashboard/engineer"],
            "lena.ortiz@acme.inc": common + ["/dashboard/employee"],
        }
        for email, paths in by_role.items():
            headers = admin if email == "admin@acme.inc" else await login(email)
            for path in paths:
                result = await client.get("/api/v1" + path, headers=headers)
                assert result.status_code == 200, (email, path, result.text[:300])

        listed = (await client.get("/api/v1/incidents?page_size=100", headers=admin)).json()
        assert listed["total"] >= 120
        photo = await db.scalar(select(Attachment))
        detail = await client.get(f"/api/v1/incidents/{photo.incident_id}", headers=admin)
        assert detail.status_code == 200, detail.text[:300]
        photos = await client.get(
            f"/api/v1/incidents/{photo.incident_id}/attachments", headers=admin
        )
        assert photos.status_code == 200 and photos.json()["items"]
        link = await client.get(f"/api/v1/attachments/{photo.id}", headers=admin)
        assert link.status_code == 200, link.text[:300]
        decided = await db.scalar(
            select(EscalationRequest).where(EscalationRequest.state == EscalationState.APPROVED)
        )
        detail = await client.get(f"/api/v1/incidents/{decided.incident_id}", headers=admin)
        assert detail.status_code == 200, detail.text[:300]
    finally:
        set_storage(None)


def test_migration_handler_showcase_operation(monkeypatch):
    import migration_handler

    calls = {}

    async def fake_seed(**kwargs):
        calls.update(kwargs)
        return {"inserted": 1}

    monkeypatch.setattr(
        migration_handler.command, "upgrade", lambda *args: calls.setdefault("up", 1)
    )
    monkeypatch.setattr(migration_handler, "seed", fake_seed)
    monkeypatch.setattr(migration_handler.settings, "attachments_bucket", "")
    result = migration_handler.handler({"operation": "showcase", "incidents": 999}, None)
    assert result["status"] == "seeded" and calls["up"] == 1
    assert calls["incidents"] == 200 and calls["showcase"] and calls["include_demo"]
    assert calls["put_object"] is None
    with pytest.raises(ValueError):
        migration_handler.handler({"operation": "drop"}, None)
    with pytest.raises(ValueError):
        migration_handler.handler({"operation": "showcase", "requestContext": {}}, None)
