"""Record numbering + cross-page spans, page folio label

Revision ID: 0020
Revises: 0019
Create Date: 2026-06-21

Lets a single register entry span two sheets (page_end_id/transcription_end_id + is_continued) so it
is stored as ONE consistent record, and captures the page's own folio number and the entry number.
The CHECK guarantees a record flagged is_continued always carries its second sheet.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("pages", sa.Column("folio_label", sa.String(32), nullable=True))

    op.add_column("records", sa.Column("page_end_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("records", sa.Column("transcription_end_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column(
        "records",
        sa.Column("is_continued", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column("records", sa.Column("record_no", sa.String(32), nullable=True))
    op.add_column("records", sa.Column("record_seq", sa.Integer(), nullable=True))

    op.create_foreign_key(
        "fk_records_page_end", "records", "pages", ["page_end_id"], ["id"], ondelete="SET NULL"
    )
    op.create_foreign_key(
        "fk_records_transcription_end", "records", "transcriptions",
        ["transcription_end_id"], ["id"], ondelete="SET NULL",
    )
    # Raw SQL (not op.create_check_constraint) to keep the exact name — the alembic naming
    # convention would otherwise prefix it to ck_records_ck_...
    op.execute(
        "ALTER TABLE records ADD CONSTRAINT ck_records_continued_has_end "
        "CHECK (NOT is_continued OR page_end_id IS NOT NULL)"
    )
    op.create_index("ix_records_document_seq", "records", ["document_id", "record_seq"])


def downgrade() -> None:
    op.drop_index("ix_records_document_seq", table_name="records")
    op.execute("ALTER TABLE records DROP CONSTRAINT IF EXISTS ck_records_continued_has_end")
    op.drop_constraint("fk_records_transcription_end", "records", type_="foreignkey")
    op.drop_constraint("fk_records_page_end", "records", type_="foreignkey")
    op.drop_column("records", "record_seq")
    op.drop_column("records", "record_no")
    op.drop_column("records", "is_continued")
    op.drop_column("records", "transcription_end_id")
    op.drop_column("records", "page_end_id")
    op.drop_column("pages", "folio_label")
