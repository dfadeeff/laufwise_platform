"""add workflow_agent, workflow_case, workflow_case_file, workflow_case_event (ADR-0030)

A workflow agent is created by a workspace from a module's workflow; each piece of work it does is
a case with its files, its input, the result computed from it, and an append-only history.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "ad1e2f3a4b5c"
down_revision = "9c0d1e2f3a4b"
branch_labels = None
depends_on = None


def _timestamps():
    return [
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    ]


def upgrade():
    op.create_table(
        "workflow_agent",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("module_key", sa.String(40), nullable=False),
        sa.Column("workflow_key", sa.String(60), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("settings", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        *_timestamps(),
    )
    op.create_index("ix_workflow_agent_tenant_id", "workflow_agent", ["tenant_id"])

    op.create_table(
        "workflow_case",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column(
            "agent_id", UUID(as_uuid=True), sa.ForeignKey("workflow_agent.id"), nullable=False
        ),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("input", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("input_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("computed_revision", sa.Integer(), nullable=True),
        sa.Column("rules_version", sa.String(60), nullable=True),
        sa.Column("result", JSONB(), nullable=True),
        sa.Column("errors", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("questions", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("assumptions", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("problems", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        *_timestamps(),
    )
    op.create_index("ix_workflow_case_tenant_id", "workflow_case", ["tenant_id"])
    op.create_index("ix_workflow_case_agent_id", "workflow_case", ["agent_id"])

    op.create_table(
        "workflow_case_file",
        sa.Column(
            "case_id",
            UUID(as_uuid=True),
            sa.ForeignKey("workflow_case.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "file_id", UUID(as_uuid=True), sa.ForeignKey("stored_file.id"), primary_key=True
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "workflow_case_event",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "case_id",
            UUID(as_uuid=True),
            sa.ForeignKey("workflow_case.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("payload", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_workflow_case_event_case_id", "workflow_case_event", ["case_id"])


def downgrade():
    op.drop_index("ix_workflow_case_event_case_id", table_name="workflow_case_event")
    op.drop_table("workflow_case_event")
    op.drop_table("workflow_case_file")
    op.drop_index("ix_workflow_case_agent_id", table_name="workflow_case")
    op.drop_index("ix_workflow_case_tenant_id", table_name="workflow_case")
    op.drop_table("workflow_case")
    op.drop_index("ix_workflow_agent_tenant_id", table_name="workflow_agent")
    op.drop_table("workflow_agent")
