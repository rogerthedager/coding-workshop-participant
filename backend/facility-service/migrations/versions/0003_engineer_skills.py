"""Add up to two engineer skills, drawn from the top-level incident categories.

The values below are frozen migration history, independent of future Python enums.
Existing engineer profiles start with no skills; no other rows are changed.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

SKILLS = (
    '["IT and AV equipment","Network and Wi-Fi","Furniture","HVAC and temperature",'
    '"Electrical and lighting","Plumbing and restrooms","Cleaning","Access and security"]'
)


def upgrade():
    """Add the skills column with an empty default and its database guards."""
    op.add_column(
        "engineer_profiles",
        sa.Column("skills", JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
    )
    op.create_check_constraint(
        "ck_engineer_skills_array", "engineer_profiles", "jsonb_typeof(skills) = 'array'"
    )
    op.create_check_constraint(
        "ck_engineer_skills_limit",
        "engineer_profiles",
        "CASE WHEN jsonb_typeof(skills) = 'array' THEN jsonb_array_length(skills) <= 2 END",
    )
    op.create_check_constraint(
        "ck_engineer_skills_values", "engineer_profiles", f"skills <@ '{SKILLS}'::jsonb"
    )


def downgrade():
    """Remove skills; this discards any skills assigned since the upgrade."""
    op.drop_constraint("ck_engineer_skills_values", "engineer_profiles", type_="check")
    op.drop_constraint("ck_engineer_skills_limit", "engineer_profiles", type_="check")
    op.drop_constraint("ck_engineer_skills_array", "engineer_profiles", type_="check")
    op.drop_column("engineer_profiles", "skills")
