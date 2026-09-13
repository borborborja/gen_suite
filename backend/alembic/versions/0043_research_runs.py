"""Investigaciones reanudables y presupuesto por ejecución; solo adiciones, sin backfill."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from app.settings import settings

revision = "0043"
down_revision = "0042"
branch_labels = depends_on = None


def _base():
    return [sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)]


def upgrade():
    op.create_table("research_runs", *_base(),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("title", sa.String(255), nullable=False), sa.Column("project_id", pg.UUID(as_uuid=True)),
        sa.Column("created_by", pg.UUID(as_uuid=True), nullable=False), sa.Column("status", sa.String(24), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False), sa.Column("budget_microusd", sa.BigInteger(), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False), sa.Column("config", pg.JSONB(), nullable=False),
        sa.Column("profiles", pg.JSONB(), nullable=False), sa.Column("error", sa.Text()),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_research_run_tenant_id"),
        sa.CheckConstraint("budget_microusd >= 0 AND version >= 1", name="bounds"),
        sa.CheckConstraint("status IN ('planned','running','paused','needs_attention','completed','cancelled')", name="status"),
    )
    op.create_table("research_run_stages", *_base(),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("run_id", pg.UUID(as_uuid=True), nullable=False), sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False), sa.Column("title", sa.String(512), nullable=False),
        sa.Column("document_id", pg.UUID(as_uuid=True)), sa.Column("person_id", pg.UUID(as_uuid=True)),
        sa.Column("job_id", pg.UUID(as_uuid=True)), sa.Column("status", sa.String(24), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False), sa.Column("result", pg.JSONB()), sa.Column("error", sa.Text()),
        sa.ForeignKeyConstraint(["tenant_id", "run_id"], ["research_runs.tenant_id", "research_runs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("run_id", "position", name="uq_research_stage_position"),
        sa.CheckConstraint("position >= 0 AND attempts >= 0", name="bounds"),
        sa.CheckConstraint("status IN ('pending','running','paused','needs_attention','completed','skipped','cancelled')", name="status"),
    )
    op.create_table("research_run_events", *_base(),
        sa.Column("run_id", pg.UUID(as_uuid=True), nullable=False), sa.Column("actor_id", pg.UUID(as_uuid=True)),
        sa.Column("kind", sa.String(32), nullable=False), sa.Column("detail", pg.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id", "run_id"], ["research_runs.tenant_id", "research_runs.id"], ondelete="CASCADE"),
    )
    for table in ("research_runs", "research_run_stages", "research_run_events"):
        op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"])
        if table != "research_runs":
            op.create_index(f"ix_{table}_run_id", table, ["run_id"])
        op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO "{settings.app_db_user}"')
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {table}_isolation ON {table} USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant())")
    op.create_index("ix_research_run_stages_job_id", "research_run_stages", ["job_id"])
    op.add_column("ai_calls", sa.Column("research_run_id", pg.UUID(as_uuid=True)))
    op.create_foreign_key("fk_ai_call_research_run", "ai_calls", "research_runs",
                          ["tenant_id", "research_run_id"], ["tenant_id", "id"])
    op.create_index("ix_ai_calls_research_run_id", "ai_calls", ["research_run_id"])
    op.add_column("task_dispatches", sa.Column("generation", sa.Integer(), server_default="0", nullable=False))


def downgrade():
    op.drop_column("task_dispatches", "generation")
    op.drop_constraint("fk_ai_call_research_run", "ai_calls", type_="foreignkey")
    op.drop_index("ix_ai_calls_research_run_id", table_name="ai_calls")
    op.drop_column("ai_calls", "research_run_id")
    for table in ("research_run_events", "research_run_stages", "research_runs"):
        op.drop_table(table)
