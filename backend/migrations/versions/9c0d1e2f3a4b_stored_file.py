"""add stored_file: files a workspace uploaded (ADR-0029)

The row describes the file (display name, sniffed media type, size, SHA-256); its bytes live in
the blob store under `storage_key`. Unique per workspace and content, so an upload of the same
file twice is one file.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "9c0d1e2f3a4b"
down_revision = "8b9c0d1e2f3a"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "stored_file",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("media_type", sa.String(80), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "sha256", name="uq_stored_file_sha256"),
    )
    op.create_index("ix_stored_file_tenant_id", "stored_file", ["tenant_id"])


def downgrade():
    op.drop_index("ix_stored_file_tenant_id", table_name="stored_file")
    op.drop_table("stored_file")
