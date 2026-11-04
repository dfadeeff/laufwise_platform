"""add builder_run: a run of the builder on a workspace's definition (ADR-0039 D6, step 6)"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "a2b6c7d8e9f0"
down_revision = "f1a5b6c7d8e9"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "builder_run",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("definition_id", UUID(as_uuid=True), sa.ForeignKey("definition.id"), nullable=False),
        sa.Column("requested_by", sa.String(120), nullable=False),
        sa.Column("instruction", sa.Text, nullable=False),
        sa.Column("file_ids", JSONB, nullable=False, server_default="[]"),
        sa.Column("state", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("model", sa.String(80), nullable=False, server_default=""),
        sa.Column("rounds", JSONB, nullable=False, server_default="[]"),
        sa.Column("summary", sa.Text, nullable=False, server_default=""),
        sa.Column("guardrail_changes", JSONB, nullable=False, server_default="[]"),
        sa.Column("draft_version", sa.Integer, nullable=True),
        sa.Column("error", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_builder_run_tenant_id", "builder_run", ["tenant_id"])
    op.create_index("ix_builder_run_definition_id", "builder_run", ["definition_id"])


def downgrade():
    op.drop_table("builder_run")
