"""add import_job.forced (appointments written past the destination's own check, ADR-0005 D7)

Its own column rather than a flag inside `created`: a forced write bypassed thevea's working-hours
validation, so it is the one bucket an operator must actually look at. Mixing it into `created`
would make an override invisible — which is indistinguishable from a bug.

Backfilled to an empty list so existing rows stay valid without a data migration.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-08-03 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "import_job",
        sa.Column("forced", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )


def downgrade() -> None:
    op.drop_column("import_job", "forced")
