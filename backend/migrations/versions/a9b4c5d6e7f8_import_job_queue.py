"""import_job: queued for the schedule worker, with the window, attempt and holder it needs to be
run again by another worker (imports no longer run in the API process).

Revision ID: a9b4c5d6e7f8
Revises: f8a3b4c5d6e7
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "a9b4c5d6e7f8"
down_revision = "f8a3b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("import_job", sa.Column("window", JSONB, nullable=True))
    op.add_column("import_job", sa.Column("attempt", sa.Integer, nullable=False, server_default="0"))
    op.add_column("import_job", sa.Column("locked_by", sa.String(120), nullable=True))
    # Only the worker takes queued jobs, oldest first.
    op.create_index(
        "ix_import_job_queued", "import_job", ["created_at"], postgresql_where=sa.text("status = 'queued'")
    )


def downgrade() -> None:
    op.drop_index("ix_import_job_queued", table_name="import_job")
    op.drop_column("import_job", "locked_by")
    op.drop_column("import_job", "attempt")
    op.drop_column("import_job", "window")
