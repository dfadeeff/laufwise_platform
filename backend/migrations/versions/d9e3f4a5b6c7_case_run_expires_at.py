"""add case_run.expires_at: when a run held at a wait expires (ADR-0039 D3, step 3b)

Only a suspended run has one; the sweep that ends unattended waits finds them by it.
"""

import sqlalchemy as sa
from alembic import op

revision = "d9e3f4a5b6c7"
down_revision = "c8d2e3f4a5b6"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("case_run", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_case_run_expires_at", "case_run", ["expires_at"])


def downgrade():
    op.drop_index("ix_case_run_expires_at", table_name="case_run")
    op.drop_column("case_run", "expires_at")
