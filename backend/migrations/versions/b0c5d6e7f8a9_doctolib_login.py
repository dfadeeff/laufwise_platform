"""doctolib_login: a doctolib sign-in the schedule worker runs, instead of an in-memory job in the
API process.

Revision ID: b0c5d6e7f8a9
Revises: a9b4c5d6e7f8
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "b0c5d6e7f8a9"
down_revision = "a9b4c5d6e7f8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "doctolib_login",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("username", sa.String(320), nullable=False),
        sa.Column("agenda_ids", sa.String, nullable=False),
        sa.Column("config", JSONB, nullable=False),
        sa.Column("refresh_connection_id", UUID(as_uuid=True), nullable=True),
        sa.Column("secrets_enc", sa.Text, nullable=True),
        sa.Column("code_enc", sa.Text, nullable=True),
        sa.Column("result_enc", sa.Text, nullable=True),
        sa.Column("error", sa.String, nullable=True),
        sa.Column("refused", sa.String(20), nullable=True),
        sa.Column("connection_id", UUID(as_uuid=True), nullable=True),
        sa.Column("locked_by", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_doctolib_login_tenant_id", "doctolib_login", ["tenant_id"])
    op.create_index(
        "ix_doctolib_login_queued", "doctolib_login", ["created_at"],
        postgresql_where=sa.text("status = 'queued'"),
    )


def downgrade() -> None:
    op.drop_index("ix_doctolib_login_queued", table_name="doctolib_login")
    op.drop_index("ix_doctolib_login_tenant_id", table_name="doctolib_login")
    op.drop_table("doctolib_login")
