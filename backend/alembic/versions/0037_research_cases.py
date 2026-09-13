"""research questions, conclusions and documented searches

Revision ID: 0037
Revises: 0036
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.settings import settings


revision: str = "0037"
down_revision: Union[str, None] = "0036"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("research_projects", sa.Column("question", sa.Text()))
    op.add_column("research_projects", sa.Column("target_person_id", postgresql.UUID(as_uuid=True)))
    op.add_column("research_projects", sa.Column("conclusion", sa.Text()))
    op.create_foreign_key("fk_research_projects_target_person", "research_projects", "persons",
                          ["target_person_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_research_projects_target_person_id", "research_projects", ["target_person_id"])
    op.create_table(
        "research_searches",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False), sa.Column("criteria", sa.Text(), nullable=False),
        sa.Column("result", sa.String(16), nullable=False), sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_research_searches"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["research_projects.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_research_searches_tenant_id", "research_searches", ["tenant_id"])
    op.create_index("ix_research_searches_project_id", "research_searches", ["project_id"])
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON research_searches TO {settings.app_db_user};")
    op.execute("ALTER TABLE research_searches ENABLE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY research_searches_isolation ON research_searches "
        "USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS research_searches_isolation ON research_searches;")
    op.drop_index("ix_research_searches_project_id", table_name="research_searches")
    op.drop_index("ix_research_searches_tenant_id", table_name="research_searches")
    op.drop_table("research_searches")
    op.drop_index("ix_research_projects_target_person_id", table_name="research_projects")
    op.drop_constraint("fk_research_projects_target_person", "research_projects", type_="foreignkey")
    op.drop_column("research_projects", "conclusion")
    op.drop_column("research_projects", "target_person_id")
    op.drop_column("research_projects", "question")
