"""add schedule_worker: each schedule worker's last tick, so the Studio can say whether schedules
run at all (ADR-0040 D9)"""

import sqlalchemy as sa
from alembic import op

revision = "c4d9e0f1a2b3"
down_revision = "b3c8d9e0f1a2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "schedule_worker",
        sa.Column("node", sa.String(80), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_tick_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("schedule_worker")
