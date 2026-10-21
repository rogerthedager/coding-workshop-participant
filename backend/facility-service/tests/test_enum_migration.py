"""A populated pre-enum database upgrades without changing values or column types."""

from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from facility.enums import (
    BlockedReason,
    EngineerAvailability,
    EscalationState,
    IncidentEventType,
    IncidentPriority,
    IncidentStatus,
    NoteVisibility,
    UserRole,
)
from facility.models import (
    EngineerProfile,
    EscalationRequest,
    Incident,
    IncidentEvent,
    IncidentNote,
    User,
)


def migrate(connection, revision):
    """Run checked-in migrations on the test's existing connection/search path."""
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    config.attributes["connection"] = connection
    command.upgrade(config, revision)


async def insert_legacy(connection, blocked_reason):
    """Insert plain historical values without the new ORM or Python enums."""
    values = {
        name: uuid4()
        for name in [
            "user",
            "engineer",
            "building",
            "floor",
            "category",
            "ticket",
            "note",
            "event",
            "escalation",
        ]
    }
    values["blocked_reason"] = blocked_reason
    statements = [
        """INSERT INTO users(id,email,full_name,password_hash,roles,is_active,must_change_password,
            token_version,failed_login_at,created_at,updated_at)
            VALUES(:user,'legacy@acme.inc','Legacy engineer','unused-test-hash','["engineer"]',
            true,false,0,'[]',now(),now())""",
        """INSERT INTO engineer_profiles(id,user_id,availability,is_active,created_at,updated_at)
            VALUES(:engineer,:user,'Busy',true,now(),now())""",
        """INSERT INTO buildings(id,name,code,is_active,created_at,updated_at)
            VALUES(:building,'Legacy building','LEGACY',true,now(),now())""",
        """INSERT INTO floors(id,building_id,name,level_number,is_active,created_at,updated_at)
            VALUES(:floor,:building,'Legacy floor',1,true,now(),now())""",
        """INSERT INTO categories(id,name,sort_order,is_active,created_at,updated_at)
            VALUES(:category,'Legacy category',0,true,now(),now())""",
        """INSERT INTO incidents(id,number,title,description,category_id,building_id,floor_id,
            status,requested_priority,priority,created_by,assigned_to,blocked_reason,reopen_count,
            created_at,updated_at) VALUES(:ticket,'INC-000001','Historical blocked ticket',
            'Keep this historical description',:category,:building,:floor,'Blocked','Medium','High',
            :user,:user,:blocked_reason,0,now(),now())""",
        """INSERT INTO incident_notes(id,incident_id,author_id,body,visibility,created_at,updated_at)
            VALUES(:note,:ticket,:user,'Keep historical private note','internal',now(),now())""",
        """INSERT INTO incident_events(id,incident_id,actor_id,type,payload,created_at,updated_at)
            VALUES(:event,:ticket,:user,'status_changed','{"from":"In Progress","to":"Blocked"}',now(),now())""",
        """INSERT INTO escalation_requests(id,incident_id,requester_id,reason,requested_at,state,
            created_at,updated_at) VALUES(:escalation,:ticket,:user,'Keep escalation reason',now(),
            'Pending',now(),now())""",
    ]
    for statement in statements:
        await connection.execute(text(statement), values)
    return values


async def fingerprints(connection):
    """Compare complete row contents without emitting historical text or credentials."""
    result = {}
    for table in [
        "users",
        "engineer_profiles",
        "incidents",
        "incident_notes",
        "incident_events",
        "escalation_requests",
    ]:
        result[table] = await connection.scalar(
            text(
                f'SELECT md5(string_agg(row_to_json(t)::text, chr(10) ORDER BY id)) FROM "{table}" t'
            )
        )
    return result


@pytest.mark.integration
async def test_populated_0001_upgrade_preserves_rows_and_storage_types(db):
    """Old strings remain byte-for-byte data-compatible and load as real enum objects."""
    connection = await db.connection()
    original_schema = await connection.scalar(text("SELECT current_schema()"))
    schema = "acme_enum_upgrade_" + uuid4().hex
    await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    try:
        await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        await connection.run_sync(migrate, "0001")
        ids = await insert_legacy(connection, "Waiting on parts")
        before = await fingerprints(connection)
        await connection.run_sync(migrate, "0002")
        assert await fingerprints(connection) == before
        assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == "0002"
        await connection.run_sync(migrate, "head")  # the current ORM model needs later columns
        columns = dict(
            (
                await connection.execute(
                    text(
                        "SELECT column_name, data_type FROM information_schema.columns "
                        "WHERE table_schema=:schema AND table_name='incidents' "
                        "AND column_name IN ('status','priority','blocked_reason')"
                    ),
                    {"schema": schema},
                )
            ).all()
        )
        assert columns == {
            "status": "character varying",
            "priority": "character varying",
            "blocked_reason": "text",
        }
        assert (
            await connection.scalar(
                text(
                    "SELECT count(*) FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace "
                    "WHERE n.nspname=:schema AND t.typtype='e'"
                ),
                {"schema": schema},
            )
            == 0
        )
        async with AsyncSession(
            bind=connection, join_transaction_mode="create_savepoint"
        ) as historical:
            ticket = await historical.get(Incident, ids["ticket"])
            assert ticket.status is IncidentStatus.BLOCKED
            assert ticket.priority is IncidentPriority.HIGH
            assert ticket.requested_priority is IncidentPriority.MEDIUM
            assert ticket.blocked_reason is BlockedReason.WAITING_ON_PARTS
            assert (await historical.get(User, ids["user"])).roles == [UserRole.ENGINEER]
            assert (
                await historical.get(EngineerProfile, ids["engineer"])
            ).availability is EngineerAvailability.BUSY
            assert (
                await historical.get(IncidentNote, ids["note"])
            ).visibility is NoteVisibility.INTERNAL
            assert (
                await historical.get(IncidentEvent, ids["event"])
            ).type is IncidentEventType.STATUS_CHANGED
            assert (
                await historical.get(EscalationRequest, ids["escalation"])
            ).state is EscalationState.PENDING
    finally:
        await connection.execute(text(f'SET LOCAL search_path TO "{original_schema}"'))
        await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await db.commit()


@pytest.mark.integration
async def test_invalid_legacy_values_abort_upgrade_without_rewriting_data(db):
    """An operator receives a failure, rather than a silently rewritten old reason."""
    connection = await db.connection()
    original_schema = await connection.scalar(text("SELECT current_schema()"))
    schema = "acme_enum_invalid_" + uuid4().hex
    await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    try:
        await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        await connection.run_sync(migrate, "0001")
        await insert_legacy(connection, "Unrecognized historical reason")
        before = await fingerprints(connection)
        with pytest.raises(IntegrityError):
            async with connection.begin_nested():
                await connection.run_sync(migrate, "head")
        assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == "0001"
        assert await fingerprints(connection) == before
    finally:
        await connection.execute(text(f'SET LOCAL search_path TO "{original_schema}"'))
        await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await db.commit()
