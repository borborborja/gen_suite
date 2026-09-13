"""Reservas S3 y línea base protegida, sin backfill ni recuento automático de datos antiguos."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from app.settings import settings

revision = "0048"
down_revision = "0047"
branch_labels = depends_on = None


def upgrade():
    op.create_table("storage_quotas",
        sa.Column("tenant_id", pg.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("phase", sa.String(16), nullable=False, server_default="uninitialized"),
        sa.Column("used_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("reserved_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("location_hash", sa.String(64)),
        sa.Column("scan_id", pg.UUID(as_uuid=True)),
        sa.Column("baseline_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id", "scan_id"], ["storage_scans.tenant_id", "storage_scans.id"]),
        sa.CheckConstraint("phase IN ('uninitialized','ready','draining','counting')", name="phase"),
        sa.CheckConstraint("used_bytes>=0 AND reserved_bytes>=0", name="amounts"))
    op.create_table("storage_writes",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), sa.ForeignKey("storage_quotas.tenant_id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", pg.UUID(as_uuid=True)),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("bucket", sa.String(255), nullable=False),
        sa.Column("location_hash", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.String(1024), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("reserved_bytes", sa.BigInteger(), nullable=False),
        sa.Column("actual_bytes", sa.BigInteger()),
        sa.Column("target_version_id", sa.String(1024)),
        sa.Column("version_id", sa.String(1024)),
        sa.Column("upload_id", sa.String(2048)),
        sa.Column("error", sa.Text()),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("tenant_id", "id", name="uq_storage_write_tenant"),
        sa.CheckConstraint("kind IN ('put','upload','copy','delete','delete_version')", name="kind"),
        sa.CheckConstraint("state IN ('reserved','sending','succeeded','unknown','cancelled','quarantined')", name="state"),
        sa.CheckConstraint("reserved_bytes>=0 AND (actual_bytes IS NULL OR actual_bytes>=0)", name="amounts"))
    op.create_index("ix_storage_writes_tenant_id", "storage_writes", ["tenant_id"])
    op.create_index("uq_storage_pending_key", "storage_writes", ["tenant_id", "location_hash", "bucket", "key_hash"], unique=True,
        postgresql_where=sa.text("state IN ('reserved','sending','unknown')"))
    op.add_column("ai_calls", sa.Column("response_write_id", pg.UUID(as_uuid=True)))
    op.create_foreign_key("fk_ai_call_response_write", "ai_calls", "storage_writes", ["tenant_id", "response_write_id"], ["tenant_id", "id"])
    app_role = '"' + settings.app_db_user.replace('"', '""') + '"'
    for table in ("storage_quotas", "storage_writes"):
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {app_role}")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {table}_isolation ON {table} USING (tenant_id=app_current_tenant()) WITH CHECK (tenant_id=app_current_tenant())")


def downgrade():
    op.drop_constraint("fk_ai_call_response_write", "ai_calls", type_="foreignkey")
    op.drop_column("ai_calls", "response_write_id")
    op.drop_table("storage_writes")
    op.drop_table("storage_quotas")
