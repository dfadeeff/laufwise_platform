"""add import_job (background calendar-import runs, ADR-0004 D4)

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-07-09 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "import_job",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("instance_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("created", JSONB(), nullable=False),
        sa.Column("skipped", JSONB(), nullable=False),
        sa.Column("failed", JSONB(), nullable=False),
        sa.Column("excluded", JSONB(), nullable=False),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"]),
        sa.ForeignKeyConstraint(["instance_id"], ["agent_instance.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_import_job_tenant_id"), "import_job", ["tenant_id"])
    op.create_index(op.f("ix_import_job_instance_id"), "import_job", ["instance_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_import_job_instance_id"), table_name="import_job")
    op.drop_index(op.f("ix_import_job_tenant_id"), table_name="import_job")
    op.drop_table("import_job")
