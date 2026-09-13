"""Persistent collection manifests and immutable, resumable upload parts."""
from __future__ import annotations

import uuid
from sqlalchemy import BigInteger, ForeignKey, ForeignKeyConstraint, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from ..db.base import Base, TimestampMixin, uuid_pk


class IngestSession(Base, TimestampMixin):
    __tablename__ = "ingest_sessions"
    # session_out exposes the activity timestamp immediately after seal/complete.
    # Fetch server-side UPDATE defaults with RETURNING, never implicit async lazy I/O.
    __mapper_args__ = {"eager_defaults": True}
    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    options: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(String(24), nullable=False, server_default=text("'manifest'"))
    expected_files: Mapped[int] = mapped_column(Integer, nullable=False)
    reserved_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    declared_files: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    declared_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    received_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    job_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL"))
    __table_args__ = (UniqueConstraint("id", "tenant_id", name="uq_ingest_session_tenant"),)


class IngestBook(Base, TimestampMixin):
    __tablename__ = "ingest_books"
    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    session_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    group_key: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    options: Mapped[dict] = mapped_column(JSONB, nullable=False)
    document_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(24), nullable=False, server_default=text("'pending'"))
    error: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        ForeignKeyConstraint(["session_id", "tenant_id"], ["ingest_sessions.id", "ingest_sessions.tenant_id"], ondelete="CASCADE"),
        UniqueConstraint("id", "session_id", "tenant_id", name="uq_ingest_book_tenant"),
    )


class IngestFile(Base, TimestampMixin):
    __tablename__ = "ingest_files"
    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    session_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    book_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    relative_path: Mapped[str] = mapped_column(Text, nullable=False)
    path_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_modified: Mapped[int] = mapped_column(BigInteger, nullable=False)
    received_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    parts: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(String(24), nullable=False, server_default=text("'pending'"))
    checksum: Mapped[str | None] = mapped_column(String(64))
    content_type: Mapped[str | None] = mapped_column(String(64))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    page_count: Mapped[int | None] = mapped_column(Integer)
    storage_key: Mapped[str | None] = mapped_column(String(512))
    error: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        ForeignKeyConstraint(["book_id", "session_id", "tenant_id"], ["ingest_books.id", "ingest_books.session_id", "ingest_books.tenant_id"], ondelete="CASCADE"),
        UniqueConstraint("session_id", "path_hash", name="uq_ingest_file_path"),
        UniqueConstraint("book_id", "position", name="uq_ingest_file_position"),
    )
