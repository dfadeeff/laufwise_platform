"""workflow_case.drafts: the letters written from a ready result (ADR-0033)"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "93fa1c5e70d4"
down_revision = "7c41e9d2a6b8"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "workflow_case",
        sa.Column("drafts", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )


def downgrade():
    op.drop_column("workflow_case", "drafts")
