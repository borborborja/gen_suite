"""Manifiestos y cargas fragmentadas; aditiva y sin migración de datos existentes."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from app.settings import settings

revision = "0041"
down_revision = "0040"
branch_labels = depends_on = None


def _base():
    return [sa.Column("id", pg.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
            sa.Column("tenant_id", pg.UUID(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)]


def upgrade():
    op.create_table("ingest_sessions", *_base(),
        sa.Column("created_by", pg.UUID(), nullable=False), sa.Column("title", sa.String(512), nullable=False),
        sa.Column("options", pg.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(24), nullable=False, server_default="manifest"),
        sa.Column("expected_files", sa.Integer(), nullable=False), sa.Column("reserved_bytes", sa.BigInteger(), nullable=False),
        sa.Column("declared_files", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("declared_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("received_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("job_id", pg.UUID(), sa.ForeignKey("jobs.id", ondelete="SET NULL")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("id", "tenant_id", name="uq_ingest_session_tenant"),
        sa.CheckConstraint("expected_files > 0 AND reserved_bytes > 0 AND declared_files >= 0 AND declared_files <= expected_files AND declared_bytes >= 0 AND declared_bytes <= reserved_bytes AND received_bytes >= 0 AND received_bytes <= reserved_bytes", name="ck_ingest_session_counts"),
    )
    op.create_table("ingest_books", *_base(),
        sa.Column("session_id", pg.UUID(), nullable=False), sa.Column("group_key", sa.Text(), nullable=False),
        sa.Column("title", sa.String(512), nullable=False), sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("options", pg.JSONB(), nullable=False),
        sa.Column("document_id", pg.UUID(), sa.ForeignKey("documents.id", ondelete="SET NULL")),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"), sa.Column("error", sa.Text()),
        sa.ForeignKeyConstraint(["session_id", "tenant_id"], ["ingest_sessions.id", "ingest_sessions.tenant_id"], ondelete="CASCADE"),
        sa.UniqueConstraint("id", "session_id", "tenant_id", name="uq_ingest_book_tenant"),
        sa.CheckConstraint("kind IN ('pdf', 'image_set')", name="ck_ingest_book_kind"),
    )
    op.create_table("ingest_files", *_base(),
        sa.Column("session_id", pg.UUID(), nullable=False), sa.Column("book_id", pg.UUID(), nullable=False),
        sa.Column("relative_path", sa.Text(), nullable=False), sa.Column("path_hash", sa.String(64), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False), sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("last_modified", sa.BigInteger(), nullable=False),
        sa.Column("received_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("parts", pg.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("checksum", sa.String(64)), sa.Column("content_type", sa.String(64)),
        sa.Column("width", sa.Integer()), sa.Column("height", sa.Integer()), sa.Column("page_count", sa.Integer()),
        sa.Column("storage_key", sa.String(512)), sa.Column("error", sa.Text()),
        sa.ForeignKeyConstraint(["book_id", "session_id", "tenant_id"], ["ingest_books.id", "ingest_books.session_id", "ingest_books.tenant_id"], ondelete="CASCADE"),
        sa.UniqueConstraint("session_id", "path_hash", name="uq_ingest_file_path"),
        sa.UniqueConstraint("book_id", "position", name="uq_ingest_file_position"),
        sa.CheckConstraint("position > 0 AND byte_size > 0 AND received_bytes >= 0 AND received_bytes <= byte_size", name="ck_ingest_file_size"),
    )
    for table in ("ingest_sessions", "ingest_books", "ingest_files"):
        op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"])
        if table != "ingest_sessions":
            op.create_index(f"ix_{table}_session_id", table, ["session_id"])
        op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO "{settings.app_db_user}"')
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {table}_isolation ON {table} USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant())")
    op.create_index("ix_ingest_files_book_id", "ingest_files", ["book_id"])


def downgrade():
    for table in ("ingest_files", "ingest_books", "ingest_sessions"):
        op.drop_table(table)
