"""the doctolib block mirror's ledger of the blocks it created (ADR-0044)

doctolib cannot list blocks, so the mirror records the id of each block it creates and touches
only those.

Revision ID: a1c2d3e4f5b6
Revises: f9b4c5d6e7a8
Create Date: 2026-11-12 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1c2d3e4f5b6"
down_revision: Union[str, None] = "f9b4c5d6e7a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "doctolib_block",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("connection_id", sa.Uuid(), sa.ForeignKey("connection.id"), nullable=False),
        sa.Column("event_id", sa.String(40), nullable=False),
        sa.Column("agenda_id", sa.String(40), nullable=False),
        sa.Column("day", sa.String(10), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("connection_id", "event_id", name="uq_doctolib_block_event"),
    )
    op.create_index("ix_doctolib_block_tenant_id", "doctolib_block", ["tenant_id"])
    # What a run reads: one connection's live blocks on one day.
    op.create_index(
        "ix_doctolib_block_live_day", "doctolib_block", ["connection_id", "day"],
        postgresql_where=sa.text("removed_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_doctolib_block_live_day", table_name="doctolib_block")
    op.drop_index("ix_doctolib_block_tenant_id", table_name="doctolib_block")
    op.drop_table("doctolib_block")
