"""Cancelación auditada de reservas no iniciadas; no atribuye guardas a llamadas antiguas."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from app.settings import settings

revision = "0049"
down_revision = "0048"
branch_labels = depends_on = None


def upgrade():
    op.add_column("storage_writes", sa.Column("ai_guard_version", sa.SmallInteger()))
    op.create_table("storage_write_actions",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("write_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("released_bytes", sa.BigInteger(), nullable=False),
        sa.Column("evidence", pg.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id", "write_id"], ["storage_writes.tenant_id", "storage_writes.id"]),
        sa.CheckConstraint("released_bytes>=0", name="released_bytes"))
    op.create_index("ix_storage_write_actions_tenant_id", "storage_write_actions", ["tenant_id"])
    role = '"' + settings.app_db_user.replace('"', '""') + '"'
    op.execute(f"GRANT SELECT, INSERT ON storage_write_actions TO {role}")
    op.execute("ALTER TABLE storage_write_actions ENABLE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY storage_write_actions_isolation ON storage_write_actions USING (tenant_id=app_current_tenant()) WITH CHECK (tenant_id=app_current_tenant())")


def downgrade():
    op.drop_table("storage_write_actions")
    op.drop_column("storage_writes", "ai_guard_version")
