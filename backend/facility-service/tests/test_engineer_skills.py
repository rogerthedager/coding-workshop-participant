"""Engineers have at most two unique skills drawn from the top-level categories."""

from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from facility.enums import MAX_ENGINEER_SKILLS, EngineerSkill
from facility.models import EngineerProfile
from facility.seed import CATEGORIES


def test_skills_match_top_level_categories_except_other():
    """Each skill names a seeded top-level category, so assignment can match them later."""
    assert {skill.value for skill in EngineerSkill} == set(CATEGORIES) - {"Other"}
    assert MAX_ENGINEER_SKILLS == 2


@pytest.mark.parametrize(
    "skills",
    [
        ["Plumbing", "Cleaning"],
        ["Cleaning", "Cleaning"],
        ["Cleaning", "Furniture", "Network and Wi-Fi"],
        "Cleaning",
    ],
)
def test_model_rejects_invalid_skill_lists(skills):
    """Unknown, repeated, too many or non-list skills never reach PostgreSQL."""
    with pytest.raises(ValueError):
        EngineerProfile(user_id=uuid4(), skills=skills)


@pytest.mark.integration
async def test_admin_sets_and_replaces_engineer_skills(client, db, admin, employee, auth_headers):
    """Exactly two skills on create and update (FR-4.5), replaced as a whole."""
    headers = await auth_headers(admin)
    created = await client.post(
        "/api/v1/engineers",
        headers=headers,
        json={
            "email": employee.email,
            "full_name": "Skilled Engineer",
            "skills": ["Cleaning", "HVAC and temperature"],
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["skills"] == ["Cleaning", "HVAC and temperature"]
    url = f"/api/v1/engineers/{created.json()['id']}"
    for invalid in [
        ["Cleaning", "Furniture", "Network and Wi-Fi"],
        ["Cleaning", "Cleaning"],
        ["Gardening"],
        ["Furniture"],
        [],
        None,
    ]:
        rejected = await client.patch(url, headers=headers, json={"skills": invalid})
        assert rejected.status_code == 422, invalid
    updated = await client.patch(
        url, headers=headers, json={"skills": ["Furniture", "Network and Wi-Fi"]}
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["skills"] == ["Furniture", "Network and Wi-Fi"]
    listed = await client.get("/api/v1/engineers", headers=headers)
    assert all("skills" in item for item in listed.json()["items"])


@pytest.mark.integration
async def test_engineer_cannot_set_own_skills(client, db, engineer, auth_headers):
    """Only administrators maintain skills."""
    headers = await auth_headers(engineer)
    profile = await db.scalar(select(EngineerProfile).where(EngineerProfile.user_id == engineer.id))
    response = await client.patch(
        f"/api/v1/engineers/{profile.id}", headers=headers, json={"skills": ["Cleaning"]}
    )
    assert response.status_code == 403


@pytest.mark.integration
async def test_database_rejects_invalid_skills(db, engineer):
    """Direct SQL cannot bypass the skill domain or the two-skill limit."""
    profile = await db.scalar(select(EngineerProfile).where(EngineerProfile.user_id == engineer.id))
    for invalid in [
        '["Cleaning","Furniture","Network and Wi-Fi"]',
        '["Gardening"]',
        '"Cleaning"',
    ]:
        with pytest.raises(IntegrityError) as rejected:
            async with db.begin_nested():
                await db.execute(
                    text("UPDATE engineer_profiles SET skills=CAST(:value AS jsonb) WHERE id=:id"),
                    {"value": invalid, "id": profile.id},
                )
        assert rejected.value.orig.sqlstate == "23514"  # CHECK violation.


def migrate(connection, revision):
    """Run checked-in migrations on the test's existing connection/search path."""
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    config.attributes["connection"] = connection
    command.upgrade(config, revision)


@pytest.mark.integration
async def test_upgrade_gives_existing_engineers_no_skills(db):
    """Existing profiles upgrade to an empty skill list without other changes."""
    connection = await db.connection()
    original_schema = await connection.scalar(text("SELECT current_schema()"))
    schema = "acme_skills_upgrade_" + uuid4().hex
    await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    try:
        await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        await connection.run_sync(migrate, "0002")
        user, engineer = uuid4(), uuid4()
        await connection.execute(
            text(
                """INSERT INTO users(id,email,full_name,password_hash,roles,is_active,
                must_change_password,token_version,failed_login_at,created_at,updated_at)
                VALUES(:user,'legacy@acme.inc','Legacy engineer','unused-test-hash',
                '["engineer"]',true,false,0,'[]',now(),now())"""
            ),
            {"user": user},
        )
        await connection.execute(
            text(
                """INSERT INTO engineer_profiles(id,user_id,availability,is_active,created_at,
                updated_at) VALUES(:engineer,:user,'Busy',true,now(),now())"""
            ),
            {"engineer": engineer, "user": user},
        )
        await connection.run_sync(migrate, "0003")
        assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == "0003"
        row = (
            await connection.execute(
                text("SELECT availability, skills FROM engineer_profiles WHERE id=:id"),
                {"id": engineer},
            )
        ).one()
        assert tuple(row) == ("Busy", [])
    finally:
        await connection.execute(text(f'SET LOCAL search_path TO "{original_schema}"'))
        await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await db.commit()
