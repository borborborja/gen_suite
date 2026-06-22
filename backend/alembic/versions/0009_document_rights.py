"""document rights/provenance + data-only (Mode B) image purge

Adds, per document: a source origin, a GDPR "may contain living persons" flag, and an image
policy (retain = keep page images so the source page can be re-shown; data_only = keep only the
extracted facts + citation, no image). Per page: an image_purged flag so the content endpoint can
say "data held, image not available" without losing the page_no a citation points to.

Facts aren't copyrightable, but the source image / a third party's database / living-person data
can be restricted — these columns let a deployment honour that (Mode B). New columns only; the
existing RLS policies on documents/pages already cover them.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("image_policy", sa.String(16), server_default=sa.text("'retain'"), nullable=False),
    )  # retain | data_only
    op.add_column(
        "documents",
        sa.Column("may_contain_living", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column("documents", sa.Column("source_origin", sa.String(64)))  # own_photo/public_archive/familysearch/...
    op.add_column(
        "pages",
        sa.Column("image_purged", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("pages", "image_purged")
    op.drop_column("documents", "source_origin")
    op.drop_column("documents", "may_contain_living")
    op.drop_column("documents", "image_policy")
