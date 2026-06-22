"""Index entries (name→folio) parsed from register indexes

Revision ID: 0023
Revises: 0022
Create Date: 2026-06-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.settings import settings

revision: str = "0023"
down_revision: Union[str, None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "index_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("page_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name_raw", sa.String(256), nullable=True),
        sa.Column("given", sa.String(128), nullable=True),
        sa.Column("surname", sa.String(128), nullable=True),
        sa.Column("norm_surname", sa.String(128), nullable=True),
        sa.Column("folio_label", sa.String(32), nullable=True),
        sa.Column("record_no", sa.String(32), nullable=True),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("record_type", sa.String(24), nullable=True),
        sa.Column("raw_json", postgresql.JSONB(), nullable=True),
        sa.Column("matched", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_index_entries"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["page_id"], ["pages.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_index_entries_tenant", "index_entries", ["tenant_id"])
    op.create_index("ix_index_entries_document", "index_entries", ["document_id"])
    op.create_index("ix_index_entries_norm_surname", "index_entries", ["norm_surname"])

    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON index_entries TO {settings.app_db_user};")
    op.execute("ALTER TABLE index_entries ENABLE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY index_entries_isolation ON index_entries "
        "USING (tenant_id = app_current_tenant()) "
        "WITH CHECK (tenant_id = app_current_tenant());"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS index_entries_isolation ON index_entries;")
    op.drop_table("index_entries")
