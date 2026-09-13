"""Durable synchronization envelopes for Gramps Desktop and Gramps Web peers."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, TimestampMixin, uuid_pk


class InteropConnection(Base, TimestampMixin):
    __tablename__ = "interop_connections"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)  # gramps_desktop | gramps_web
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    endpoint: Mapped[str | None] = mapped_column(String(1024))
    status: Mapped[str] = mapped_column(String(16), server_default=text("'active'"), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    last_pulled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_pushed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SyncBaseline(Base, TimestampMixin):
    __tablename__ = "sync_baselines"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("interop_connections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    snapshot_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    object_counts: Mapped[dict] = mapped_column(JSONB, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(server_default=text("true"), nullable=False)
    established_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))


class SyncChange(Base, TimestampMixin):
    """Append-only sync event. Tombstones are records, never destructive deletes."""

    __tablename__ = "sync_changes"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("interop_connections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_system: Mapped[str] = mapped_column(String(32), nullable=False)
    object_type: Mapped[str] = mapped_column(String(32), nullable=False)
    object_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    operation: Mapped[str] = mapped_column(String(16), nullable=False)  # upsert | tombstone
    remote_revision: Mapped[str | None] = mapped_column(String(128))
    payload: Mapped[dict | None] = mapped_column(JSONB)
    state: Mapped[str] = mapped_column(String(16), server_default=text("'queued'"), nullable=False)
    conflict_with_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("sync_changes.id", ondelete="SET NULL"))
    resolution_note: Mapped[str | None] = mapped_column(Text)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SyncObject(Base, TimestampMixin):
    """Canonical peer object plus its local projection, including fields Gen Suite cannot model yet."""

    __tablename__ = "sync_objects"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("interop_connections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    object_type: Mapped[str] = mapped_column(String(32), nullable=False)
    object_key: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSONB)
    local_type: Mapped[str | None] = mapped_column(String(32))
    local_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)
    remote_revision: Mapped[str | None] = mapped_column(String(128))
    is_tombstone: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)

    __table_args__ = (
        UniqueConstraint("connection_id", "object_type", "object_key", name="uq_sync_objects_connection_key"),
    )
