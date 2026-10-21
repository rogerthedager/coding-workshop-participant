"""Constrain reusable domains without changing stored values or PostgreSQL types.

The values below are frozen migration history, independent of future Python enums.
Existing rows are validated when adding each constraint. Invalid legacy values
stop the transactional upgrade for an operator to review; no rows are rewritten.
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    """Add missing database guards while preserving every business record."""
    op.create_check_constraint(
        "ck_users_roles_values", "users", 'roles <@ \'["engineer","admin"]\'::jsonb'
    )
    op.create_check_constraint(
        "ck_incidents_blocked_reason",
        "incidents",
        "blocked_reason IN ('Waiting on parts','Waiting on employee','Vendor needed',"
        "'Access needed','Other')",
    )
    op.create_check_constraint(
        "ck_incident_events_type",
        "incident_events",
        "type IN ('created','details_changed','status_changed','assigned','unassigned',"
        "'priority_changed','escalation_requested','escalation_approved',"
        "'escalation_rejected','escalation_superseded','note_added','note_edited',"
        "'note_hidden','duplicate_marked','auto_closed')",
    )


def downgrade():
    """Remove only the new guards; values, column types and old checks are retained."""
    op.drop_constraint("ck_incident_events_type", "incident_events", type_="check")
    op.drop_constraint("ck_incidents_blocked_reason", "incidents", type_="check")
    op.drop_constraint("ck_users_roles_values", "users", type_="check")
