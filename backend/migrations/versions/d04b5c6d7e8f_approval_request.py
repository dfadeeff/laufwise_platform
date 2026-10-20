"""add approval_request: a second person approves one action on a case (ADR-0033)

Bound to a fingerprint of what is approved; the requester never decides; deliveries record what
executing the action did. The older `approval` table (run-scoped, never wired) is left as it is.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "d04b5c6d7e8f"
down_revision = "cf3a4b5c6d7e"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "approval_request",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column(
            "case_id",
            UUID(as_uuid=True),
            sa.ForeignKey("workflow_case.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("payload", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("requested_by", sa.String(120), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_by", sa.String(120), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("deliveries", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_approval_request_tenant_id", "approval_request", ["tenant_id"])
    op.create_index("ix_approval_request_case_id", "approval_request", ["case_id"])


def downgrade():
    op.drop_index("ix_approval_request_case_id", table_name="approval_request")
    op.drop_index("ix_approval_request_tenant_id", table_name="approval_request")
    op.drop_table("approval_request")
