"""arm an instance for the backend clock, and label how a job was started

ADR-0010: the occupancy mirror stops depending on an open browser tab.

- agent_instance.schedule — NULL means manual only; a named schedule arms the instance.
- import_job.trigger      — manual | schedule, so a scheduled fire is visible in the record.
- import_job.status gains the value `interrupted` (no DDL — it is a plain string column): what a
  job left `running` by a process restart becomes, so one orphan cannot block every later tick.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-09-20 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("agent_instance", sa.Column("schedule", sa.String(40), nullable=True))
    op.add_column(
        "import_job",
        sa.Column("trigger", sa.String(20), nullable=False, server_default="manual"),
    )
    # Only armed instances are ever selected, so the index stays tiny.
    op.create_index(
        "ix_agent_instance_schedule",
        "agent_instance",
        ["schedule"],
        postgresql_where=sa.text("schedule IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_agent_instance_schedule", table_name="agent_instance")
    op.drop_column("import_job", "trigger")
    op.drop_column("agent_instance", "schedule")
