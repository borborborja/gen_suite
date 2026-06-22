"""search: pgvector extension + transcriptions.tsv (FTS) + embedding (vector 1024)

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-16

``tsv`` is a STORED generated column (Spanish FTS config) with a GIN index. ``embedding`` is a
pgvector(1024) column with an HNSW cosine index — 1024 dims so one column serves Ollama
mxbai-embed-large (native) and OpenAI v3 (via its `dimensions` param).
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    op.execute(
        "ALTER TABLE transcriptions ADD COLUMN tsv tsvector "
        "GENERATED ALWAYS AS (to_tsvector('spanish', coalesce(text, ''))) STORED;"
    )
    op.execute("ALTER TABLE transcriptions ADD COLUMN embedding vector(1024);")
    op.execute("CREATE INDEX ix_transcriptions_tsv ON transcriptions USING gin (tsv);")
    op.execute(
        "CREATE INDEX ix_transcriptions_embedding ON transcriptions "
        "USING hnsw (embedding vector_cosine_ops);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_transcriptions_embedding;")
    op.execute("DROP INDEX IF EXISTS ix_transcriptions_tsv;")
    op.execute("ALTER TABLE transcriptions DROP COLUMN IF EXISTS embedding;")
    op.execute("ALTER TABLE transcriptions DROP COLUMN IF EXISTS tsv;")
