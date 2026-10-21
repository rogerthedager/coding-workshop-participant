"""Persistent session security and people invariants using real PostgreSQL."""

import asyncio
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException, Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from facility import database
from facility.config import Settings, settings
from facility.models import EngineerProfile, RefreshSession, User, utcnow
from facility.routers.auth import Login, login, refresh
from facility.routers.people import UserUpdate, update_user
from facility.security import (
    hash_password,
    normalize_email,
    validate_password,
    verify_password,
)
from facility.sessions import cleanup_expired_sessions

pytestmark = pytest.mark.asyncio
PASSWORD = "Valid-Passphrase-734!"


async def test_workshop_database_settings_lazy_secret_and_ssl(monkeypatch):
    """Workshop injection encodes credentials safely and production requires SSL."""
    config = Settings(
        _env_file=None,
        database_url="",
        jwt_secret="",
        secret_arn="",
        app_name="",
        postgres_host="private-db.example",
        postgres_user="user@acme",
        postgres_pass="secret:/with@symbols",
        postgres_name="facility",
        postgres_port=5432,
    )
    assert config.resolved_database_url() == (
        "postgresql+asyncpg://user%40acme:secret%3A%2Fwith%40symbols@private-db.example:5432/facility"
    )
    assert "secret:/with@symbols" not in repr(config)
    import boto3

    calls = []

    class Secrets:
        def get_secret_value(self, **kwargs):
            calls.append(kwargs)
            return {"SecretString": '{"jwt_secret":"' + "x" * 64 + '"}'}

    monkeypatch.setattr(boto3, "client", lambda service: Secrets())
    config = Settings(
        _env_file=None,
        database_url="",
        jwt_secret="",
        secret_arn="",
        app_name="workshop",
    )
    assert calls == []
    assert config.resolved_jwt_secret() == "x" * 64
    assert config.resolved_jwt_secret() == "x" * 64
    assert calls == [{"SecretId": "workshop/facility"}]
    monkeypatch.setattr(database, "engine", None)
    monkeypatch.setattr(
        database.settings,
        "database_url",
        "postgresql+asyncpg://test:password@private/database",
    )
    monkeypatch.setattr(database.settings, "is_local", False)
    observed = {}

    def capture(url, **kwargs):
        observed.update(kwargs)
        return object()

    monkeypatch.setattr(database, "create_async_engine", capture)
    assert database.get_engine() is not None
    assert observed["connect_args"] == {"ssl": "require"}


async def test_expired_session_retention_respects_seven_day_grace(db, employee):
    """Only authentication rows older than expiry plus the audit grace are removed."""
    for expiry in (
        utcnow() - timedelta(days=8),
        utcnow() - timedelta(days=6),
        utcnow() + timedelta(days=1),
    ):
        db.add(
            RefreshSession(
                user_id=employee.id,
                token_hash=uuid4().hex * 2,
                family_id=uuid4(),
                expires_at=expiry,
            )
        )
    await db.commit()
    assert await cleanup_expired_sessions(db) == 1
    assert await db.scalar(select(func.count(RefreshSession.id))) == 2
    assert await db.scalar(select(func.count(User.id))) == 1


async def test_password_policy_email_and_postgres_only():
    """Reject byte truncation/domain lookalikes/non-PG configuration."""
    assert normalize_email("  EMPLOYEE@ACME.INC ") == "employee@acme.inc"
    for value in ("user@acme.inc.evil", "user@other.inc", "bad name@acme.inc"):
        with pytest.raises(ValueError):
            normalize_email(value)
    for value in ("short1", "longwithoutdigits", "123456789012", "字" * 25 + "A1"):
        with pytest.raises(ValueError):
            validate_password(value)
    hashed = hash_password(PASSWORD)
    assert hashed.startswith("$2b$12$")
    assert verify_password(PASSWORD, hashed)
    assert not verify_password(PASSWORD + "x", hashed)
    assert not verify_password("字" * 100, hashed)
    assert not verify_password(PASSWORD, "malformed")
    with pytest.raises(ValueError):
        Settings(database_url="sqlite:///forbidden.db")
    with pytest.raises(ValueError):
        Settings(is_local=False, allowed_origins=["http://insecure.example"])
    unconfigured = Settings(
        _env_file=None,
        database_url="",
        jwt_secret="",
        secret_arn="",
        app_name="",
        postgres_host="",
        postgres_user="",
        postgres_pass="",
        postgres_name="",
        is_local=False,
    )
    assert unconfigured.resolved_origins() == []
    with pytest.raises(RuntimeError, match="not configured"):
        unconfigured.resolved_database_url()


async def test_registration_domain_duplicate_and_injected_roles(client):
    """Self registration normalizes email and cannot grant staff roles."""
    payload = {"email": "NEW@ACME.INC", "full_name": " New User ", "password": PASSWORD}
    created = await client.post("/api/v1/auth/register", json=payload)
    assert created.status_code == 201, created.text
    assert created.json()["email"] == "new@acme.inc"
    assert created.json()["roles"] == []
    assert "password_hash" not in created.json()
    assert (await client.post("/api/v1/auth/register", json=payload)).status_code == 409
    payload["email"] = "new@evil.inc"
    assert (await client.post("/api/v1/auth/register", json=payload)).status_code == 422
    payload["email"] = "injected@acme.inc"
    payload["roles"] = ["admin"]
    assert (await client.post("/api/v1/auth/register", json=payload)).status_code == 422
    assert (await client.get("/api/v1/auth/me")).status_code == 401


async def test_persistent_lockout_fifth_failure_expiry_and_reset(client, db, employee):
    """The fifth failure commits a lock that blocks a correct sixth password."""
    for _ in range(5):
        response = await client.post(
            "/api/v1/auth/login",
            json={
                "email": employee.email,
                "password": "incorrect",
            },
        )
        assert response.status_code == 401
    await db.refresh(employee)
    assert len(employee.failed_login_at) == 5
    assert employee.locked_until > utcnow()
    correct = {"email": employee.email, "password": PASSWORD}
    assert (await client.post("/api/v1/auth/login", json=correct)).status_code == 401
    employee.locked_until = utcnow() - timedelta(seconds=1)
    employee.failed_login_at = [(utcnow() - timedelta(minutes=16)).isoformat()]
    await db.commit()
    response = await client.post("/api/v1/auth/login", json=correct)
    assert response.status_code == 200
    await db.refresh(employee)
    assert employee.locked_until is None and employee.failed_login_at == []
    assert "httponly" in response.headers["set-cookie"].lower()
    assert "samesite=strict" in response.headers["set-cookie"].lower()


async def test_concurrent_failures_are_atomic_across_database_sessions(db, employee):
    """Independent backend requests cannot overwrite each other's failed attempts."""
    factory = async_sessionmaker(db.bind, expire_on_commit=False)

    async def attempt(password):
        async with factory() as session:
            request = Request(
                {
                    "type": "http",
                    "method": "POST",
                    "path": "/api/v1/auth/login",
                    "scheme": "http",
                    "server": ("testserver", 80),
                    "query_string": b"",
                    "headers": [],
                }
            )
            try:
                await login(
                    Login(email=employee.email, password=password), Response(), session, request
                )
                return 200
            except HTTPException as exc:
                await session.rollback()
                return exc.status_code

    assert await asyncio.gather(*(attempt("incorrect") for _ in range(5))) == [401] * 5
    await db.refresh(employee)
    assert len(employee.failed_login_at) == 5
    assert employee.locked_until > utcnow()
    assert await attempt(PASSWORD) == 401


async def test_concurrent_refresh_rotation_revokes_the_replayed_family(
    client,
    db,
    employee,
    auth_headers,
):
    """Two backend sessions competing for one token cannot create two live successors."""
    await auth_headers(employee)
    token = client.cookies.get("facility_refresh")
    factory = async_sessionmaker(db.bind, expire_on_commit=False)

    async def rotate():
        async with factory() as session:
            request = Request(
                {
                    "type": "http",
                    "method": "POST",
                    "path": "/api/v1/auth/refresh",
                    "scheme": "http",
                    "server": ("testserver", 80),
                    "query_string": b"",
                    "headers": [(b"cookie", f"facility_refresh={token}".encode())],
                }
            )
            try:
                await refresh(request, Response(), session)
                return 200
            except HTTPException as exc:
                await session.rollback()
                return exc.status_code

    assert sorted(await asyncio.gather(rotate(), rotate())) == [200, 401]
    sessions = (await db.scalars(select(RefreshSession))).all()
    assert len(sessions) == 2
    assert all(item.revoked_at is not None for item in sessions)


async def test_refresh_rotation_replay_and_logout(client, db, employee):
    """A replay revokes the successor; logout revokes all tokens in its family."""
    assert (
        await client.post(
            "/api/v1/auth/login",
            json={
                "email": employee.email,
                "password": PASSWORD,
            },
        )
    ).status_code == 200
    old = client.cookies.get("facility_refresh")
    refreshed = await client.post("/api/v1/auth/refresh")
    assert refreshed.status_code == 200, refreshed.text
    successor = client.cookies.get("facility_refresh")
    assert old != successor
    replay = await client.post(
        "/api/v1/auth/refresh", headers={"Cookie": f"facility_refresh={old}"}
    )
    assert replay.status_code == 401
    denied = await client.post(
        "/api/v1/auth/refresh", headers={"Cookie": f"facility_refresh={successor}"}
    )
    assert denied.status_code == 401
    sessions = (await db.scalars(select(RefreshSession))).all()
    assert all(item.revoked_at for item in sessions)
    assert not any(item.token_hash in {old, successor} for item in sessions)
    assert (
        await client.post(
            "/api/v1/auth/login",
            json={
                "email": employee.email,
                "password": PASSWORD,
            },
        )
    ).status_code == 200
    latest = client.cookies.get("facility_refresh")
    assert (await client.post("/api/v1/auth/logout")).status_code == 204
    assert (
        await client.post("/api/v1/auth/refresh", headers={"Cookie": f"facility_refresh={latest}"})
    ).status_code == 401


async def test_refresh_rejects_cross_origin(client, employee, auth_headers):
    """Browser cookie mutations do not trust sibling/cross-origin requests."""
    await auth_headers(employee)
    denied = await client.post("/api/v1/auth/refresh", headers={"Origin": "https://evil.example"})
    assert denied.status_code == 403
    denied = await client.post("/api/v1/auth/logout", headers={"Sec-Fetch-Site": "same-site"})
    assert denied.status_code == 403
    assert (
        await client.post("/api/v1/auth/refresh", headers={"Origin": "http://testserver"})
    ).status_code == 200


async def test_configured_cloud_origin_and_login_csrf(client, employee, auth_headers, monkeypatch):
    """A trusted public proxy origin works without trusting arbitrary forwarded hosts."""
    await auth_headers(employee)
    monkeypatch.setattr(settings, "is_local", False)
    monkeypatch.setattr(settings, "allowed_origins", ["https://facilities.acme.example"])
    assert settings.resolved_origins() == ["https://facilities.acme.example"]
    allowed = await client.post(
        "/api/v1/auth/refresh",
        headers={
            "Origin": "https://facilities.acme.example",
        },
    )
    assert allowed.status_code == 200, allowed.text
    raw = client.cookies.get("facility_refresh")
    denied = await client.post(
        "/api/v1/auth/refresh",
        headers={
            "Origin": "https://untrusted.acme.example",
            "Cookie": f"facility_refresh={raw}",
            "X-Forwarded-Host": "untrusted.acme.example",
        },
    )
    assert denied.status_code == 403
    rejected = await client.post(
        "/api/v1/auth/login",
        headers={
            "Origin": "https://untrusted.acme.example",
        },
        json={"email": employee.email, "password": PASSWORD},
    )
    assert rejected.status_code == 403


async def test_required_secret_never_falls_back(client, employee, monkeypatch):
    """An unconfigured deployment refuses to issue signed credentials."""
    monkeypatch.setattr(settings, "jwt_secret", "")
    response = await client.post(
        "/api/v1/auth/login",
        json={
            "email": employee.email,
            "password": PASSWORD,
        },
    )
    assert response.status_code == 503


async def test_temporary_password_restriction_and_session_revocation(
    client,
    db,
    admin,
    auth_headers,
):
    """New engineer credentials permit only inspect/change/logout until replaced."""
    headers = await auth_headers(admin)
    created = await client.post(
        "/api/v1/engineers",
        headers=headers,
        json={
            "email": "temporary@acme.inc",
            "full_name": "New Engineer",
            "phone": "555-0109",
        },
    )
    assert created.status_code == 201, created.text
    temporary = created.json()["temporary_password"]
    login = await client.post(
        "/api/v1/auth/login",
        json={
            "email": "temporary@acme.inc",
            "password": temporary,
        },
    )
    assert login.status_code == 200
    old_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    old_refresh = client.cookies.get("facility_refresh")
    assert (await client.get("/api/v1/auth/me", headers=old_headers)).status_code == 200
    assert (await client.get("/api/v1/engineers", headers=old_headers)).status_code == 403
    assert (await client.post("/api/v1/auth/refresh")).status_code == 403
    changed = await client.post(
        "/api/v1/auth/change-password",
        headers=old_headers,
        json={
            "current_password": temporary,
            "new_password": PASSWORD,
        },
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["user"]["must_change_password"] is False
    assert (await client.get("/api/v1/auth/me", headers=old_headers)).status_code == 401
    new_headers = {"Authorization": f"Bearer {changed.json()['access_token']}"}
    assert (await client.get("/api/v1/engineers", headers=new_headers)).status_code == 200
    assert (
        await client.post(
            "/api/v1/auth/refresh",
            headers={"Cookie": f"facility_refresh={old_refresh}"},
        )
    ).status_code == 401
    user = await db.scalar(select(User).where(User.email == "temporary@acme.inc"))
    assert user.token_version == 1


async def test_people_rbac_self_profile_and_live_roles(client, db, employee, admin, auth_headers):
    """Current DB roles take effect immediately; self service cannot alter them."""
    employee_headers = await auth_headers(employee)
    assert (await client.get("/api/v1/users", headers=employee_headers)).status_code == 403
    assert (await client.get("/api/v1/engineers", headers=employee_headers)).status_code == 403
    denied = await client.patch(
        "/api/v1/users/me",
        headers=employee_headers,
        json={
            "full_name": "Changed",
            "roles": ["admin"],
        },
    )
    assert denied.status_code == 422
    renamed = await client.patch(
        "/api/v1/users/me", headers=employee_headers, json={"full_name": "Changed"}
    )
    assert renamed.status_code == 200 and renamed.json()["full_name"] == "Changed"
    admin_headers = await auth_headers(admin)
    promoted = await client.patch(
        f"/api/v1/users/{employee.id}", headers=admin_headers, json={"roles": ["admin"]}
    )
    assert promoted.status_code == 200
    assert (await client.get("/api/v1/users", headers=employee_headers)).status_code == 200
    demoted = await client.patch(
        f"/api/v1/users/{employee.id}", headers=admin_headers, json={"roles": []}
    )
    assert demoted.status_code == 200
    assert (await client.get("/api/v1/users", headers=employee_headers)).status_code == 403
    deactivated = await client.patch(
        f"/api/v1/users/{employee.id}", headers=admin_headers, json={"is_active": False}
    )
    assert deactivated.status_code == 200
    assert (await client.get("/api/v1/auth/me", headers=employee_headers)).status_code == 401
    await db.refresh(employee)
    assert employee.token_version == 1


async def test_last_admin_guard_including_concurrent_removals(
    client, db, admin, make_user, auth_headers
):
    """Two concurrent role removals cannot leave the organization without an admin."""
    headers = await auth_headers(admin)
    for patch in ({"roles": []}, {"is_active": False}):
        response = await client.patch(f"/api/v1/users/{admin.id}", headers=headers, json=patch)
        assert response.status_code == 409
    second = await make_user(roles=["admin"])
    factory = async_sessionmaker(db.bind, expire_on_commit=False)

    async def remove(user_id):
        async with factory() as session:
            try:
                await update_user(user_id, UserUpdate(roles=[]), session, admin)
                return 200
            except HTTPException as exc:
                await session.rollback()
                return exc.status_code

    outcomes = await asyncio.gather(remove(admin.id), remove(second.id))
    assert sorted(outcomes) == [200, 409]
    count = await db.scalar(
        select(func.count(User.id)).where(
            User.is_active.is_(True),
            User.roles.contains(["admin"]),
        )
    )
    assert count == 1


async def test_engineer_existing_account_availability_and_logical_delete(
    client,
    db,
    admin,
    employee,
    engineer,
    auth_headers,
):
    """Engineer deactivation retains the user; reactivation restores profile access."""
    admin_headers = await auth_headers(admin)
    added = await client.post(
        "/api/v1/engineers",
        headers=admin_headers,
        json={
            "email": employee.email,
            "full_name": "Employee Engineer",
        },
    )
    assert added.status_code == 201, added.text
    assert "temporary_password" not in added.json()
    assert added.json()["user"]["must_change_password"] is False
    assert (
        await client.post(
            "/api/v1/engineers",
            headers=admin_headers,
            json={
                "email": employee.email,
                "full_name": "Employee Engineer",
            },
        )
    ).status_code == 409
    engineer_headers = await auth_headers(engineer)
    availability = await client.patch(
        "/api/v1/engineers/me/availability",
        headers=engineer_headers,
        json={"availability": "Off-duty"},
    )
    assert availability.status_code == 200
    assert availability.json()["availability"] == "Off-duty"
    profile = await db.scalar(select(EngineerProfile).where(EngineerProfile.user_id == engineer.id))
    profile_url = f"/api/v1/engineers/{profile.id}"
    assert (
        await client.patch(profile_url, headers=engineer_headers, json={"is_active": False})
    ).status_code == 403
    deactivated = await client.patch(
        profile_url, headers=admin_headers, json={"is_active": False, "phone": None}
    )
    assert deactivated.status_code == 200, deactivated.text
    assert deactivated.json()["user"]["is_active"] is True
    assert (
        await client.patch(
            "/api/v1/engineers/me/availability",
            headers=engineer_headers,
            json={"availability": "Available"},
        )
    ).status_code == 403
    assert (
        await client.patch(profile_url, headers=admin_headers, json={"is_active": True})
    ).status_code == 200
    directory = await client.get(
        "/api/v1/engineers", headers=admin_headers, params={"q": "Employee Engineer"}
    )
    assert directory.status_code == 200 and directory.json()["total"] == 1


async def test_nonfinal_work_prevents_engineer_deactivation_role_removal_or_user_disable(
    client,
    db,
    admin,
    engineer,
    make_incident,
    auth_headers,
):
    """Resolved work is still non-final and must be reassigned before disabling staff."""
    incident = await make_incident(status="Resolved")
    headers = await auth_headers(admin)
    profile = await db.scalar(select(EngineerProfile).where(EngineerProfile.user_id == engineer.id))
    response = await client.patch(
        f"/api/v1/engineers/{profile.id}", headers=headers, json={"is_active": False}
    )
    assert response.status_code == 409
    for patch in ({"roles": []}, {"is_active": False}):
        response = await client.patch(f"/api/v1/users/{engineer.id}", headers=headers, json=patch)
        assert response.status_code == 409
    summary = await client.get(f"/api/v1/engineers/{profile.id}", headers=headers)
    assert summary.json()["status_counts"]["Resolved"] == 1
    assert summary.json()["workload"] == 1
    incident.status = "Closed"
    await db.commit()
    assert (
        await client.patch(f"/api/v1/users/{engineer.id}", headers=headers, json={"roles": []})
    ).status_code == 200
    await db.refresh(profile)
    assert profile.is_active is False


async def test_expired_refresh_and_inactive_account_are_rejected(
    client, db, employee, auth_headers
):
    """Session validity is persisted and rechecked rather than trusted from cookies."""
    await auth_headers(employee)
    session = await db.scalar(select(RefreshSession).where(RefreshSession.user_id == employee.id))
    session.expires_at = utcnow() - timedelta(seconds=1)
    await db.commit()
    assert (await client.post("/api/v1/auth/refresh")).status_code == 401
    await auth_headers(employee)
    employee.is_active = False
    await db.commit()
    assert (await client.post("/api/v1/auth/refresh")).status_code == 401


async def test_wrong_current_password_and_malformed_bearer(client, employee, auth_headers):
    """Password changes require reauthentication; token parsing fails closed."""
    headers = await auth_headers(employee)
    assert (
        await client.post(
            "/api/v1/auth/change-password",
            headers=headers,
            json={
                "current_password": "wrong",
                "new_password": "Different-Passphrase-909!",
            },
        )
    ).status_code == 401
    assert (
        await client.get("/api/v1/auth/me", headers={"Authorization": "Bearer invalid"})
    ).status_code == 401
    async with httpx.AsyncClient(
        transport=client._transport, base_url="http://testserver"
    ) as separate:
        assert (await separate.get("/api/v1/auth/me")).status_code == 401
