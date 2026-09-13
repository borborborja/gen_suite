"""Autorizaciones económicas append-only. No recalcula llamadas ni cambia perfiles existentes."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from app.settings import settings

revision = "0045"
down_revision = "0044"
branch_labels = depends_on = None


def upgrade():
    # id is already globally unique; this adds an explicit tenant-composite FK target.
    op.create_unique_constraint("uq_jobs_tenant_id_id", "jobs", ["tenant_id", "id"])
    op.create_table("ai_billing_authorizations",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("research_run_id", pg.UUID(as_uuid=True)),
        sa.Column("embedding_generation_id", pg.UUID(as_uuid=True)),
        sa.Column("job_id", pg.UUID(as_uuid=True)),
        sa.Column("task_type", sa.String(24), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("base_profile_hash", sa.String(64), nullable=False),
        sa.Column("budget_policy", pg.JSONB(), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("preview_hash", sa.String(64), nullable=False),
        sa.Column("created_by", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("reason", sa.String(2000), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id", "research_run_id"], ["research_runs.tenant_id", "research_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id", "embedding_generation_id"], ["embedding_generations.tenant_id", "embedding_generations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id", "job_id"], ["jobs.tenant_id", "jobs.id"], ondelete="CASCADE"),
        sa.CheckConstraint("num_nonnulls(research_run_id,embedding_generation_id,job_id) = 1", name="one_owner"),
        sa.CheckConstraint("version >= 1 AND length(btrim(reason)) > 0", name="approval"),
        sa.CheckConstraint("task_type IN ('transcription','inference','embedding')", name="task"))
    for column in ("research_run_id", "embedding_generation_id", "job_id"):
        op.create_index(f"uq_ai_authorization_{column}", "ai_billing_authorizations",
            ["tenant_id", column, "task_type", "version"], unique=True, postgresql_where=sa.text(f"{column} IS NOT NULL"))
    op.create_index("ix_ai_billing_authorizations_tenant_id", "ai_billing_authorizations", ["tenant_id"])
    app_role = '"' + settings.app_db_user.replace('"', '""') + '"'
    # Override the schema-wide default UPDATE/DELETE grants: prior approvals cannot be edited by runtime.
    op.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON ai_billing_authorizations FROM {app_role}")
    op.execute(f"GRANT SELECT, INSERT ON ai_billing_authorizations TO {app_role}")
    op.execute("ALTER TABLE ai_billing_authorizations ENABLE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY ai_billing_authorizations_isolation ON ai_billing_authorizations USING (tenant_id=app_current_tenant()) WITH CHECK (tenant_id=app_current_tenant())")


def downgrade():
    op.drop_table("ai_billing_authorizations")
    op.drop_constraint("uq_jobs_tenant_id_id", "jobs", type_="unique")
