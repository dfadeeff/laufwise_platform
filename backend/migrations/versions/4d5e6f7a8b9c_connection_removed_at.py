"""A connection can be removed: its login wiped, the row kept for history."""

from alembic import op
import sqlalchemy as sa

revision = "4d5e6f7a8b9c"
down_revision = "3c4d5e6f7a8b"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("connection", sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column("connection", "removed_at")
