"""add case_run: a case's run through its definition's procedure (ADR-0039 D3, step 3)

Lifecycle state and outcome are separate columns: one says where the run is, the other what it
came to.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "c8d2e3f4a5b6"
down_revision = "b7c1d2e3f4a5"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "case_run",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column(
            "case_id", UUID(as_uuid=True), sa.ForeignKey("workflow_case.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("definition_version_id", UUID(as_uuid=True), sa.ForeignKey("definition_version.id"), nullable=True),
        sa.Column("input_revision", sa.Integer, nullable=False),
        sa.Column("state", sa.String(20), nullable=False, server_default="running"),
        sa.Column("outcome", sa.String(20), nullable=True),
        sa.Column("position", sa.Integer, nullable=False, server_default="0"),
        sa.Column("outputs", JSONB, nullable=False, server_default="{}"),
        sa.Column("trace", JSONB, nullable=False, server_default="[]"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_case_run_tenant_id", "case_run", ["tenant_id"])
    op.create_index("ix_case_run_case_id", "case_run", ["case_id"])


def downgrade():
    op.drop_index("ix_case_run_case_id", table_name="case_run")
    op.drop_index("ix_case_run_tenant_id", table_name="case_run")
    op.drop_table("case_run")
