"""Explicit staging disposal and resumable, version-specific deletion receipts."""
from __future__ import annotations
import uuid
from datetime import datetime
from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from ..db.base import Base, TimestampMixin, uuid_pk


class IngestCleanup(Base, TimestampMixin):
    __tablename__ = "ingest_cleanups"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_ingest_cleanup_tenant"),
        UniqueConstraint("tenant_id", "session_id", name="uq_ingest_cleanup_session"),
        ForeignKeyConstraint(["session_id", "tenant_id"], ["ingest_sessions.id", "ingest_sessions.tenant_id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["tenant_id", "job_id"], ["jobs.tenant_id", "jobs.id"], ondelete="CASCADE"),
        CheckConstraint("mode IN ('compact','discard')", name="mode"),
        CheckConstraint("phase IN ('verify_originals','inventory','delete','verify_empty','completed')", name="phase"),
        CheckConstraint("inventory_epoch>=1 AND retained_reservation_bytes>=0", name="bounds"),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    session_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    phase: Mapped[str] = mapped_column(String(24), nullable=False)
    original_status: Mapped[str] = mapped_column(String(24), nullable=False)
    bucket: Mapped[str] = mapped_column(String(255), nullable=False)
    inventory_epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    verified_after: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    inventory_cursor: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    retained_reservation_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    preview_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    reason: Mapped[str] = mapped_column(String(2000), nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IngestCleanupObject(Base):
    __tablename__ = "ingest_cleanup_objects"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "cleanup_id"], ["ingest_cleanups.tenant_id", "ingest_cleanups.id"], ondelete="CASCADE"),
        CheckConstraint("byte_size>=0 AND inventory_epoch>=1", name="bounds"),
        Index("ix_ingest_cleanup_pending", "cleanup_id", "inventory_epoch", "id", postgresql_where=text("deleted_at IS NULL")),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    cleanup_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    inventory_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    version_id: Mapped[str] = mapped_column(String(1024), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    is_delete_marker: Mapped[bool] = mapped_column(Boolean, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
