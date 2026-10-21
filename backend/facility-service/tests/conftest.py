"""PostgreSQL-only fixtures isolated in a new schema for every integration test."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable
from typing import Any
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

# This known secret is confined to the test process and is never a service default.
os.environ.setdefault("FACILITY_JWT_SECRET", "test-only-secret-" + "z" * 64)
os.environ.setdefault("IS_LOCAL", "true")

from facility.database import Base, get_db
from facility.main import app
from facility.models import (
    Building,
    Category,
    EngineerProfile,
    Floor,
    Incident,
    Seat,
    User,
)
from facility.security import hash_password

PASSWORD = "Valid-Passphrase-734!"


@pytest_asyncio.fixture
async def db() -> AsyncIterator[AsyncSession]:
    """Create and remove only a random test schema in the explicitly selected PG DB."""
    url = os.environ.get("TEST_DATABASE_URL")
    if not url or not url.startswith("postgresql+asyncpg://"):
        pytest.fail(
            "TEST_DATABASE_URL must explicitly select PostgreSQL using postgresql+asyncpg://"
        )
    schema = f"acme_test_{uuid4().hex}"
    administration = create_async_engine(url, poolclass=NullPool)
    try:
        async with administration.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    except (SQLAlchemyError, OSError, TimeoutError):
        await administration.dispose()
        pytest.fail(
            "Cannot connect to the selected PostgreSQL database or create a test schema; "
            "verify init and database permissions. Credentials have been omitted.",
            pytrace=False,
        )
    engine = create_async_engine(
        url,
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": schema}},
    )
    try:
        async with engine.begin() as connection:
            await connection.execute(text("CREATE SEQUENCE incident_number_seq START WITH 1"))
            await connection.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            yield session
            await session.rollback()
    finally:
        await engine.dispose()
        async with administration.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await administration.dispose()


@pytest_asyncio.fixture
async def client(db: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    """Route every request into the current test's isolated PostgreSQL schema."""
    request_sessions = async_sessionmaker(db.bind, expire_on_commit=False)

    async def override_db() -> AsyncIterator[AsyncSession]:
        async with request_sessions() as request_db:
            try:
                yield request_db
            finally:
                await request_db.rollback()

    app.dependency_overrides[get_db] = override_db
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as value:
            yield value
    finally:
        app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def make_user(db: AsyncSession) -> Callable[..., Any]:
    """Return a factory for active users and optional engineer profiles."""

    async def create(**overrides: Any) -> User:
        password = overrides.pop("password", PASSWORD)
        values = {
            "email": f"user-{uuid4().hex}@acme.inc",
            "full_name": "Integration User",
            "password_hash": hash_password(password),
            "roles": [],
            "is_active": True,
            "must_change_password": False,
            "token_version": 0,
            "failed_login_at": [],
        }
        values.update(overrides)
        user = User(**values)
        db.add(user)
        await db.flush()
        if "engineer" in user.roles:
            db.add(
                EngineerProfile(
                    user_id=user.id,
                    phone="555-0101",
                    is_active=True,
                    availability="Available",
                )
            )
        await db.commit()
        await db.refresh(user)
        return user

    return create


@pytest_asyncio.fixture
async def auth_headers(client: httpx.AsyncClient) -> Callable[..., Any]:
    """Authenticate through the real login endpoint for role and token coverage."""

    async def headers(user: User, password: str = PASSWORD) -> dict[str, str]:
        result = await client.post(
            "/api/v1/auth/login",
            json={
                "email": user.email,
                "password": password,
            },
        )
        assert result.status_code == 200, result.text
        return {"Authorization": f"Bearer {result.json()['access_token']}"}

    return headers


@pytest_asyncio.fixture
async def employee(make_user: Callable[..., Any]) -> User:
    """Create the incident creator role."""
    return await make_user(full_name="Employee One")


@pytest_asyncio.fixture
async def other_employee(make_user: Callable[..., Any]) -> User:
    """Create an employee without incident ownership."""
    return await make_user(full_name="Employee Two")


@pytest_asyncio.fixture
async def admin(make_user: Callable[..., Any]) -> User:
    """Create a facility administrator."""
    return await make_user(full_name="Facility Admin", roles=["admin"])


@pytest_asyncio.fixture
async def engineer(make_user: Callable[..., Any]) -> User:
    """Create an engineer available for assignment."""
    return await make_user(full_name="Engineer One", roles=["engineer"])


@pytest_asyncio.fixture
async def other_engineer(make_user: Callable[..., Any]) -> User:
    """Create an engineer who is not assigned to the incident."""
    return await make_user(full_name="Engineer Two", roles=["engineer"])


@pytest_asyncio.fixture
async def reference_data(db: AsyncSession) -> dict[str, Any]:
    """Create a valid building/floor/seat/category hierarchy."""
    building = Building(name="North Campus", code="NORTH", is_active=True)
    category = Category(name="Workplace Technology", is_active=True, sort_order=0)
    db.add_all([building, category])
    await db.flush()
    floor = Floor(building_id=building.id, name="Floor One", level_number=1, is_active=True)
    db.add(floor)
    await db.flush()
    seat = Seat(floor_id=floor.id, code="N-101", is_active=True)
    db.add(seat)
    await db.commit()
    return {"building": building, "floor": floor, "seat": seat, "category": category}


@pytest_asyncio.fixture
async def make_incident(
    db: AsyncSession,
    employee: User,
    engineer: User,
    reference_data: dict[str, Any],
) -> Callable[..., Any]:
    """Create an assigned Open incident for tests of individual operations."""

    async def create(**overrides: Any) -> Incident:
        number = await db.scalar(text("SELECT nextval('incident_number_seq')"))
        values = {
            "number": f"INC-{number:06d}",
            "title": "Projector has no signal",
            "description": "The meeting-room projector does not receive HDMI input.",
            "category_id": reference_data["category"].id,
            "building_id": reference_data["building"].id,
            "floor_id": reference_data["floor"].id,
            "seat_id": reference_data["seat"].id,
            "created_by": employee.id,
            "assigned_to": engineer.id,
            "status": "Open",
            "priority": "Medium",
            "requested_priority": "Medium",
        }
        values.update(overrides)
        incident = Incident(**values)
        db.add(incident)
        await db.commit()
        await db.refresh(incident)
        return incident

    return create
