"""Derivados documentales privados y puntos de control de extracción, sin backfill."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from app.settings import settings

revision = "0040"
down_revision = "0039"
branch_labels = depends_on = None


def _base():
    return [
        sa.Column("id", pg.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def upgrade():
    op.create_table("document_exports", *_base(),
        sa.Column("document_id", pg.UUID(as_uuid=True), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False), sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False), sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("manifest", pg.JSONB(), nullable=False),
    )
    op.create_index("ix_document_exports_document_id", "document_exports", ["document_id"])
    op.create_table("processing_checkpoints", *_base(),
        sa.Column("transcription_id", pg.UUID(as_uuid=True), sa.ForeignKey("transcriptions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", pg.UUID(as_uuid=True), sa.ForeignKey("jobs.id", ondelete="SET NULL")),
        sa.Column("stage", sa.String(24), nullable=False), sa.Column("records_count", sa.Integer(), nullable=False),
        sa.UniqueConstraint("tenant_id", "transcription_id", "stage", name="uq_processing_checkpoint"),
    )
    for table in ("document_exports", "processing_checkpoints"):
        op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"])
        op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO "{settings.app_db_user}"')
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {table}_isolation ON {table} USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant())")


def downgrade():
    op.drop_table("processing_checkpoints")
    op.drop_table("document_exports")
