"""add workspace_service: a workspace's bookable services (ADR-0025)

Each row is one service the practice offers: its name, the calendar system's own id for it
(Thevea: a price-list position), a duration, how callers say it per language, and whether it may be
booked by phone. Publishing copies the catalogue into the agent's snapshot.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "6f7a8b9c0d1e"
down_revision = "5e6f7a8b9c0d"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "workspace_service",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("key", sa.String(80), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("external_id", sa.String(80), nullable=False, server_default=""),
        sa.Column("duration_minutes", sa.Integer(), nullable=True),
        sa.Column("aliases", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("bookable", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("consultation", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "key", name="uq_workspace_service_key"),
    )
    op.create_index("ix_workspace_service_tenant_id", "workspace_service", ["tenant_id"])


def downgrade():
    op.drop_index("ix_workspace_service_tenant_id", table_name="workspace_service")
    op.drop_table("workspace_service")
