"""letter_delivery: one row per attempt to send an approved letter, at most one live per letter
(ADR-0043). Copies what `approval_request.deliveries` recorded; that column stays, unwritten.

Revision ID: c1d6e7f8a9b0
Revises: b0c5d6e7f8a9
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "c1d6e7f8a9b0"
down_revision = "b0c5d6e7f8a9"
branch_labels = None
depends_on = None

# Each entry of the old JSONB list as a row. Idempotent: a letter already copied conflicts with
# its live row and is skipped (a failed one is copied once, by its approval and time).
BACKFILL = """
INSERT INTO letter_delivery
    (id, tenant_id, approval_id, recipient, email, title, status, error, note,
     created_at, updated_at)
SELECT gen_random_uuid(), a.tenant_id, a.id, d->>'recipient', d->>'email',
       COALESCE(d->>'title', ''), d->>'status', d->>'error', '',
       COALESCE((d->>'at')::timestamptz, a.created_at),
       COALESCE((d->>'at')::timestamptz, a.created_at)
FROM approval_request a, jsonb_array_elements(a.deliveries) d
WHERE d->>'status' IN ('sent', 'failed')
  AND NOT EXISTS (
      SELECT 1 FROM letter_delivery l
      WHERE l.approval_id = a.id AND l.recipient = d->>'recipient'
        AND l.created_at = COALESCE((d->>'at')::timestamptz, a.created_at)
  )
ON CONFLICT DO NOTHING
"""


def upgrade() -> None:
    op.create_table(
        "letter_delivery",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column(
            "approval_id",
            UUID(as_uuid=True),
            sa.ForeignKey("approval_request.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("recipient", sa.String(200), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("resolved_by", sa.String(120), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_letter_delivery_tenant_id", "letter_delivery", ["tenant_id"])
    op.create_index("ix_letter_delivery_approval_id", "letter_delivery", ["approval_id"])
    op.create_index(
        "uq_letter_delivery_one_live",
        "letter_delivery",
        ["approval_id", "recipient"],
        unique=True,
        postgresql_where=sa.text("status IN ('sending', 'sent', 'uncertain')"),
    )
    op.execute(BACKFILL)


def downgrade() -> None:
    op.drop_table("letter_delivery")
