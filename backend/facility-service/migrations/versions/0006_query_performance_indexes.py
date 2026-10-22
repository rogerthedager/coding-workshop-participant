"""Indexes for report metrics, dashboards, the SLA job and the incident list.

The values below are frozen migration history, independent of future Python enums.
Every change only adds or replaces indexes; no data is rewritten.
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

ACTIVE = "status IN ('Open', 'In Progress', 'Blocked')"


def upgrade():
    """Create the new indexes, then retire the two that composite indexes cover."""
    op.create_index("ix_incident_events_type_created", "incident_events", ["type", "created_at"])
    op.create_index(
        "ix_incident_events_resolved",
        "incident_events",
        ["incident_id", "created_at"],
        postgresql_where=sa.text("type = 'status_changed' AND (payload ->> 'to') = 'Resolved'"),
    )
    op.create_index(
        "ix_incident_notes_public",
        "incident_notes",
        ["incident_id", "created_at"],
        postgresql_where=sa.text("visibility = 'public' AND hidden_at IS NULL"),
    )
    op.create_index("ix_incidents_assignee_status", "incidents", ["assigned_to", "status"])
    op.create_index(
        "ix_incidents_active_ack_due",
        "incidents",
        ["ack_due_at"],
        postgresql_where=sa.text(f"{ACTIVE} AND acknowledged_at IS NULL"),
    )
    op.create_index(
        "ix_incidents_active_resolve_due",
        "incidents",
        ["resolve_due_at"],
        postgresql_where=sa.text(f"{ACTIVE} AND resolved_at IS NULL"),
    )
    op.create_index(
        "ix_escalation_requests_latest",
        "escalation_requests",
        ["incident_id", "requested_at", "id"],
    )
    op.drop_index("ix_escalation_requests_incident_id", table_name="escalation_requests")
    op.drop_index("ix_incidents_assigned_to", table_name="incidents")


def downgrade():
    """Restore the 0005 index set."""
    op.create_index("ix_incidents_assigned_to", "incidents", ["assigned_to"])
    op.create_index(
        "ix_escalation_requests_incident_id", "escalation_requests", ["incident_id"]
    )
    op.drop_index("ix_escalation_requests_latest", table_name="escalation_requests")
    op.drop_index("ix_incidents_active_resolve_due", table_name="incidents")
    op.drop_index("ix_incidents_active_ack_due", table_name="incidents")
    op.drop_index("ix_incidents_assignee_status", table_name="incidents")
    op.drop_index("ix_incident_notes_public", table_name="incident_notes")
    op.drop_index("ix_incident_events_resolved", table_name="incident_events")
    op.drop_index("ix_incident_events_type_created", table_name="incident_events")
