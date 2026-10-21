"""Allow building-level incidents without losing existing location references."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("incidents", "floor_id", existing_type=sa.UUID(), nullable=True)
    op.create_check_constraint(
        "ck_incidents_seat_requires_floor", "incidents",
        "seat_id IS NULL OR floor_id IS NOT NULL",
    )


def downgrade():
    # Refuse to invent floors or delete building-level records on downgrade.
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM incidents WHERE floor_id IS NULL")):
        raise RuntimeError("Assign valid floors to building-level incidents before downgrade")
    op.drop_constraint("ck_incidents_seat_requires_floor", "incidents", type_="check")
    op.alter_column("incidents", "floor_id", existing_type=sa.UUID(), nullable=False)
