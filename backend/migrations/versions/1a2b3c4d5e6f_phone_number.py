"""A practice owns the numbers it claimed from the pool, instead of an environment variable."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "1a2b3c4d5e6f"
down_revision = "c0d1e2f3a4b5"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "phone_number",
        # E.164. The key, so two practices racing for one number cannot both claim it.
        sa.Column("number", sa.String(20), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("twilio_sid", sa.String(64), nullable=False),
        sa.Column(
            "claimed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_phone_number_tenant_id", "phone_number", ["tenant_id"])


def downgrade():
    op.drop_table("phone_number")
