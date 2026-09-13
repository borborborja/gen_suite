"""Amplía contratos de importación y auditoría sin reescribir datos existentes."""
from alembic import op
import sqlalchemy as sa

revision = "0051"
down_revision = "0050"
branch_labels = depends_on = None

CONSTRAINT = "ck_documents_ck_documents_source_kind"


def upgrade():
    op.drop_constraint(op.f(CONSTRAINT), "documents", type_="check")
    op.create_check_constraint(op.f(CONSTRAINT), "documents",
        "source_kind IN ('upload','familysearch','transcription_output','resumable_upload')")
    op.alter_column("change_log", "entity_type", existing_type=sa.String(16), type_=sa.String(64))


def downgrade():
    # Refuse incompatible historical rows; never truncate audit names or relabel sources.
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM documents WHERE source_kind='resumable_upload')
           OR EXISTS (SELECT 1 FROM change_log WHERE length(entity_type)>16) THEN
            RAISE EXCEPTION '0051 no se puede revertir mientras existan importaciones reanudables o entidades de auditoría largas';
        END IF;
    END $$""")
    op.alter_column("change_log", "entity_type", existing_type=sa.String(64), type_=sa.String(16))
    op.drop_constraint(op.f(CONSTRAINT), "documents", type_="check")
    op.create_check_constraint(op.f(CONSTRAINT), "documents",
        "source_kind IN ('upload','familysearch','transcription_output')")
