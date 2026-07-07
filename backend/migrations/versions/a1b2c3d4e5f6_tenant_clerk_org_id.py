"""add tenant.clerk_org_id (org = tenant, ADR-0003)

Revision ID: a1b2c3d4e5f6
Revises: 40abe0727dcf
Create Date: 2026-07-08 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "40abe0727dcf"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tenant", sa.Column("clerk_org_id", sa.String(length=120), nullable=True))
    op.create_index(op.f("ix_tenant_clerk_org_id"), "tenant", ["clerk_org_id"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_tenant_clerk_org_id"), table_name="tenant")
    op.drop_column("tenant", "clerk_org_id")