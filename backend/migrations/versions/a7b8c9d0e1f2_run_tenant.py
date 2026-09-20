"""A run carries its own owner, so it can be read back by the practice that started it."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "a7b8c9d0e1f2"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "run", sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenant.id"), nullable=True)
    )
    op.create_index("ix_run_tenant_id", "run", ["tenant_id"])
    # Runs that came from a deployed instance already have an owner; carry it over so existing
    # history stays visible. A run with neither stays unowned and stays hidden.
    op.execute(
        "UPDATE run SET tenant_id = agent_instance.tenant_id "
        "FROM agent_instance WHERE run.instance_id = agent_instance.id"
    )


def downgrade():
    op.drop_index("ix_run_tenant_id", table_name="run")
    op.drop_column("run", "tenant_id")
