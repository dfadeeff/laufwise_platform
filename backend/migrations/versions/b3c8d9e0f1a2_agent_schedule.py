"""add agent_schedule and schedule_occurrence, and case_run.trigger: a workspace schedules its
agents (ADR-0040)"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "b3c8d9e0f1a2"
down_revision = "a2b6c7d8e9f0"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "case_run", sa.Column("trigger", sa.String(20), nullable=False, server_default="manual")
    )
    op.create_table(
        "agent_schedule",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("target_kind", sa.String(20), nullable=False),
        sa.Column("target_id", UUID(as_uuid=True), nullable=False),
        sa.Column("target_version_id", UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("rule", JSONB, nullable=False),
        sa.Column("timezone", sa.String(60), nullable=False),
        sa.Column("input", JSONB, nullable=False, server_default="{}"),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revision", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_by", sa.String(120), nullable=False),
        sa.Column("updated_by", sa.String(120), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_agent_schedule_tenant_id", "agent_schedule", ["tenant_id"])
    op.create_index("ix_agent_schedule_target_id", "agent_schedule", ["target_id"])
    op.create_index("ix_agent_schedule_next_run_at", "agent_schedule", ["next_run_at"])
    op.create_table(
        "schedule_occurrence",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("schedule_id", UUID(as_uuid=True), sa.ForeignKey("agent_schedule.id"), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("origin", sa.String(20), nullable=False, server_default="schedule"),
        sa.Column("schedule_revision", sa.Integer, nullable=False),
        sa.Column("target_kind", sa.String(20), nullable=False),
        sa.Column("target_id", UUID(as_uuid=True), nullable=False),
        sa.Column("target_version_id", UUID(as_uuid=True), nullable=True),
        sa.Column("input", JSONB, nullable=False, server_default="{}"),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("attempt", sa.Integer, nullable=False, server_default="0"),
        sa.Column("locked_by", sa.String(80), nullable=True),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("run_ref", sa.String(80), nullable=True),
        sa.Column("outcome", sa.String(20), nullable=True),
        sa.Column("error", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("schedule_id", "scheduled_for", name="uq_schedule_occurrence_due"),
    )
    op.create_index("ix_schedule_occurrence_tenant_id", "schedule_occurrence", ["tenant_id"])
    op.create_index("ix_schedule_occurrence_schedule_id", "schedule_occurrence", ["schedule_id"])
    op.create_index("ix_schedule_occurrence_status", "schedule_occurrence", ["status", "scheduled_for"])


def downgrade():
    op.drop_table("schedule_occurrence")
    op.drop_table("agent_schedule")
    op.drop_column("case_run", "trigger")
