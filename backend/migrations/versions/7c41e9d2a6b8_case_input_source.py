"""workflow_case.input_source and to_check: who wrote the input, and what to check (ADR-0033)

An input a model read from the documents is "extracted" until a person saves it; until then it is
not computed. `to_check` lists the values the documents' own text does not contain.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "7c41e9d2a6b8"
down_revision = "be2f3a4b5c6d"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "workflow_case",
        sa.Column("input_source", sa.String(20), nullable=False, server_default="person"),
    )
    op.add_column(
        "workflow_case",
        sa.Column("to_check", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )


def downgrade():
    op.drop_column("workflow_case", "to_check")
    op.drop_column("workflow_case", "input_source")
