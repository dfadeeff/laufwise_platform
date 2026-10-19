"""add workspace_module: which modules a workspace runs (ADR-0028)

Every workspace that already holds anything (an agent, a deployment, a connection, a document, a
service) was built as a medical practice, and keeps working as one: it gets `praxis`. So does the
legacy `default` tenant, which local development and the test suite run as. A workspace that holds
nothing yet gets no row and is asked what it is for the next time someone opens it.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "8b9c0d1e2f3a"
down_revision = "7a8b9c0d1e2f"
branch_labels = None
depends_on = None

_IN_USE = " OR ".join(
    f"EXISTS (SELECT 1 FROM {table} WHERE {table}.tenant_id = tenant.id)"
    for table in (
        "studio_agent",
        "agent_instance",
        "connection",
        "knowledge_document",
        "workspace_service",
    )
)


def upgrade():
    op.create_table(
        "workspace_module",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenant.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("module_key", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "module_key", name="uq_workspace_module_key"),
    )
    op.create_index("ix_workspace_module_tenant_id", "workspace_module", ["tenant_id"])
    op.execute(
        "INSERT INTO workspace_module (id, tenant_id, module_key) "
        f"SELECT gen_random_uuid(), tenant.id, 'praxis' FROM tenant "
        f"WHERE tenant.clerk_org_id IS NULL OR {_IN_USE}"
    )


def downgrade():
    op.drop_index("ix_workspace_module_tenant_id", table_name="workspace_module")
    op.drop_table("workspace_module")
