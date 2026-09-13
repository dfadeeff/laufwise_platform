"""optionally link legacy import jobs to operational tasks

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-08-23 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("import_job", sa.Column("task_id", UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_import_job_task_id", "import_job", "task", ["task_id"], ["id"])
    op.create_index("ix_import_job_task_id", "import_job", ["task_id"])


def downgrade() -> None:
    op.drop_index("ix_import_job_task_id", table_name="import_job")
    op.drop_constraint("fk_import_job_task_id", "import_job", type_="foreignkey")
    op.drop_column("import_job", "task_id")
