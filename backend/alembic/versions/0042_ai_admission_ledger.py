"""Reservas de IA duraderas; no modifica ni recalcula consumos históricos."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from app.settings import settings

revision = "0042"
down_revision = "0041"
branch_labels = depends_on = None


def upgrade():
    op.create_table("ai_calls",
        sa.Column("id", pg.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", pg.UUID(as_uuid=True)),
        sa.Column("task_type", sa.String(24), nullable=False),
        sa.Column("operation_key", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("profile", pg.JSONB(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("reserved_microusd", sa.BigInteger(), nullable=False),
        sa.Column("cost_microusd", sa.BigInteger()),
        sa.Column("usage", pg.JSONB()),
        sa.Column("provider_request_id", sa.String(256)),
        sa.Column("result_key", sa.Text()),
        sa.Column("result_checksum", sa.String(64)),
        sa.Column("error_code", sa.String(64)),
        sa.Column("settled_at", sa.DateTime(timezone=True)),
        sa.Column("reconciliation", pg.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("tenant_id", "operation_key", name="uq_ai_call_operation"),
        sa.CheckConstraint("reserved_microusd >= 0 AND (cost_microusd IS NULL OR cost_microusd >= 0)", name="amounts"),
        sa.CheckConstraint("state IN ('inflight', 'succeeded', 'unknown', 'reconciled')", name="state"),
    )
    op.create_index("ix_ai_calls_job_task", "ai_calls", ["tenant_id", "job_id", "task_type"])
    op.create_index("ix_ai_calls_accounting", "ai_calls", ["tenant_id", "state", "created_at"])
    op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ai_calls TO "{settings.app_db_user}"')
    op.execute("ALTER TABLE ai_calls ENABLE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY ai_calls_isolation ON ai_calls USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant())")


def downgrade():
    op.drop_table("ai_calls")
