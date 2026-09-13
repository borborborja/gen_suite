"""Importaciones revisables y varios documentos por fuente; sin backfill."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from app.settings import settings

revision = "0053"
down_revision = "0052"
branch_labels = depends_on = None


def stamps():
    return [sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()"))
            for name in ("created_at", "updated_at")]


def upgrade():
    op.create_unique_constraint("uq_documents_tenant_id_id", "documents", ["tenant_id", "id"])
    op.create_unique_constraint("uq_research_sources_tenant_id_id", "research_sources", ["tenant_id", "id"])
    op.create_table("tree_imports",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("client_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("apply_job_id", pg.UUID(as_uuid=True)),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("report", pg.JSONB()), *stamps(),
        sa.ForeignKeyConstraint(["tenant_id", "id"], ["jobs.tenant_id", "jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id", "apply_job_id"], ["jobs.tenant_id", "jobs.id"], name="fk_tree_imports_apply_job"),
        sa.UniqueConstraint("tenant_id", "client_id"),
        sa.CheckConstraint("kind IN ('gedcom7','gedzip')", name="kind"),
        sa.CheckConstraint("byte_size > 0", name="positive_size"),
        sa.CheckConstraint("checksum ~ '^[a-f0-9]{64}$'", name="checksum"),
        sa.CheckConstraint("storage_key LIKE tenant_id::text || '/tree-imports/' || id::text || '/%'", name="private_prefix"))
    op.create_index("ix_tree_imports_tenant_id", "tree_imports", ["tenant_id"])
    op.create_table("source_documents",
        sa.Column("tenant_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("source_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", pg.UUID(as_uuid=True), primary_key=True), *stamps(),
        sa.ForeignKeyConstraint(["tenant_id", "source_id"], ["research_sources.tenant_id", "research_sources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id", "document_id"], ["documents.tenant_id", "documents.id"], ondelete="CASCADE"))
    for table in ("tree_imports", "source_documents"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {table}_isolation ON {table} USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant())")
        op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO "{settings.app_db_user}"')
    # The report may be set once. Applying/retrying only changes the linked job.
    op.execute("""CREATE FUNCTION protect_tree_import_input() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
        IF (NEW.tenant_id,NEW.id,NEW.client_id,NEW.kind,NEW.filename,NEW.storage_key,NEW.checksum,NEW.byte_size)
           IS DISTINCT FROM (OLD.tenant_id,OLD.id,OLD.client_id,OLD.kind,OLD.filename,OLD.storage_key,OLD.checksum,OLD.byte_size)
           OR (OLD.report IS NOT NULL AND NEW.report IS DISTINCT FROM OLD.report) THEN
           RAISE EXCEPTION 'El original y el informe de importación son inmutables'; END IF;
        RETURN NEW; END $$""")
    op.execute("CREATE TRIGGER tree_import_input_immutable BEFORE UPDATE ON tree_imports FOR EACH ROW EXECUTE FUNCTION protect_tree_import_input()")


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM tree_imports) OR EXISTS(SELECT 1 FROM source_documents) THEN
        RAISE EXCEPTION 'Conserva las importaciones y vínculos antes de revertir 0053'; END IF; END $$""")
    op.drop_table("source_documents")
    op.drop_table("tree_imports")
    op.execute("DROP FUNCTION protect_tree_import_input()")
    op.drop_constraint("uq_research_sources_tenant_id_id", "research_sources", type_="unique")
    op.drop_constraint("uq_documents_tenant_id_id", "documents", type_="unique")
