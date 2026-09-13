"""canonical sync objects and local projections

Revision ID: 0034
Revises: 0033
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.settings import settings


revision: str = "0034"
down_revision: Union[str, None] = "0033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sync_objects",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("connection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("object_type", sa.String(32), nullable=False), sa.Column("object_key", sa.String(255), nullable=False),
        sa.Column("payload", postgresql.JSONB()), sa.Column("local_type", sa.String(32)),
        sa.Column("local_id", postgresql.UUID(as_uuid=True)), sa.Column("remote_revision", sa.String(128)),
        sa.Column("is_tombstone", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_sync_objects"),
        sa.UniqueConstraint("connection_id", "object_type", "object_key", name="uq_sync_objects_connection_key"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["connection_id"], ["interop_connections.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_sync_objects_tenant_id", "sync_objects", ["tenant_id"])
    op.create_index("ix_sync_objects_connection_id", "sync_objects", ["connection_id"])
    op.create_index("ix_sync_objects_local_id", "sync_objects", ["local_id"])
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON sync_objects TO {settings.app_db_user};")
    op.execute("ALTER TABLE sync_objects ENABLE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY sync_objects_isolation ON sync_objects "
        "USING (tenant_id = app_current_tenant()) "
        "WITH CHECK (tenant_id = app_current_tenant());"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS sync_objects_isolation ON sync_objects;")
    op.drop_table("sync_objects")
