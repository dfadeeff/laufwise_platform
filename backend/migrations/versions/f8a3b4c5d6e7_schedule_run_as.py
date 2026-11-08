"""agent_schedule.run_as: the administrator a schedule runs for (ADR-0040 D5). It was always the
creator, so another admin resuming a schedule whose creator left was paused again at its next run.

Revision ID: f8a3b4c5d6e7
Revises: e7f2a3b4c5d6
"""

import sqlalchemy as sa
from alembic import op

revision = "f8a3b4c5d6e7"
down_revision = "e7f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_schedule", sa.Column("run_as", sa.String(120), nullable=True))
    op.execute("UPDATE agent_schedule SET run_as = created_by")
    op.alter_column("agent_schedule", "run_as", nullable=False)


def downgrade() -> None:
    op.drop_column("agent_schedule", "run_as")
