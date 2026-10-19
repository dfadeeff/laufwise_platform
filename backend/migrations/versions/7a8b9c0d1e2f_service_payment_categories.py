"""workspace_service.payment_categories: how a service may be paid for (ADR-0027)

The keys of the agent's payment categories a service may be booked with. Empty (every existing
row) means any, so nothing booked today changes until a practice restricts a service.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "7a8b9c0d1e2f"
down_revision = "6f7a8b9c0d1e"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "workspace_service",
        sa.Column(
            "payment_categories", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
    )


def downgrade():
    op.drop_column("workspace_service", "payment_categories")
