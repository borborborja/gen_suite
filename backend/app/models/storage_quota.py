"""Application payload admission; uncertain writes retain their entire reservation."""
from __future__ import annotations
import uuid
from datetime import datetime
from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, SmallInteger, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from ..db.base import Base, TimestampMixin, uuid_pk


class StorageQuota(Base, TimestampMixin):
    __tablename__ = "storage_quotas"
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "scan_id"], ["storage_scans.tenant_id", "storage_scans.id"]),
        CheckConstraint("phase IN ('uninitialized','ready','draining','counting')", name="phase"),
        CheckConstraint("used_bytes>=0 AND reserved_bytes>=0", name="amounts"),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True)
    phase: Mapped[str] = mapped_column(String(16), nullable=False, default="uninitialized")
    used_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    reserved_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    location_hash: Mapped[str | None] = mapped_column(String(64))
    scan_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    baseline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StorageWrite(Base, TimestampMixin):
    __tablename__ = "storage_writes"
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_storage_write_tenant"),
        CheckConstraint("kind IN ('put','upload','copy','delete','delete_version')", name="kind"),
        CheckConstraint("state IN ('reserved','sending','succeeded','unknown','cancelled','quarantined')", name="state"),
        CheckConstraint("reserved_bytes>=0 AND (actual_bytes IS NULL OR actual_bytes>=0)", name="amounts"),
        Index("uq_storage_pending_key", "tenant_id", "location_hash", "bucket", "key_hash", unique=True,
              postgresql_where=text("state IN ('reserved','sending','unknown')")),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("storage_quotas.tenant_id", ondelete="CASCADE"), nullable=False, index=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    bucket: Mapped[str] = mapped_column(String(255), nullable=False)
    location_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reserved_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    actual_bytes: Mapped[int | None] = mapped_column(BigInteger)
    target_version_id: Mapped[str | None] = mapped_column(String(1024))
    version_id: Mapped[str | None] = mapped_column(String(1024))
    upload_id: Mapped[str | None] = mapped_column(String(2048))
    error: Mapped[str | None] = mapped_column(Text)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ai_guard_version: Mapped[int | None] = mapped_column(SmallInteger)


class StorageWriteAction(Base):
    __tablename__ = "storage_write_actions"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "write_id"], ["storage_writes.tenant_id", "storage_writes.id"]),
        CheckConstraint("released_bytes>=0", name="released_bytes"),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    write_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    released_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    evidence: Mapped[dict] = mapped_column(JSONB, nullable=False)
