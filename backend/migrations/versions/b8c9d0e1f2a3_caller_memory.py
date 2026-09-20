"""Remember a returning caller by a key, never by what is in their calendar (ADR-0011)."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "b8c9d0e1f2a3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "caller_memory",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column(
            "agent_id", UUID(as_uuid=True), sa.ForeignKey("studio_agent.id"), nullable=False
        ),
        # sha256(pepper + tenant + E.164). Exact-match lookup needs no more than this, and the
        # table is therefore never a list of patients' phone numbers.
        sa.Column("caller_hash", sa.String(64), nullable=False),
        sa.Column("patient_id", sa.Integer, nullable=True),
        sa.Column("display_name", sa.String(160), nullable=True),
        sa.Column("locale", sa.String(5), nullable=True),
        sa.Column("last_outcome", sa.String(40), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("call_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("tenant_id", "agent_id", "caller_hash", name="uq_caller_memory_scope"),
    )
    op.create_index("ix_caller_memory_tenant_id", "caller_memory", ["tenant_id"])
    op.create_index("ix_caller_memory_agent_id", "caller_memory", ["agent_id"])
    # The retention sweep deletes by age, and it should not read the whole table to do it.
    op.create_index("ix_caller_memory_last_seen_at", "caller_memory", ["last_seen_at"])


def downgrade():
    op.drop_table("caller_memory")
