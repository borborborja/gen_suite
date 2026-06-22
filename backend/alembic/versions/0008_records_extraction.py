"""records extraction: records, person_mentions, match_candidates, citations

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-19

``records`` and ``person_mentions`` are EVIDENCE: tenant-scoped with the public-read RLS exception
(they inherit ``visibility`` from the parent document, like transcriptions). ``match_candidates``
and ``citations`` are private research artifacts (tenant-only RLS). Embedding columns + HNSW indexes
are added in raw SQL (like 0006). pg_trgm powers fuzzy surname blocking (GIN trigram index on
``norm_surname``). The ``vector`` extension already exists from 0006.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.settings import settings

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PUBLIC_READ = ["records", "person_mentions"]
_TENANT_ONLY = ["match_candidates", "citations"]
_TABLES = _PUBLIC_READ + _TENANT_ONLY


def _uuid_pk() -> sa.Column:
    return sa.Column(
        "id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False
    )


def _tenant_fk() -> sa.Column:
    return sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False)


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def upgrade() -> None:
    # ── records (one extracted act per page/region) ──
    op.create_table(
        "records",
        _uuid_pk(),
        _tenant_fk(),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("page_id", postgresql.UUID(as_uuid=True)),
        sa.Column("transcription_id", postgresql.UUID(as_uuid=True)),
        sa.Column("visibility", sa.String(16), server_default=sa.text("'private'"), nullable=False),
        sa.Column("record_type", sa.String(24), nullable=False),
        sa.Column("date_raw", sa.String(128)),
        sa.Column("date_year", sa.Integer()),
        sa.Column("date_month", sa.Integer()),
        sa.Column("date_day", sa.Integer()),
        sa.Column("place_id", postgresql.UUID(as_uuid=True)),
        sa.Column("parish_raw", sa.String(256)),
        sa.Column("summary", sa.Text()),
        sa.Column("raw_json", postgresql.JSONB()),
        sa.Column("extraction_engine", sa.String(32), nullable=False),
        sa.Column("extraction_model", sa.String(128)),
        sa.Column("confidence", sa.Float()),
        sa.Column("status", sa.String(16), server_default=sa.text("'extracted'"), nullable=False),
        sa.Column("region_bbox", postgresql.JSONB()),
        sa.Column("job_id", postgresql.UUID(as_uuid=True)),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_records"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_records_tenant_id_tenants", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], name="fk_records_document_id_documents", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["page_id"], ["pages.id"], name="fk_records_page_id_pages", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["transcription_id"], ["transcriptions.id"], name="fk_records_transcription_id_transcriptions", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["place_id"], ["places.id"], name="fk_records_place_id_places", ondelete="SET NULL"),
    )
    op.create_index("ix_records_tenant_id", "records", ["tenant_id"])
    op.create_index("ix_records_document_id", "records", ["document_id"])
    op.create_index("ix_records_transcription_id", "records", ["transcription_id"])

    # ── person_mentions (each named person+role in a record) ──
    op.create_table(
        "person_mentions",
        _uuid_pk(),
        _tenant_fk(),
        sa.Column("record_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("visibility", sa.String(16), server_default=sa.text("'private'"), nullable=False),
        sa.Column("role", sa.String(24), nullable=False),
        sa.Column("given", sa.String(255)),
        sa.Column("surname", sa.String(255)),
        sa.Column("surname_prefix", sa.String(64)),
        sa.Column("name_raw", sa.String(512)),
        sa.Column("sex", sa.String(1), server_default=sa.text("'U'"), nullable=False),
        sa.Column("stated_age", sa.String(64)),
        sa.Column("stated_origin", sa.String(256)),
        sa.Column("stated_status", sa.String(128)),
        sa.Column("block_key_surname", sa.String(32)),
        sa.Column("block_key_given", sa.String(32)),
        sa.Column("norm_given", sa.String(255)),
        sa.Column("norm_surname", sa.String(255)),
        sa.Column("resolved_person_id", postgresql.UUID(as_uuid=True)),
        sa.Column("match_status", sa.String(16), server_default=sa.text("'unlinked'"), nullable=False),
        sa.Column("raw_json", postgresql.JSONB()),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_person_mentions"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_person_mentions_tenant_id_tenants", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["record_id"], ["records.id"], name="fk_person_mentions_record_id_records", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["resolved_person_id"], ["persons.id"], name="fk_person_mentions_resolved_person_id_persons", ondelete="SET NULL"),
    )
    op.create_index("ix_person_mentions_tenant_id", "person_mentions", ["tenant_id"])
    op.create_index("ix_person_mentions_record_id", "person_mentions", ["record_id"])
    op.create_index("ix_person_mentions_surname", "person_mentions", ["surname"])
    op.create_index("ix_person_mentions_norm_surname", "person_mentions", ["norm_surname"])
    op.create_index("ix_person_mentions_block_key_surname", "person_mentions", ["block_key_surname"])
    op.create_index("ix_person_mentions_resolved_person_id", "person_mentions", ["resolved_person_id"])
    # Composite for blocking under RLS: every query is tenant-scoped + blocked by phonetic surname.
    op.create_index("ix_person_mentions_tenant_block_surname", "person_mentions", ["tenant_id", "block_key_surname"])

    # ── match_candidates (tree_person ↔ mention, scored) ──
    op.create_table(
        "match_candidates",
        _uuid_pk(),
        _tenant_fk(),
        sa.Column("tree_person_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("person_mention_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("record_id", postgresql.UUID(as_uuid=True)),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("evidence", postgresql.JSONB()),
        sa.Column("status", sa.String(16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("method", sa.String(24), server_default=sa.text("'auto'"), nullable=False),
        sa.Column("decided_by", postgresql.UUID(as_uuid=True)),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_match_candidates"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_match_candidates_tenant_id_tenants", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tree_person_id"], ["persons.id"], name="fk_match_candidates_tree_person_id_persons", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["person_mention_id"], ["person_mentions.id"], name="fk_match_candidates_person_mention_id_person_mentions", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["record_id"], ["records.id"], name="fk_match_candidates_record_id_records", ondelete="SET NULL"),
        sa.UniqueConstraint("tree_person_id", "person_mention_id", name="uq_match_candidates_person_mention"),
    )
    op.create_index("ix_match_candidates_tenant_id", "match_candidates", ["tenant_id"])
    op.create_index("ix_match_candidates_tree_person_id", "match_candidates", ["tree_person_id"])
    op.create_index("ix_match_candidates_person_mention_id", "match_candidates", ["person_mention_id"])
    op.create_index("ix_match_candidates_record_id", "match_candidates", ["record_id"])

    # ── citations (provenance: conclusion → source evidence) ──
    op.create_table(
        "citations",
        _uuid_pk(),
        _tenant_fk(),
        sa.Column("target_type", sa.String(16), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("record_id", postgresql.UUID(as_uuid=True)),
        sa.Column("page_id", postgresql.UUID(as_uuid=True)),
        sa.Column("transcription_id", postgresql.UUID(as_uuid=True)),
        sa.Column("person_mention_id", postgresql.UUID(as_uuid=True)),
        sa.Column("match_candidate_id", postgresql.UUID(as_uuid=True)),
        sa.Column("note", sa.Text()),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_citations"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_citations_tenant_id_tenants", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["record_id"], ["records.id"], name="fk_citations_record_id_records", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["page_id"], ["pages.id"], name="fk_citations_page_id_pages", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["transcription_id"], ["transcriptions.id"], name="fk_citations_transcription_id_transcriptions", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["person_mention_id"], ["person_mentions.id"], name="fk_citations_person_mention_id_person_mentions", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["match_candidate_id"], ["match_candidates.id"], name="fk_citations_match_candidate_id_match_candidates", ondelete="SET NULL"),
    )
    op.create_index("ix_citations_tenant_id", "citations", ["tenant_id"])
    op.create_index("ix_citations_target", "citations", ["target_type", "target_id"])
    op.create_index("ix_citations_record_id", "citations", ["record_id"])

    # ── grants ──
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {', '.join(_TABLES)} TO {settings.app_db_user};")

    # ── RLS: evidence tables are public-readable; research artifacts are tenant-only ──
    for table in _PUBLIC_READ:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(
            f"CREATE POLICY {table}_isolation ON {table} "
            f"USING (tenant_id = app_current_tenant() OR visibility = 'public') "
            f"WITH CHECK (tenant_id = app_current_tenant());"
        )
    for table in _TENANT_ONLY:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(
            f"CREATE POLICY {table}_isolation ON {table} "
            f"USING (tenant_id = app_current_tenant()) "
            f"WITH CHECK (tenant_id = app_current_tenant());"
        )

    # ── pg_trgm for fuzzy surname blocking (the `%` operator + GIN trigram index) ──
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
    op.execute(
        "CREATE INDEX ix_person_mentions_norm_surname_trgm ON person_mentions "
        "USING gin (norm_surname gin_trgm_ops);"
    )

    # ── pgvector embedding columns + HNSW cosine indexes (raw SQL, like 0006) ──
    # vector(1024) keeps byte-compatibility with transcriptions.embedding and embed_texts.
    # person_mentions.embedding → halfvec(1024) is the M3 scale optimization (millions of rows).
    op.execute("ALTER TABLE records ADD COLUMN embedding vector(1024);")
    op.execute("ALTER TABLE person_mentions ADD COLUMN embedding vector(1024);")
    op.execute("CREATE INDEX ix_records_embedding ON records USING hnsw (embedding vector_cosine_ops);")
    op.execute(
        "CREATE INDEX ix_person_mentions_embedding ON person_mentions "
        "USING hnsw (embedding vector_cosine_ops);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_person_mentions_embedding;")
    op.execute("DROP INDEX IF EXISTS ix_records_embedding;")
    op.execute("DROP INDEX IF EXISTS ix_person_mentions_norm_surname_trgm;")
    for table in reversed(_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {table}_isolation ON {table};")
    op.drop_table("citations")
    op.drop_table("match_candidates")
    op.drop_table("person_mentions")
    op.drop_table("records")
