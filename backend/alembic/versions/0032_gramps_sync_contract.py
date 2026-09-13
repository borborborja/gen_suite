"""durable Gramps Desktop/Web synchronization contract

Revision ID: 0032
Revises: 0031
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.settings import settings


revision: str = "0032"
down_revision: Union[str, None] = "0031"
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
    op.create_table(
        "interop_connections",
        _id(), sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False), sa.Column("name", sa.String(128), nullable=False),
        sa.Column("endpoint", sa.String(1024)), sa.Column("status", sa.String(16), server_default=sa.text("'active'"), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True)), sa.Column("last_pulled_at", sa.DateTime(timezone=True)),
        sa.Column("last_pushed_at", sa.DateTime(timezone=True)), *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_interop_connections"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint("provider IN ('gramps_desktop','gramps_web')", name="ck_interop_connections_provider"),
        sa.CheckConstraint("status IN ('active','paused')", name="ck_interop_connections_status"),
    )
    op.create_index("ix_interop_connections_tenant_id", "interop_connections", ["tenant_id"])
    op.create_table(
        "sync_baselines",
        _id(), sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("connection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("snapshot_hash", sa.String(128), nullable=False), sa.Column("object_counts", postgresql.JSONB(), nullable=False),
        sa.Column("note", sa.Text()), sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("established_by", postgresql.UUID(as_uuid=True)), *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_sync_baselines"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["connection_id"], ["interop_connections.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["established_by"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_sync_baselines_tenant_id", "sync_baselines", ["tenant_id"])
    op.create_index("ix_sync_baselines_connection_id", "sync_baselines", ["connection_id"])
    op.create_table(
        "sync_changes",
        _id(), sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("connection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_system", sa.String(32), nullable=False), sa.Column("object_type", sa.String(32), nullable=False),
        sa.Column("object_key", sa.String(255), nullable=False), sa.Column("operation", sa.String(16), nullable=False),
        sa.Column("remote_revision", sa.String(128)), sa.Column("payload", postgresql.JSONB()),
        sa.Column("state", sa.String(16), server_default=sa.text("'queued'"), nullable=False),
        sa.Column("conflict_with_id", postgresql.UUID(as_uuid=True)), sa.Column("resolution_note", sa.Text()),
        sa.Column("resolved_by", postgresql.UUID(as_uuid=True)), sa.Column("resolved_at", sa.DateTime(timezone=True)),
        *_timestamps(), sa.PrimaryKeyConstraint("id", name="pk_sync_changes"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["connection_id"], ["interop_connections.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conflict_with_id"], ["sync_changes.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["resolved_by"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint("operation IN ('upsert','tombstone')", name="ck_sync_changes_operation"),
        sa.CheckConstraint("state IN ('queued','conflict','applied','rejected','tombstone')", name="ck_sync_changes_state"),
    )
    op.create_index("ix_sync_changes_tenant_id", "sync_changes", ["tenant_id"])
    op.create_index("ix_sync_changes_connection_id", "sync_changes", ["connection_id"])
    op.create_index("ix_sync_changes_object_key", "sync_changes", ["object_key"])

    for table in ("interop_connections", "sync_baselines", "sync_changes"):
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {settings.app_db_user};")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(
            f"CREATE POLICY {table}_isolation ON {table} "
            "USING (tenant_id = app_current_tenant()) "
            "WITH CHECK (tenant_id = app_current_tenant());"
        )


def downgrade() -> None:
    for table in ("sync_changes", "sync_baselines", "interop_connections"):
        op.execute(f"DROP POLICY IF EXISTS {table}_isolation ON {table};")
        op.drop_table(table)
