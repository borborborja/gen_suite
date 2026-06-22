from __future__ import annotations

import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, TimestampMixin, uuid_pk


class Transcription(Base, TimestampMixin):
    """Transcribed text for one page of a document. ``visibility`` is denormalized from the
    parent document so public transcriptions are searchable cross-tenant (RLS). FTS + vector
    columns are added in Phase 5."""

    __tablename__ = "transcriptions"

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
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)
    visibility: Mapped[str] = mapped_column(
        String(16), server_default=sa_text("'private'"), nullable=False
    )
    engine: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str | None] = mapped_column(String(128))
    text: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), server_default=sa_text("'ok'"), nullable=False)
    # Exactly one row per (document, page) is active; re-recognition writes candidates (is_active=false)
    # until the user reconciles. Search/extraction/Visor read only active rows.
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=sa_text("true"), nullable=False)
    job_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    # FTS tsv is a DB-generated column (see migration 0006); not mapped here.
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024))
