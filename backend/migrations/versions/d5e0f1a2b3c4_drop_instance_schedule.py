"""drop agent_instance.schedule: the old clock (ADR-0010 D3) is retired; workspaces schedule
their agents on agent_schedule (ADR-0040)

Its two cron services were never created in production, so no armed row was ever fired; the
arming carries nothing worth moving onto a schedule, and creating schedules from it would start
imports nobody saw run before.
"""

import sqlalchemy as sa
from alembic import op

revision = "d5e0f1a2b3c4"
down_revision = "c4d9e0f1a2b3"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_column("agent_instance", "schedule")


def downgrade():
    op.add_column("agent_instance", sa.Column("schedule", sa.String(40), nullable=True))
