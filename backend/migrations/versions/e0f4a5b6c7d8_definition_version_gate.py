"""add definition_version.gate: what the publish gate reported (ADR-0039 step 4)

Written once, when a version is published, and sealed with it by the existing trigger. Versions
published before the gate existed have none.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "e0f4a5b6c7d8"
down_revision = "d9e3f4a5b6c7"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("definition_version", sa.Column("gate", JSONB, nullable=True))


def downgrade():
    op.drop_column("definition_version", "gate")
