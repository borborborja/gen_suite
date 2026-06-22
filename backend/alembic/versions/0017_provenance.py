"""provenance: documents.source_ref / derived_from_id, pages.source_ref

End-to-end traceability: a document records its external origin (e.g. the FamilySearch book URL) and,
for derived documents (compacted PDF), the parent it came from; a page records the exact image
reference (FS ARK). So any extracted act → page → document → external source can be traced back.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("source_ref", sa.Text()))
    op.add_column("documents", sa.Column("derived_from_id", postgresql.UUID(as_uuid=True)))
    op.create_foreign_key(
        "fk_documents_derived_from_id_documents", "documents", "documents",
        ["derived_from_id"], ["id"], ondelete="SET NULL",
    )
    op.add_column("pages", sa.Column("source_ref", sa.Text()))


def downgrade() -> None:
    op.drop_column("pages", "source_ref")
    op.drop_constraint("fk_documents_derived_from_id_documents", "documents", type_="foreignkey")
    op.drop_column("documents", "derived_from_id")
    op.drop_column("documents", "source_ref")
