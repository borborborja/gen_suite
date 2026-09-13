"""research foundation: sources, evidence assertions, notes, tags and projects

Revision ID: 0029
Revises: 0028
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.settings import settings

revision: str = "0029"
down_revision: Union[str, None] = "0028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _uuid_pk() -> sa.Column:
    return sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False)


def _tenant() -> sa.Column:
    return sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False)


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "repositories", _uuid_pk(), _tenant(),
        sa.Column("name", sa.String(255), nullable=False), sa.Column("url", sa.String(1024)),
        sa.Column("address", sa.Text()), sa.Column("notes", sa.Text()), *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_repositories"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_repositories_tenant_id", "repositories", ["tenant_id"])
    op.create_table(
        "research_sources", _uuid_pk(), _tenant(),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("source_type", sa.String(32), server_default=sa.text("'document'"), nullable=False),
        sa.Column("author", sa.String(255)), sa.Column("publication_info", sa.Text()),
        sa.Column("call_number", sa.String(255)), sa.Column("url", sa.String(1024)),
        sa.Column("repository_id", postgresql.UUID(as_uuid=True)),
        sa.Column("primary_document_id", postgresql.UUID(as_uuid=True)), sa.Column("raw", postgresql.JSONB()),
        *_timestamps(), sa.PrimaryKeyConstraint("id", name="pk_research_sources"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["repository_id"], ["repositories.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["primary_document_id"], ["documents.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_research_sources_tenant_id", "research_sources", ["tenant_id"])
    op.create_index("ix_research_sources_repository_id", "research_sources", ["repository_id"])
    op.create_index("ix_research_sources_primary_document_id", "research_sources", ["primary_document_id"])
    op.create_table(
        "research_notes", _uuid_pk(), _tenant(), sa.Column("subject_type", sa.String(32), nullable=False),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=False), sa.Column("text", sa.Text(), nullable=False),
        sa.Column("format", sa.String(16), server_default=sa.text("'plain'"), nullable=False), *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_research_notes"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_research_notes_tenant_id", "research_notes", ["tenant_id"])
    op.create_index("ix_research_notes_subject_id", "research_notes", ["subject_id"])
    op.create_table(
        "research_tags", _uuid_pk(), _tenant(), sa.Column("name", sa.String(64), nullable=False),
        sa.Column("color", sa.String(16)), *_timestamps(), sa.PrimaryKeyConstraint("id", name="pk_research_tags"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_research_tags_tenant_name"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_research_tags_tenant_id", "research_tags", ["tenant_id"])
    op.create_table(
        "research_object_tags", _uuid_pk(), _tenant(), sa.Column("tag_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_type", sa.String(32), nullable=False), sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_research_object_tags"),
        sa.UniqueConstraint("tag_id", "target_type", "target_id", name="uq_research_object_tags_target"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tag_id"], ["research_tags.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_research_object_tags_tenant_id", "research_object_tags", ["tenant_id"])
    op.create_index("ix_research_object_tags_tag_id", "research_object_tags", ["tag_id"])
    op.create_index("ix_research_object_tags_target_id", "research_object_tags", ["target_id"])
    op.create_table(
        "research_projects", _uuid_pk(), _tenant(), sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text()), sa.Column("status", sa.String(16), server_default=sa.text("'active'"), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True)), *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_research_projects"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_research_projects_tenant_id", "research_projects", ["tenant_id"])
    op.create_table(
        "research_tasks", _uuid_pk(), _tenant(), sa.Column("project_id", postgresql.UUID(as_uuid=True)),
        sa.Column("title", sa.String(255), nullable=False), sa.Column("description", sa.Text()),
        sa.Column("status", sa.String(16), server_default=sa.text("'open'"), nullable=False),
        sa.Column("priority", sa.String(16), server_default=sa.text("'medium'"), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True)), sa.Column("assignee_user_id", postgresql.UUID(as_uuid=True)),
        sa.Column("target_type", sa.String(32)), sa.Column("target_id", postgresql.UUID(as_uuid=True)),
        sa.Column("created_by", postgresql.UUID(as_uuid=True)), *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_research_tasks"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["research_projects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["assignee_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_research_tasks_tenant_id", "research_tasks", ["tenant_id"])
    op.create_index("ix_research_tasks_project_id", "research_tasks", ["project_id"])
    op.create_index("ix_research_tasks_assignee_user_id", "research_tasks", ["assignee_user_id"])
    op.create_table(
        "research_assertions", _uuid_pk(), _tenant(), sa.Column("subject_type", sa.String(32), nullable=False),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=False), sa.Column("predicate", sa.String(64), nullable=False),
        sa.Column("value", postgresql.JSONB(), nullable=False),
        sa.Column("state", sa.String(16), server_default=sa.text("'proposed'"), nullable=False),
        sa.Column("confidence", sa.Integer()), sa.Column("created_by", postgresql.UUID(as_uuid=True)), *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_research_assertions"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_research_assertions_tenant_id", "research_assertions", ["tenant_id"])
    op.create_index("ix_research_assertions_subject_id", "research_assertions", ["subject_id"])

    op.add_column("citations", sa.Column("source_id", postgresql.UUID(as_uuid=True)))
    op.add_column("citations", sa.Column("assertion_id", postgresql.UUID(as_uuid=True)))
    op.add_column("citations", sa.Column("locator", postgresql.JSONB()))
    op.add_column("citations", sa.Column("quote", sa.Text()))
    op.add_column("citations", sa.Column("quality", sa.String(16)))
    op.create_foreign_key("fk_citations_source_id_research_sources", "citations", "research_sources", ["source_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_citations_assertion_id_research_assertions", "citations", "research_assertions", ["assertion_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_citations_source_id", "citations", ["source_id"])
    op.create_index("ix_citations_assertion_id", "citations", ["assertion_id"])

    # Every existing local Document receives a stable Source record; legacy citations retain all
    # their original page/record links and gain the matching source where one can be resolved.
    op.execute(
        "INSERT INTO research_sources (tenant_id, title, source_type, primary_document_id, created_at, updated_at) "
        "SELECT tenant_id, title, 'document', id, created_at, updated_at FROM documents"
    )
    op.execute(
        "WITH citation_documents AS ("
        "  SELECT c.id, COALESCE(r.document_id, t.document_id, p.document_id) AS document_id "
        "  FROM citations c "
        "  LEFT JOIN records r ON r.id = c.record_id "
        "  LEFT JOIN transcriptions t ON t.id = c.transcription_id "
        "  LEFT JOIN pages p ON p.id = c.page_id"
        ") "
        "UPDATE citations c SET source_id = s.id "
        "FROM citation_documents d JOIN research_sources s ON s.primary_document_id = d.document_id "
        "WHERE c.id = d.id AND c.source_id IS NULL"
    )

    tables = ("repositories", "research_sources", "research_notes", "research_tags", "research_object_tags", "research_projects", "research_tasks", "research_assertions")
    for table in tables:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {settings.app_db_user};")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(
            f"CREATE POLICY {table}_isolation ON {table} "
            "USING (tenant_id = app_current_tenant()) "
            "WITH CHECK (tenant_id = app_current_tenant());"
        )


def downgrade() -> None:
    op.drop_index("ix_citations_assertion_id", table_name="citations")
    op.drop_index("ix_citations_source_id", table_name="citations")
    op.drop_constraint("fk_citations_assertion_id_research_assertions", "citations", type_="foreignkey")
    op.drop_constraint("fk_citations_source_id_research_sources", "citations", type_="foreignkey")
    for column in ("quality", "quote", "locator", "assertion_id", "source_id"):
        op.drop_column("citations", column)
    tables = ("research_assertions", "research_tasks", "research_projects", "research_object_tags", "research_tags", "research_notes", "research_sources", "repositories")
    for table in tables:
        op.execute(f"DROP POLICY IF EXISTS {table}_isolation ON {table};")
        op.drop_table(table)
