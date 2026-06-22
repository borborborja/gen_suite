from __future__ import annotations

import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, TimestampMixin, uuid_pk


class Record(Base, TimestampMixin):
    """One genealogical act extracted from a transcription page (baptism/marriage/death/...).

    Immutable EVIDENCE: tree conclusions (Person/Event/...) cite Records via ``citations``, never
    the reverse. ``visibility`` is denormalized from the parent document so public records are
    searchable cross-tenant (RLS), exactly like Transcription. ``raw_json`` keeps the full LLM
    extraction verbatim for audit/reprocess. The ``embedding`` column is added in raw SQL by
    migration 0008 (like transcriptions in 0006)."""

    __tablename__ = "records"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    page_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("pages.id", ondelete="SET NULL")
    )
    transcription_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transcriptions.id", ondelete="SET NULL"), index=True
    )
    # An entry can be split across two sheets: page_id/transcription_id are the START; when the act
    # continues on the next sheet, page_end_id/transcription_end_id point to it and is_continued=True.
    # A CHECK constraint (migration 0020) guarantees is_continued ⇒ page_end_id is set.
    page_end_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("pages.id", ondelete="SET NULL")
    )
    transcription_end_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transcriptions.id", ondelete="SET NULL")
    )
    is_continued: Mapped[bool] = mapped_column(
        Boolean, server_default=sa_text("false"), nullable=False
    )
    # Entry number as written on the page ("45", "45 bis"); record_seq is its parsed numeric value
    # (for ordering / gap-and-duplicate validation). Both NULL when the register isn't numbered.
    record_no: Mapped[str | None] = mapped_column(String(32))
    record_seq: Mapped[int | None] = mapped_column(Integer, index=True)
    visibility: Mapped[str] = mapped_column(
        String(16), server_default=sa_text("'private'"), nullable=False
    )
    record_type: Mapped[str] = mapped_column(String(24), nullable=False)  # baptism/marriage/death/...
    date_raw: Mapped[str | None] = mapped_column(String(128))
    date_year: Mapped[int | None] = mapped_column(Integer)
    date_month: Mapped[int | None] = mapped_column(Integer)
    date_day: Mapped[int | None] = mapped_column(Integer)
    place_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("places.id", ondelete="SET NULL")
    )
    parish_raw: Mapped[str | None] = mapped_column(String(256))
    address: Mapped[str | None] = mapped_column(String(256))  # household/domicile (census/residence/will)
    household_key: Mapped[str | None] = mapped_column(String(128), index=True)  # groups co-residents
    summary: Mapped[str | None] = mapped_column(Text)
    attributes: Mapped[dict | None] = mapped_column(JSONB)  # type-specific fields (no migration per type)
    raw_json: Mapped[dict | None] = mapped_column(JSONB)
    extraction_engine: Mapped[str] = mapped_column(String(32), nullable=False)
    extraction_model: Mapped[str | None] = mapped_column(String(128))
    confidence: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(
        String(16), server_default=sa_text("'extracted'"), nullable=False
    )  # extracted/needs_review/reviewed/rejected/superseded
    # [x, y, w, h] when more than one act shares a page (e.g. Kraken line geometry). NULL = full page.
    region_bbox: Mapped[dict | None] = mapped_column(JSONB)
    job_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024))
