"""${message}."""
from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}

def upgrade():
    """Apply the forward migration."""
    ${upgrades if upgrades else "pass"}

def downgrade():
    """Reverse only when explicitly approved by an operator."""
    ${downgrades if downgrades else "pass"}
