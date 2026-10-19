"""workflow_case.drafts: the letters written from a ready result (ADR-0032)"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "cf3a4b5c6d7e"
down_revision = "be2f3a4b5c6d"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "workflow_case",
        sa.Column("drafts", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )


def downgrade():
    op.drop_column("workflow_case", "drafts")
