"""Procedencia de imágenes y revisiones; los valores históricos desconocidos siguen nulos."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "0039"
down_revision = "0038"
branch_labels = depends_on = None


def upgrade():
    op.add_column("transcriptions", sa.Column("supersedes_id", pg.UUID(as_uuid=True)))
    op.create_foreign_key("fk_transcription_supersedes", "transcriptions", "transcriptions", ["supersedes_id"], ["id"], ondelete="SET NULL")
    op.add_column("transcriptions", sa.Column("reviewed_by", pg.UUID(as_uuid=True)))
    op.add_column("transcriptions", sa.Column("provenance", pg.JSONB()))
    op.add_column("pages", sa.Column("original_filename", sa.Text()))
    op.add_column("pages", sa.Column("checksum", sa.String(64)))
    op.add_column("pages", sa.Column("source_page_no", sa.Integer()))
    op.add_column("pages", sa.Column("source_region", pg.JSONB()))


def downgrade():
    for column in ("source_region", "source_page_no", "checksum", "original_filename"):
        op.drop_column("pages", column)
    op.drop_constraint("fk_transcription_supersedes", "transcriptions", type_="foreignkey")
    for column in ("provenance", "reviewed_by", "supersedes_id"):
        op.drop_column("transcriptions", column)
