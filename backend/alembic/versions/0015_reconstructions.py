"""reconstructions: proposed family-tree reconstructions from the corpus (super-discovery)

Stored as JSON (graph) so the real tree is untouched until the user merges the proposal. Tenant-
scoped with the standard RLS policy.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.settings import settings

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "reconstructions",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True)),
        sa.Column("status", sa.String(16), server_default=sa.text("'running'"), nullable=False),
        sa.Column("conservative", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("include_census", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("link_to_tree", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("graph", postgresql.JSONB()),
        sa.Column("stats", postgresql.JSONB()),
        sa.Column("merged_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_reconstructions"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_reconstructions_tenant_id_tenants", ondelete="CASCADE"),
    )
    op.create_index("ix_reconstructions_tenant_id", "reconstructions", ["tenant_id"])
    op.create_index("ix_reconstructions_job_id", "reconstructions", ["job_id"])
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON reconstructions TO {settings.app_db_user};")
    op.execute("ALTER TABLE reconstructions ENABLE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY reconstructions_isolation ON reconstructions "
        "USING (tenant_id = app_current_tenant()) "
        "WITH CHECK (tenant_id = app_current_tenant());"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS reconstructions_isolation ON reconstructions;")
    op.drop_table("reconstructions")
