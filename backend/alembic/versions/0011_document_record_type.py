"""document default_record_type (the kind of book: baptisms/marriages/census/…)

Lets the uploader declare a book's type, municipality (place_id, existing) and year range
(year_from/year_to, existing). Extraction reads these to ground the LLM ("this is a baptism book
from X, 1851–1857"), which constrains inference and reduces hallucination/mislabelling.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("default_record_type", sa.String(32)))


def downgrade() -> None:
    op.drop_column("documents", "default_record_type")
