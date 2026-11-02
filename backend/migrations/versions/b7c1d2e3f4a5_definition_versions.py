"""add definition and definition_version: workflow definitions as versioned data (ADR-0039 step 2b)

A definition is a stable key; its versions hold the content that runs. A draft may change; a
published version may not: a trigger refuses any UPDATE or DELETE of a published row, so history
is append-only in the database, not only in the code. A case records the version it was computed
with.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "b7c1d2e3f4a5"
down_revision = "e58b2d7f1a3c"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "definition",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("key", sa.String(60), nullable=False, unique=True),
        sa.Column("module_key", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "definition_version",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("definition_id", UUID(as_uuid=True), sa.ForeignKey("definition.id"), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("content", JSONB, nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("definition_id", "version", name="uq_definition_version"),
    )
    op.create_index("ix_definition_version_definition_id", "definition_version", ["definition_id"])
    op.create_index(
        "uq_definition_one_draft",
        "definition_version",
        ["definition_id"],
        unique=True,
        postgresql_where=sa.text("status = 'draft'"),
    )
    op.add_column(
        "workflow_case",
        sa.Column(
            "definition_version_id",
            UUID(as_uuid=True),
            sa.ForeignKey("definition_version.id"),
            nullable=True,
        ),
    )
    op.execute(
        """
        CREATE FUNCTION definition_version_is_sealed() RETURNS trigger AS $$
        BEGIN
            IF OLD.status = 'published' THEN
                RAISE EXCEPTION 'definition_version % is published and cannot change', OLD.id;
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER definition_version_sealed
        BEFORE UPDATE OR DELETE ON definition_version
        FOR EACH ROW EXECUTE FUNCTION definition_version_is_sealed();
        """
    )


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS definition_version_sealed ON definition_version")
    op.execute("DROP FUNCTION IF EXISTS definition_version_is_sealed()")
    op.drop_column("workflow_case", "definition_version_id")
    op.drop_index("uq_definition_one_draft", table_name="definition_version")
    op.drop_index("ix_definition_version_definition_id", table_name="definition_version")
    op.drop_table("definition_version")
    op.drop_table("definition")
