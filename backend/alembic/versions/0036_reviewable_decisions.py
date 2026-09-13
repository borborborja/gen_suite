"""reviewable linkage decisions and heuristic-score disclosure

Revision ID: 0036
Revises: 0035
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.settings import settings


revision: str = "0036"
down_revision: Union[str, None] = "0035"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tenant_policy(table: str) -> None:
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {settings.app_db_user};")
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
    op.execute(
        f"CREATE POLICY {table}_isolation ON {table} "
        "USING (tenant_id = app_current_tenant()) "
        "WITH CHECK (tenant_id = app_current_tenant());"
    )


def upgrade() -> None:
    op.add_column("match_candidates", sa.Column(
        "score_kind", sa.String(24), server_default=sa.text("'heuristic'"), nullable=False
    ))
    op.add_column("match_candidates", sa.Column(
        "score_version", sa.String(24), server_default=sa.text("'v1'"), nullable=False
    ))
    op.create_table(
        "mutation_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("response", postgresql.JSONB(), nullable=False),
        sa.Column("change_id", postgresql.UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_mutation_requests"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["change_id"], ["change_log.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_mutation_requests_tenant_key"),
    )
    op.create_index("ix_mutation_requests_tenant_id", "mutation_requests", ["tenant_id"])
    _tenant_policy("mutation_requests")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS mutation_requests_isolation ON mutation_requests;")
    op.drop_index("ix_mutation_requests_tenant_id", table_name="mutation_requests")
    op.drop_table("mutation_requests")
    op.drop_column("match_candidates", "score_version")
    op.drop_column("match_candidates", "score_kind")
