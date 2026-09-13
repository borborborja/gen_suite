"""reviewable proposals and redacted unlisted publications

Revision ID: 0031
Revises: 0030
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.settings import settings


revision: str = "0031"
down_revision: Union[str, None] = "0030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _id() -> sa.Column:
    return sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False)


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def upgrade() -> None:
    op.add_column("persons", sa.Column("privacy_status", sa.String(16), server_default=sa.text("'unknown'"), nullable=False))
    op.create_check_constraint(
        "ck_persons_privacy_status", "persons",
        "privacy_status IN ('unknown','living','deceased','public','private')",
    )

    op.create_table(
        "change_proposals",
        _id(), sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_type", sa.String(32)), sa.Column("target_id", postgresql.UUID(as_uuid=True)),
        sa.Column("operation", sa.String(48), nullable=False), sa.Column("patch", postgresql.JSONB(), nullable=False),
        sa.Column("rationale", sa.Text()), sa.Column("status", sa.String(16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("submitted_by", postgresql.UUID(as_uuid=True)), sa.Column("reviewed_by", postgresql.UUID(as_uuid=True)),
        sa.Column("reviewer_note", sa.Text()), sa.Column("reviewed_at", sa.DateTime(timezone=True)), *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_change_proposals"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["submitted_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint("status IN ('pending','accepted','rejected','withdrawn')", name="ck_change_proposals_status"),
    )
    op.create_index("ix_change_proposals_tenant_id", "change_proposals", ["tenant_id"])
    op.create_index("ix_change_proposals_target_id", "change_proposals", ["target_id"])

    op.create_table(
        "publications",
        _id(), sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(255), nullable=False), sa.Column("slug", sa.String(96), nullable=False),
        sa.Column("access_token_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), server_default=sa.text("'draft'"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)), sa.Column("payload", postgresql.JSONB()),
        sa.Column("created_by", postgresql.UUID(as_uuid=True)), sa.Column("published_at", sa.DateTime(timezone=True)),
        *_timestamps(), sa.PrimaryKeyConstraint("id", name="pk_publications"),
        sa.UniqueConstraint("slug", name="uq_publications_slug"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint("status IN ('draft','published','revoked')", name="ck_publications_status"),
    )
    op.create_index("ix_publications_tenant_id", "publications", ["tenant_id"])
    op.create_index("ix_publications_slug", "publications", ["slug"])

    for table in ("change_proposals", "publications"):
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {settings.app_db_user};")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(
            f"CREATE POLICY {table}_tenant ON {table} "
            "USING (tenant_id = app_current_tenant()) "
            "WITH CHECK (tenant_id = app_current_tenant());"
        )
    # A published snapshot contains only redacted payload data.  It can be selected without a
    # tenant GUC solely to serve a secret-link request; the access token is checked in Python.
    op.execute(
        "CREATE POLICY publications_unlisted_read ON publications FOR SELECT "
        "USING (status = 'published' AND (expires_at IS NULL OR expires_at > now()));"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS publications_unlisted_read ON publications;")
    for table in ("publications", "change_proposals"):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant ON {table};")
        op.drop_table(table)
    op.drop_constraint("ck_persons_privacy_status", "persons", type_="check")
    op.drop_column("persons", "privacy_status")
