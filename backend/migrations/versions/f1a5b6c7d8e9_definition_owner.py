"""add definition.tenant_id and template_key: a workspace owns the definitions it builds (ADR-0039 step 5)

None for the platform's shipped templates, which stay as they are.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "f1a5b6c7d8e9"
down_revision = "e0f4a5b6c7d8"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("definition", sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenant.id"), nullable=True))
    op.add_column("definition", sa.Column("template_key", sa.String(60), nullable=True))
    op.create_index("ix_definition_tenant_id", "definition", ["tenant_id"])


def downgrade():
    op.drop_index("ix_definition_tenant_id", table_name="definition")
    op.drop_column("definition", "template_key")
    op.drop_column("definition", "tenant_id")
