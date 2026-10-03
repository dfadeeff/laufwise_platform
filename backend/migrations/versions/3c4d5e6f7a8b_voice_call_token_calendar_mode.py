"""A test call can use the practice's real calendar: the admission records which, and how (ADR-0018)."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "3c4d5e6f7a8b"
down_revision = "2b3c4d5e6f7a"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "voice_call_token",
        sa.Column("calendar_mode", sa.String(10), nullable=False, server_default="sandbox"),
    )
    op.add_column(
        "voice_call_token",
        sa.Column("connection_id", UUID(as_uuid=True), sa.ForeignKey("connection.id"), nullable=True),
    )


def downgrade():
    op.drop_column("voice_call_token", "connection_id")
    op.drop_column("voice_call_token", "calendar_mode")
