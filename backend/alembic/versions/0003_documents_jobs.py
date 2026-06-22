"""documents + pages (public-read RLS exception) and the unified jobs table

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-15

documents/pages are tenant-scoped but PUBLIC rows are readable cross-tenant — this is how a
town's shared register is transcribed/searched once and cited by every tenant, while writes
stay confined to the owner. jobs are plain tenant-scoped.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.settings import settings

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _uuid_pk() -> sa.Column:
    return sa.Column(
        "id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "documents",
        _uuid_pk(),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("doc_type", sa.String(32), nullable=False),
        sa.Column("visibility", sa.String(16), server_default=sa.text("'private'"), nullable=False),
        sa.Column("rights_declaration", sa.String(32)),
        sa.Column("rights_declared_by", postgresql.UUID(as_uuid=True)),
        sa.Column("rights_declared_at", sa.DateTime(timezone=True)),
        sa.Column("rights_declared_ip", sa.String(64)),
        sa.Column("source_kind", sa.String(32), server_default=sa.text("'upload'"), nullable=False),
        sa.Column("storage_bucket", sa.String(64), nullable=False),
        sa.Column("storage_prefix", sa.String(256), nullable=False),
        sa.Column("page_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("fingerprint", sa.String(64)),
        sa.Column("place_id", postgresql.UUID(as_uuid=True)),
        sa.Column("year_from", sa.Integer()),
        sa.Column("year_to", sa.Integer()),
        sa.Column("created_by", postgresql.UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_documents"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_documents_tenant_id_tenants", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["place_id"], ["places.id"], name="fk_documents_place_id_places", ondelete="SET NULL"),
        sa.CheckConstraint("visibility IN ('private','public')", name="ck_documents_visibility"),
        sa.CheckConstraint("doc_type IN ('image_set','pdf','other')", name="ck_documents_doc_type"),
        sa.CheckConstraint(
            "source_kind IN ('upload','familysearch','transcription_output')",
            name="ck_documents_source_kind",
        ),
    )
    op.create_index("ix_documents_tenant_id", "documents", ["tenant_id"])
    op.create_index("ix_documents_fingerprint", "documents", ["fingerprint"])
    op.create_index("ix_documents_visibility", "documents", ["visibility"])

    op.create_table(
        "pages",
        _uuid_pk(),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("visibility", sa.String(16), server_default=sa.text("'private'"), nullable=False),
        sa.Column("page_no", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(512), nullable=False),
        sa.Column("content_type", sa.String(64)),
        sa.Column("byte_size", sa.Integer()),
        sa.Column("width", sa.Integer()),
        sa.Column("height", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_pages"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_pages_tenant_id_tenants", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], name="fk_pages_document_id_documents", ondelete="CASCADE"),
        sa.UniqueConstraint("document_id", "page_no", name="uq_pages_document_page"),
        sa.CheckConstraint("visibility IN ('private','public')", name="ck_pages_visibility"),
    )
    op.create_index("ix_pages_tenant_id", "pages", ["tenant_id"])
    op.create_index("ix_pages_document_id", "pages", ["document_id"])

    op.create_table(
        "jobs",
        _uuid_pk(),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), server_default=sa.text("'queued'"), nullable=False),
        sa.Column("params", postgresql.JSONB()),
        sa.Column("progress", postgresql.JSONB()),
        sa.Column("result", postgresql.JSONB()),
        sa.Column("error", sa.Text()),
        sa.Column("created_by", postgresql.UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id", name="pk_jobs"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_jobs_tenant_id_tenants", ondelete="CASCADE"),
        sa.CheckConstraint(
            "status IN ('queued','running','paused','completed','cancelled','error')",
            name="ck_jobs_status",
        ),
    )
    op.create_index("ix_jobs_tenant_id", "jobs", ["tenant_id"])

    app_user = settings.app_db_user
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON documents, pages, jobs TO {app_user};")

    # documents + pages: tenant rows OR public rows are readable; writes only own-tenant.
    for table in ("documents", "pages"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(
            f"CREATE POLICY {table}_isolation ON {table} "
            f"USING (tenant_id = app_current_tenant() OR visibility = 'public') "
            f"WITH CHECK (tenant_id = app_current_tenant());"
        )

    op.execute("ALTER TABLE jobs ENABLE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY jobs_isolation ON jobs "
        "USING (tenant_id = app_current_tenant()) "
        "WITH CHECK (tenant_id = app_current_tenant());"
    )


def downgrade() -> None:
    for table in ("jobs", "pages", "documents"):
        op.execute(f"DROP POLICY IF EXISTS {table}_isolation ON {table};")
    op.drop_table("jobs")
    op.drop_table("pages")
    op.drop_table("documents")
