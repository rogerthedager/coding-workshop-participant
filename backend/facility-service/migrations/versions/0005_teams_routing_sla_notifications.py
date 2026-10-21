"""Teams, category groups and routing, followers, duplicates, SLA timers,
notifications, attachments and default seats.

The values below are frozen migration history, independent of future Python enums.
Every change is additive. Existing incidents get SLA due times computed from their
creation time and current priority; no other business values are rewritten.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None



def _entity_columns():
    """Fresh ID and timestamp columns for each new entity table."""
    return [
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade():
    """Create new tables and columns, then backfill SLA due times."""
    # Roles and event types gain new values.
    op.drop_constraint("ck_users_roles_values", "users", type_="check")
    op.create_check_constraint(
        "ck_users_roles_values", "users", 'roles <@ \'["engineer","lead","admin"]\'::jsonb'
    )
    op.drop_constraint("ck_incident_events_type", "incident_events", type_="check")
    op.create_check_constraint(
        "ck_incident_events_type",
        "incident_events",
        "type IN ('created','details_changed','status_changed','assigned','unassigned',"
        "'priority_changed','escalation_requested','escalation_approved',"
        "'escalation_rejected','escalation_superseded','note_added','note_edited',"
        "'note_hidden','duplicate_marked','auto_closed','attachment_added')",
    )

    op.create_table(
        "teams",
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("lead_user_id", sa.UUID(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *_entity_columns(),
        sa.UniqueConstraint("lead_user_id", name="uq_teams_lead"),
    )
    op.create_index("uq_teams_name_lower", "teams", [sa.text("lower(name)")], unique=True)

    op.add_column(
        "engineer_profiles",
        sa.Column("team_id", sa.UUID(), sa.ForeignKey("teams.id"), nullable=True),
    )
    op.create_index("ix_engineer_profiles_team_id", "engineer_profiles", ["team_id"])
    op.add_column(
        "engineer_profiles",
        sa.Column(
            "home_building_ids",
            postgresql.ARRAY(sa.UUID()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
    )

    op.create_table(
        "category_groups",
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("team_id", sa.UUID(), sa.ForeignKey("teams.id"), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *_entity_columns(),
    )
    op.create_index(
        "uq_category_groups_name_lower", "category_groups", [sa.text("lower(name)")], unique=True
    )
    op.create_index("ix_category_groups_team_id", "category_groups", ["team_id"])
    op.add_column(
        "categories",
        sa.Column("group_id", sa.UUID(), sa.ForeignKey("category_groups.id"), nullable=True),
    )
    op.create_index("ix_categories_group_id", "categories", ["group_id"])
    op.create_check_constraint(
        "ck_categories_group_top_level", "categories", "parent_id IS NULL OR group_id IS NULL"
    )

    op.add_column(
        "users", sa.Column("default_seat_id", sa.UUID(), sa.ForeignKey("seats.id"), nullable=True)
    )

    columns = [
        sa.Column("close_kind", sa.Text(), nullable=True),
        sa.Column("duplicate_of_id", sa.UUID(), sa.ForeignKey("incidents.id"), nullable=True),
        sa.Column("routed_team_id", sa.UUID(), sa.ForeignKey("teams.id"), nullable=True),
        sa.Column("triage_reason", sa.Text(), nullable=True),
        sa.Column("route_return_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("ack_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolve_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ack_at_risk_alerted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ack_breach_alerted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolve_at_risk_alerted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolve_breach_alerted_at", sa.DateTime(timezone=True), nullable=True),
    ]
    for column in columns:
        op.add_column("incidents", column)
    op.create_index("ix_incidents_routed_team_id", "incidents", ["routed_team_id"])
    op.create_index(
        "ix_incidents_triage",
        "incidents",
        ["triage_reason"],
        postgresql_where=sa.text("triage_reason IS NOT NULL"),
    )
    op.create_index(
        "ix_incidents_resolved_at",
        "incidents",
        ["resolved_at"],
        postgresql_where=sa.text("status = 'Resolved'"),
    )
    op.execute(
        """
        UPDATE incidents SET
          ack_due_at = created_at + make_interval(hours => CASE priority
            WHEN 'Critical' THEN 1 WHEN 'High' THEN 4 WHEN 'Medium' THEN 24 ELSE 48 END),
          resolve_due_at = created_at + make_interval(hours => CASE priority
            WHEN 'Critical' THEN 4 WHEN 'High' THEN 24 WHEN 'Medium' THEN 72 ELSE 120 END)
        """
    )
    # Historical closures predate close kinds; record them as confirmed or admin closes.
    op.execute(
        """
        UPDATE incidents i SET close_kind = CASE
          WHEN EXISTS (SELECT 1 FROM incident_events e WHERE e.incident_id = i.id
                       AND e.type = 'status_changed' AND e.payload->>'to' = 'Closed'
                       AND e.actor_id = i.created_by) THEN 'confirmed' ELSE 'admin' END
        WHERE status = 'Closed'
        """
    )
    op.alter_column("incidents", "ack_due_at", nullable=False)
    op.alter_column("incidents", "resolve_due_at", nullable=False)
    op.create_check_constraint(
        "ck_incidents_triage",
        "incidents",
        "triage_reason IN ('unrouted_group','no_engineer','returned')",
    )
    op.create_check_constraint(
        "ck_incidents_triage_unassigned", "incidents", "triage_reason IS NULL OR assigned_to IS NULL"
    )
    op.create_check_constraint(
        "ck_incidents_close_kind",
        "incidents",
        "close_kind IN ('confirmed','admin','auto','duplicate')",
    )
    op.create_check_constraint(
        "ck_incidents_duplicate_kind",
        "incidents",
        "(close_kind = 'duplicate') = (duplicate_of_id IS NOT NULL)",
    )
    op.create_check_constraint("ck_incidents_duplicate_self", "incidents", "duplicate_of_id <> id")
    op.create_check_constraint(
        "ck_incidents_route_returns", "incidents", "route_return_count >= 0"
    )

    op.create_table(
        "incident_followers",
        sa.Column("incident_id", sa.UUID(), sa.ForeignKey("incidents.id"), primary_key=True),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_incident_followers_user_id", "incident_followers", ["user_id"])

    op.create_table(
        "notifications",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("incident_id", sa.UUID(), sa.ForeignKey("incidents.id"), nullable=False),
        sa.Column("event_id", sa.UUID(), sa.ForeignKey("incident_events.id"), nullable=True),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "type IN ('status_changed','assigned','unassigned','returned_to_triage',"
            "'note_added','escalation_decided','duplicate_marked','sla_at_risk','sla_breached')",
            name="ck_notifications_type",
        ),
    )
    op.create_index("ix_notifications_user_unread", "notifications", ["user_id", "read_at"])
    op.create_index("ix_notifications_user_created", "notifications", ["user_id", "created_at"])

    op.create_table(
        "attachments",
        sa.Column("incident_id", sa.UUID(), sa.ForeignKey("incidents.id"), nullable=False),
        sa.Column("note_id", sa.UUID(), sa.ForeignKey("incident_notes.id"), nullable=True),
        sa.Column("uploaded_by", sa.UUID(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("s3_key", sa.Text(), nullable=False),
        sa.Column("content_type", sa.String(16), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        *_entity_columns(),
        sa.UniqueConstraint("s3_key", name="uq_attachments_key"),
        sa.CheckConstraint(
            "content_type IN ('image/jpeg','image/png','image/webp','image/heic')",
            name="ck_attachments_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending','accepted','rejected')", name="ck_attachments_status"
        ),
        sa.CheckConstraint("size_bytes BETWEEN 1 AND 5242880", name="ck_attachments_size"),
    )
    op.create_index("ix_attachments_incident_id", "attachments", ["incident_id"])


def downgrade():
    """Drop the new structures; refuses while lead roles exist so no grants are lost silently."""
    leads = sa.text("SELECT count(*) FROM users WHERE roles @> '[\"lead\"]'::jsonb")
    if op.get_bind().scalar(leads):
        raise RuntimeError("Remove the lead role from users before downgrading")
    # The restored event-type check has no 'attachment_added'; fail clearly up front
    # instead of partway through.
    attachment_events = sa.text(
        "SELECT count(*) FROM incident_events WHERE type = 'attachment_added'"
    )
    if op.get_bind().scalar(attachment_events):
        raise RuntimeError("Attachment events exist; this downgrade would lose them")
    op.drop_table("attachments")
    op.drop_table("notifications")
    op.drop_table("incident_followers")
    for name in [
        "ck_incidents_route_returns",
        "ck_incidents_duplicate_self",
        "ck_incidents_duplicate_kind",
        "ck_incidents_close_kind",
        "ck_incidents_triage_unassigned",
        "ck_incidents_triage",
    ]:
        op.drop_constraint(name, "incidents", type_="check")
    op.drop_index("ix_incidents_resolved_at", "incidents")
    op.drop_index("ix_incidents_triage", "incidents")
    op.drop_index("ix_incidents_routed_team_id", "incidents")
    for name in [
        "resolve_breach_alerted_at",
        "resolve_at_risk_alerted_at",
        "ack_breach_alerted_at",
        "ack_at_risk_alerted_at",
        "resolve_due_at",
        "ack_due_at",
        "route_return_count",
        "triage_reason",
        "routed_team_id",
        "duplicate_of_id",
        "close_kind",
    ]:
        op.drop_column("incidents", name)
    op.drop_column("users", "default_seat_id")
    op.drop_constraint("ck_categories_group_top_level", "categories", type_="check")
    op.drop_index("ix_categories_group_id", "categories")
    op.drop_column("categories", "group_id")
    op.drop_table("category_groups")
    op.drop_column("engineer_profiles", "home_building_ids")
    op.drop_index("ix_engineer_profiles_team_id", "engineer_profiles")
    op.drop_column("engineer_profiles", "team_id")
    op.drop_table("teams")
    op.drop_constraint("ck_incident_events_type", "incident_events", type_="check")
    op.create_check_constraint(
        "ck_incident_events_type",
        "incident_events",
        "type IN ('created','details_changed','status_changed','assigned','unassigned',"
        "'priority_changed','escalation_requested','escalation_approved',"
        "'escalation_rejected','escalation_superseded','note_added','note_edited',"
        "'note_hidden','duplicate_marked','auto_closed')",
    )
    op.drop_constraint("ck_users_roles_values", "users", type_="check")
    op.create_check_constraint(
        "ck_users_roles_values", "users", 'roles <@ \'["engineer","admin"]\'::jsonb'
    )
