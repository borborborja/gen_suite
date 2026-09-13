"""Derived, versioned retrieval data. Original evidence never lives only in vectors."""
from __future__ import annotations
import uuid
from datetime import datetime
from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from ..db.base import Base, TimestampMixin, uuid_pk


class EmbeddingGeneration(Base, TimestampMixin):
    __tablename__ = "embedding_generations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_embedding_generation_tenant"),
        CheckConstraint("status IN ('building','ready','error','cancelled')", name="status"),
        CheckConstraint("version >= 1", name="version"),
        Index("uq_embedding_generation_active", "tenant_id", unique=True, postgresql_where=text("is_active")),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    purpose: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    include_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    profile: Mapped[dict] = mapped_column(JSONB, nullable=False)
    space_key: Mapped[str] = mapped_column(String(64), nullable=False)
    job_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    research_run_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    error: Mapped[str | None] = mapped_column(Text)


class EmbeddingItem(Base, TimestampMixin):
    __tablename__ = "embedding_items"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "generation_id"], ["embedding_generations.tenant_id", "embedding_generations.id"], ondelete="CASCADE"),
        UniqueConstraint("generation_id", "kind", "source_id", "source_hash", name="uq_embedding_item_source"),
        CheckConstraint("kind IN ('transcription','mention')", name="kind"),
        CheckConstraint("status IN ('pending','ready','stale')", name="status"),
        CheckConstraint("(status = 'ready') = (embedding IS NOT NULL)", name="vector_state"),
        Index("ix_embedding_items_generation_kind", "generation_id", "kind", "status"),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    generation_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    source_tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    # Historical source identifiers; read queries rejoin current evidence under its own RLS.
    document_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    page_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    transcription_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    transcription_end_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    record_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    input_text: Mapped[str] = mapped_column(Text, nullable=False)
    input_fields: Mapped[dict] = mapped_column(JSONB, nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    ai_call_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024))


class EmbeddingIndexEvent(Base):
    __tablename__ = "embedding_index_events"
    __table_args__ = (ForeignKeyConstraint(["tenant_id", "generation_id"], ["embedding_generations.tenant_id", "embedding_generations.id"], ondelete="CASCADE"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    generation_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
