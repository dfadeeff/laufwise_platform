"""builder_run.changes: every part a run changed, as a diff a person reads (ADR-0039 step 6e)"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "e7f2a3b4c5d6"
down_revision = "d5e0f1a2b3c4"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("builder_run", sa.Column("changes", JSONB, nullable=False, server_default="[]"))


def downgrade():
    op.drop_column("builder_run", "changes")
