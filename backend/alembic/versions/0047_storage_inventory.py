"""Inventarios observacionales de almacenamiento, sin backfill ni modificación de S3."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from app.settings import settings

revision = "0047"
down_revision = "0046"
branch_labels = depends_on = None


def upgrade():
    op.create_table("storage_scans",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("buckets", pg.JSONB(), nullable=False),
        sa.Column("location_hash", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("phase", sa.String(16), nullable=False, server_default="versions"),
        sa.Column("bucket_slot", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cursor", pg.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_by", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("reason", sa.String(2000), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("invalidated_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("tenant_id", "id", name="uq_storage_scan_tenant"),
        sa.UniqueConstraint("job_id", name="uq_storage_scan_job"),
        sa.ForeignKeyConstraint(["tenant_id", "job_id"], ["jobs.tenant_id", "jobs.id"], ondelete="CASCADE"),
        sa.CheckConstraint("phase IN ('versions','uploads','parts','completed')", name="phase"),
        sa.CheckConstraint("bucket_slot BETWEEN 0 AND 1", name="bucket_slot"))
    op.create_table("storage_inventory_items",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("scan_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("bucket_slot", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("category", sa.String(24), nullable=False),
        sa.Column("storage_key", sa.String(1024), nullable=False),
        sa.Column("version_id", sa.String(1024)),
        sa.Column("upload_id", sa.String(2048)),
        sa.Column("part_number", sa.Integer()),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("is_latest", sa.Boolean()),
        sa.Column("upload_state", sa.String(16)),
        sa.Column("parts_cursor", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id", "scan_id"], ["storage_scans.tenant_id", "storage_scans.id"], ondelete="CASCADE"),
        sa.CheckConstraint("byte_size>=0 AND bucket_slot BETWEEN 0 AND 1 AND parts_cursor BETWEEN 0 AND 10000", name="bounds"),
        sa.CheckConstraint("category IN ('documents','exports','staging','media','ai_responses','other')", name="category"),
        sa.CheckConstraint("(kind IN ('version','delete_marker') AND version_id IS NOT NULL AND is_latest IS NOT NULL AND upload_id IS NULL AND part_number IS NULL AND upload_state IS NULL) OR "
                           "(kind='upload' AND upload_id IS NOT NULL AND upload_state IS NOT NULL AND upload_state IN ('pending','listed','unavailable') AND version_id IS NULL AND is_latest IS NULL AND part_number IS NULL) OR "
                           "(kind='part' AND upload_id IS NOT NULL AND part_number IS NOT NULL AND part_number BETWEEN 1 AND 10000 AND version_id IS NULL AND is_latest IS NULL AND upload_state IS NULL)", name="identity"),
        sa.CheckConstraint("kind NOT IN ('upload','delete_marker') OR byte_size=0", name="non_payload"))
    op.create_index("ix_storage_scans_tenant_id", "storage_scans", ["tenant_id"])
    for column in ("tenant_id", "scan_id"):
        op.create_index(f"ix_storage_inventory_items_{column}", "storage_inventory_items", [column])
    op.create_index("ix_storage_inventory_pending", "storage_inventory_items", ["scan_id", "id"],
                    postgresql_where=sa.text("kind='upload' AND upload_state='pending'"))
    app_role = '"' + settings.app_db_user.replace('"', '""') + '"'
    for table in ("storage_scans", "storage_inventory_items"):
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {app_role}")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {table}_isolation ON {table} USING (tenant_id=app_current_tenant()) WITH CHECK (tenant_id=app_current_tenant())")


def downgrade():
    op.drop_table("storage_inventory_items")
    op.drop_table("storage_scans")
