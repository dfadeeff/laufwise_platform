"""an import job names the run it queued after itself (ADR-0044 D7)

A calendar import hands the same days to the doctolib block mirror once it completes; the
Studio shows that run under the import that started it.

Revision ID: b2d3e4f5a6c7
Revises: a1c2d3e4f5b6
Create Date: 2026-11-13 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b2d3e4f5a6c7"
down_revision: Union[str, None] = "a1c2d3e4f5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("import_job", sa.Column("followed_by", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("import_job", "followed_by")
