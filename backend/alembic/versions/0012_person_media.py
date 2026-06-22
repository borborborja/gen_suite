"""person_media: photos/portraits attached to a person

A genealogy app is expected to let users attach photos to ancestors. Media blobs live in MinIO
(private bucket, streamed through the API); this table holds the metadata + storage key. Tenant-
scoped with the same RLS policy pattern as the rest of the schema.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.settings import settings

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "person_media",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("storage_key", sa.String(512), nullable=False),
        sa.Column("content_type", sa.String(64), nullable=False),
        sa.Column("caption", sa.String(512)),
        sa.Column("is_primary", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_person_media"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_person_media_tenant_id_tenants", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["person_id"], ["persons.id"], name="fk_person_media_person_id_persons", ondelete="CASCADE"),
    )
    op.create_index("ix_person_media_tenant_id", "person_media", ["tenant_id"])
    op.create_index("ix_person_media_person_id", "person_media", ["person_id"])

    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON person_media TO {settings.app_db_user};")
    op.execute("ALTER TABLE person_media ENABLE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY person_media_isolation ON person_media "
        "USING (tenant_id = app_current_tenant()) "
        "WITH CHECK (tenant_id = app_current_tenant());"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS person_media_isolation ON person_media;")
    op.drop_table("person_media")
