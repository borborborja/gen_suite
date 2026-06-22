"""AI spending control: per-tenant monthly budget + usage_events

Revision ID: 0022
Revises: 0021
Create Date: 2026-06-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.settings import settings

revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("monthly_budget_cents", sa.Integer(), nullable=True))

    op.create_table(
        "usage_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("task_type", sa.String(24), nullable=False),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("cost_cents", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_usage_events"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_usage_events_tenant_created", "usage_events", ["tenant_id", "created_at"])

    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON usage_events TO {settings.app_db_user};")
    op.execute("ALTER TABLE usage_events ENABLE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY usage_events_isolation ON usage_events "
        "USING (tenant_id = app_current_tenant()) "
        "WITH CHECK (tenant_id = app_current_tenant());"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS usage_events_isolation ON usage_events;")
    op.drop_index("ix_usage_events_tenant_created", table_name="usage_events")
    op.drop_table("usage_events")
    op.drop_column("tenants", "monthly_budget_cents")
