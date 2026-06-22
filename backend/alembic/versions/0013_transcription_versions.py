"""transcription versioning: is_active

Re-recognizing a book with another model writes a new transcription per page as a *candidate*
(is_active=false) so the old one keeps serving search/extraction/Visor until the user reconciles
(substitute / mix / manual). Exactly one row per (document, page) is active at a time. Existing rows
default to active.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "transcriptions",
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )
    op.create_index(
        "ix_transcriptions_doc_page_active", "transcriptions",
        ["document_id", "page_no", "is_active"],
    )


def downgrade() -> None:
    op.drop_index("ix_transcriptions_doc_page_active", table_name="transcriptions")
    op.drop_column("transcriptions", "is_active")
