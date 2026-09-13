"""Exportaciones privadas del árbol; sin modificar datos preexistentes."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from app.settings import settings

revision = "0052"
down_revision = "0051"
branch_labels = depends_on = None


def upgrade():
    op.create_table("tree_exports",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("report", pg.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id", "id"], ["jobs.tenant_id", "jobs.id"], ondelete="CASCADE"),
        sa.CheckConstraint("kind IN ('gedcom551', 'gedcom7', 'gedzip')", name="kind"),
        sa.CheckConstraint("byte_size > 0", name="positive_size"),
        sa.CheckConstraint("checksum ~ '^[a-f0-9]{64}$'", name="checksum"),
        sa.CheckConstraint("storage_key LIKE tenant_id::text || '/tree-exports/' || id::text || '/%'", name="private_prefix"),
    )
    op.create_index("ix_tree_exports_tenant_id", "tree_exports", ["tenant_id"])
    op.execute("ALTER TABLE tree_exports ENABLE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY tree_exports_isolation ON tree_exports USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant())")
    # Results cannot be changed by app/worker after publication, including their audit.
    op.execute(f'REVOKE UPDATE, DELETE ON tree_exports FROM "{settings.app_db_user}"')
    op.execute(f'GRANT SELECT, INSERT ON tree_exports TO "{settings.app_db_user}"')


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM tree_exports) THEN
        RAISE EXCEPTION 'Conserva las exportaciones antes de revertir 0052'; END IF; END $$""")
    op.drop_table("tree_exports")
