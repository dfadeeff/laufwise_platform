"""add import_job.review and import_job.patients

`review`: copies already in thevea that the source has since moved or cancelled. The import is
append-only, so it skips them and cannot fix them — without their own column they hid inside
`skipped`, which reads as "all in order".

`patients`: ref -> patient name for the failed and forced appointments only, so those can be read
as people (a `DL-…` ref can't be searched for in either calendar). Created, skipped and excluded
appointments are not named.

Backfilled empty so existing rows stay valid without a data migration.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "5e6f7a8b9c0d"
down_revision = "4d5e6f7a8b9c"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "import_job",
        sa.Column("review", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.add_column(
        "import_job",
        sa.Column("patients", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )


def downgrade():
    op.drop_column("import_job", "patients")
    op.drop_column("import_job", "review")
