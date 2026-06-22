"""Book series numbering + index documents + page kind

Revision ID: 0021
Revises: 0020
Create Date: 2026-06-21

Parish books are numbered within a series (place + record type); book_number enables gap detection.
A document can be an index (name→folio) rather than a register, optionally indexing another book.
pages.kind marks index/cover/blank pages so record extraction skips them.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("book_number", sa.Integer(), nullable=True))
    op.add_column(
        "documents",
        sa.Column("is_index", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column("documents", sa.Column("indexes_for_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_documents_indexes_for", "documents", "documents",
        ["indexes_for_id"], ["id"], ondelete="SET NULL",
    )
    op.add_column(
        "pages",
        sa.Column("kind", sa.String(16), server_default=sa.text("'record'"), nullable=False),
    )
    op.create_index("ix_documents_series", "documents", ["place_id", "default_record_type", "book_number"])


def downgrade() -> None:
    op.drop_index("ix_documents_series", table_name="documents")
    op.drop_column("pages", "kind")
    op.drop_constraint("fk_documents_indexes_for", "documents", type_="foreignkey")
    op.drop_column("documents", "indexes_for_id")
    op.drop_column("documents", "is_index")
    op.drop_column("documents", "book_number")
