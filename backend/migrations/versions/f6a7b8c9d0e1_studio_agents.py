"""Stable Studio agents, immutable instance snapshots and verified phone assignments."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "studio_agent",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("draft", JSONB, nullable=False),
        sa.Column("generation", sa.Integer, nullable=False),
        sa.Column("published_instance_id", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_studio_agent_tenant_id", "studio_agent", ["tenant_id"])
    op.add_column(
        "agent_instance",
        sa.Column("agent_id", UUID(as_uuid=True), sa.ForeignKey("studio_agent.id"), nullable=True),
    )
    op.create_index("ix_agent_instance_agent_id", "agent_instance", ["agent_id"])
    op.add_column("agent_instance", sa.Column("revision", sa.Integer, nullable=True))
    op.add_column("agent_instance", sa.Column("runtime_config", JSONB, nullable=True))
    op.add_column("agent_instance", sa.Column("snapshot_kind", sa.String(20), nullable=True))
    op.create_table(
        "voice_channel",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column(
            "agent_id",
            UUID(as_uuid=True),
            sa.ForeignKey("studio_agent.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "instance_id", UUID(as_uuid=True), sa.ForeignKey("agent_instance.id"), nullable=False
        ),
        sa.Column(
            "connection_id", UUID(as_uuid=True), sa.ForeignKey("connection.id"), nullable=False
        ),
        sa.Column("phone_number", sa.String(40), nullable=False, unique=True),
        sa.Column("active", sa.Boolean, nullable=False),
    )
    op.create_index("ix_voice_channel_tenant_id", "voice_channel", ["tenant_id"])


def downgrade():
    op.drop_table("voice_channel")
    for column in ("snapshot_kind", "runtime_config", "revision", "agent_id"):
        op.drop_column("agent_instance", column)
    op.drop_table("studio_agent")
