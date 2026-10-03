"""Admit a call in the database, so its audio can arrive at any replica."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "c0d1e2f3a4b5"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "voice_call_token",
        # sha256 of the token. The token itself only ever exists in the TwiML or the Studio's url.
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column(
            "conversation_id",
            UUID(as_uuid=True),
            sa.ForeignKey("conversation.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("language", sa.String(5), nullable=False),
        sa.Column("rehearsal", sa.Boolean, nullable=False),
        sa.Column("caller_number", sa.String(40), nullable=True),
        sa.Column("recall", sa.Text, nullable=True),
        sa.Column("caller_hash", sa.String(64), nullable=True),
        sa.Column("agent_id", UUID(as_uuid=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    # Expired admissions are swept on every new one; the sweep should not read the table to do it.
    op.create_index("ix_voice_call_token_expires_at", "voice_call_token", ["expires_at"])


def downgrade():
    op.drop_table("voice_call_token")
