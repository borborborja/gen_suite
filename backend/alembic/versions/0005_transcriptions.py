"""transcriptions (text per page; public-read RLS exception)

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-16
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.settings import settings

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "transcriptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("page_id", postgresql.UUID(as_uuid=True)),
        sa.Column("page_no", sa.Integer(), nullable=False),
        sa.Column("visibility", sa.String(16), server_default=sa.text("'private'"), nullable=False),
        sa.Column("engine", sa.String(32), nullable=False),
        sa.Column("model", sa.String(128)),
        sa.Column("text", sa.Text()),
        sa.Column("confidence", sa.Float()),
        sa.Column("status", sa.String(16), server_default=sa.text("'ok'"), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_transcriptions"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_transcriptions_tenant_id_tenants", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], name="fk_transcriptions_document_id_documents", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["page_id"], ["pages.id"], name="fk_transcriptions_page_id_pages", ondelete="SET NULL"),
    )
    op.create_index("ix_transcriptions_tenant_id", "transcriptions", ["tenant_id"])
    op.create_index("ix_transcriptions_document_id", "transcriptions", ["document_id"])

    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON transcriptions TO {settings.app_db_user};")
    op.execute("ALTER TABLE transcriptions ENABLE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY transcriptions_isolation ON transcriptions "
        "USING (tenant_id = app_current_tenant() OR visibility = 'public') "
        "WITH CHECK (tenant_id = app_current_tenant());"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS transcriptions_isolation ON transcriptions;")
    op.drop_table("transcriptions")
