"""Durable execution intent and ownership of builder work across rolling deploys."""

import sqlalchemy as sa
from alembic import op

revision = "f9b4c5d6e7a8"
down_revision = "c1d6e7f8a9b0"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("run", sa.Column("lifecycle", sa.String(20), nullable=False, server_default="completed"))
    op.add_column("builder_run", sa.Column("owner_id", sa.String(36), nullable=True))
    op.add_column("builder_run", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column("builder_run", "lease_expires_at")
    op.drop_column("builder_run", "owner_id")
    op.drop_column("run", "lifecycle")
