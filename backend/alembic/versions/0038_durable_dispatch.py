"""Cola transaccional; no modifica ni reprocesa datos existentes.

Revision ID: 0038
Revises: 0037
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from app.settings import settings

revision = "0038"
down_revision = "0037"
branch_labels = depends_on = None


def upgrade():
    op.create_table("task_dispatches",
        sa.Column("job_id", pg.UUID(as_uuid=True), sa.ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("function", sa.String(64), nullable=False),
        sa.Column("payload", pg.JSONB(), nullable=False),
        sa.Column("status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.Text()),
    )
    op.create_index("ix_task_dispatches_tenant_id", "task_dispatches", ["tenant_id"])
    op.create_index("ix_task_dispatches_pending", "task_dispatches", ["available_at"], postgresql_where=sa.text("status = 'pending'"))
    op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON task_dispatches TO "{settings.app_db_user}"')
    op.execute("ALTER TABLE task_dispatches ENABLE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY task_dispatches_isolation ON task_dispatches USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant())")


def downgrade():
    op.drop_table("task_dispatches")
