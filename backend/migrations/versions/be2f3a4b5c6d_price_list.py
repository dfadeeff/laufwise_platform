"""add price_list and workspace_service.price_label: prices read from the practice website (ADR-0032)

`price_list` is one row per workspace: the page it reads and every price row it has seen there.
A service names its row by `price_label`. Additive only: no existing row changes meaning.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "be2f3a4b5c6d"
down_revision = "ad1e2f3a4b5c"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "price_list",
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenant.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("url", sa.String(500), nullable=False),
        sa.Column("rows", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "workspace_service",
        sa.Column("price_label", sa.String(200), nullable=False, server_default=""),
    )


def downgrade():
    op.drop_column("workspace_service", "price_label")
    op.drop_table("price_list")
