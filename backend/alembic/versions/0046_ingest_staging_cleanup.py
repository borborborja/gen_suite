"""Limpieza explícita de staging, sin borrar ni migrar originales existentes."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from app.settings import settings

revision = "0046"
down_revision = "0045"
branch_labels = depends_on = None


def upgrade():
    op.create_table("ingest_cleanups",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("phase", sa.String(24), nullable=False),
        sa.Column("original_status", sa.String(24), nullable=False),
        sa.Column("bucket", sa.String(255), nullable=False),
        sa.Column("inventory_epoch", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("verified_after", pg.UUID(as_uuid=True)),
        sa.Column("inventory_cursor", pg.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("retained_reservation_bytes", sa.BigInteger(), nullable=False),
        sa.Column("preview_hash", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("created_by", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("reason", sa.String(2000), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("tenant_id", "id", name="uq_ingest_cleanup_tenant"),
        sa.UniqueConstraint("tenant_id", "session_id", name="uq_ingest_cleanup_session"),
        sa.ForeignKeyConstraint(["session_id", "tenant_id"], ["ingest_sessions.id", "ingest_sessions.tenant_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id", "job_id"], ["jobs.tenant_id", "jobs.id"], ondelete="CASCADE"),
        sa.CheckConstraint("mode IN ('compact','discard')", name="mode"),
        sa.CheckConstraint("phase IN ('verify_originals','inventory','delete','verify_empty','completed')", name="phase"),
        sa.CheckConstraint("inventory_epoch>=1 AND retained_reservation_bytes>=0", name="bounds"))
    op.create_table("ingest_cleanup_objects",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("cleanup_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("inventory_epoch", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(512), nullable=False),
        sa.Column("version_id", sa.String(1024), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("is_delete_marker", sa.Boolean(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["tenant_id", "cleanup_id"], ["ingest_cleanups.tenant_id", "ingest_cleanups.id"], ondelete="CASCADE"),
        sa.CheckConstraint("byte_size>=0 AND inventory_epoch>=1", name="bounds"))
    for column in ("tenant_id", "cleanup_id", "deleted_at"):
        op.create_index(f"ix_ingest_cleanup_objects_{column}", "ingest_cleanup_objects", [column])
    op.create_index("ix_ingest_cleanups_tenant_id", "ingest_cleanups", ["tenant_id"])
    op.create_index("ix_ingest_cleanup_pending", "ingest_cleanup_objects", ["cleanup_id", "inventory_epoch", "id"], postgresql_where=sa.text("deleted_at IS NULL"))
    app_role = '"' + settings.app_db_user.replace('"', '""') + '"'
    for table in ("ingest_cleanups", "ingest_cleanup_objects"):
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {app_role}")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {table}_isolation ON {table} USING (tenant_id=app_current_tenant()) WITH CHECK (tenant_id=app_current_tenant())")


def downgrade():
    op.drop_table("ingest_cleanup_objects")
    op.drop_table("ingest_cleanups")
