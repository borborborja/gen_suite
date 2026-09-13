"""Índices vectoriales generacionales. No modifica ni atribuye los vectores históricos."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from pgvector.sqlalchemy import Vector
from app.settings import settings

revision = "0044"
down_revision = "0043"
branch_labels = depends_on = None


def base(timestamps=True):
    columns = [sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()"))]
    if timestamps: columns.append(sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")))
    return columns


def upgrade():
    op.create_table("embedding_generations", *base(),
        sa.Column("title", sa.String(255), nullable=False), sa.Column("purpose", sa.String(24), nullable=False),
        sa.Column("status", sa.String(16), nullable=False), sa.Column("is_active", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("include_public", sa.Boolean(), server_default="false", nullable=False), sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("profile", pg.JSONB(), nullable=False), sa.Column("space_key", sa.String(64), nullable=False),
        sa.Column("job_id", pg.UUID(as_uuid=True)), sa.Column("research_run_id", pg.UUID(as_uuid=True)),
        sa.Column("created_by", pg.UUID(as_uuid=True)), sa.Column("error", sa.Text()),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_embedding_generation_tenant"),
        sa.CheckConstraint("status IN ('building','ready','error','cancelled')", name="status"),
        sa.CheckConstraint("version >= 1", name="version"))
    op.create_index("uq_embedding_generation_active", "embedding_generations", ["tenant_id"], unique=True, postgresql_where=sa.text("is_active"))
    op.create_table("embedding_items", *base(),
        sa.Column("generation_id", pg.UUID(as_uuid=True), nullable=False), sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("source_id", pg.UUID(as_uuid=True), nullable=False), sa.Column("source_tenant_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", pg.UUID(as_uuid=True), nullable=False), sa.Column("page_id", pg.UUID(as_uuid=True)),
        sa.Column("transcription_id", pg.UUID(as_uuid=True)), sa.Column("transcription_end_id", pg.UUID(as_uuid=True)),
        sa.Column("record_id", pg.UUID(as_uuid=True)), sa.Column("input_text", sa.Text(), nullable=False),
        sa.Column("input_fields", pg.JSONB(), nullable=False), sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("job_id", pg.UUID(as_uuid=True), nullable=False), sa.Column("ai_call_id", pg.UUID(as_uuid=True)),
        sa.Column("status", sa.String(16), nullable=False), sa.Column("embedding", Vector(1024)),
        sa.ForeignKeyConstraint(["tenant_id", "generation_id"], ["embedding_generations.tenant_id", "embedding_generations.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("generation_id", "kind", "source_id", "source_hash", name="uq_embedding_item_source"),
        sa.CheckConstraint("kind IN ('transcription','mention')", name="kind"),
        sa.CheckConstraint("status IN ('pending','ready','stale')", name="status"),
        sa.CheckConstraint("(status = 'ready') = (embedding IS NOT NULL)", name="vector_state"))
    op.create_index("ix_embedding_items_generation_kind", "embedding_items", ["generation_id", "kind", "status"])
    for column in ("job_id", "document_id"):
        op.create_index(f"ix_embedding_items_{column}", "embedding_items", [column])
    op.create_table("embedding_index_events", *base(False),
        sa.Column("generation_id", pg.UUID(as_uuid=True), nullable=False), sa.Column("actor_id", pg.UUID(as_uuid=True)),
        sa.Column("kind", sa.String(32), nullable=False), sa.Column("detail", pg.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id", "generation_id"], ["embedding_generations.tenant_id", "embedding_generations.id"], ondelete="CASCADE"))
    op.create_index("ix_embedding_index_events_generation_id", "embedding_index_events", ["generation_id"])
    for table in ("embedding_generations", "embedding_items", "embedding_index_events"):
        op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"])
        op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO "{settings.app_db_user}"')
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {table}_isolation ON {table} USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant())")


def downgrade():
    for table in ("embedding_index_events", "embedding_items", "embedding_generations"):
        op.drop_table(table)
