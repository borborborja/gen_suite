"""Historical observations of tenant storage, not an admission/quota ledger."""
from __future__ import annotations

import uuid
from datetime import datetime
from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from ..db.base import Base, TimestampMixin, uuid_pk


class StorageScan(Base, TimestampMixin):
    __tablename__ = "storage_scans"
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_storage_scan_tenant"),
        UniqueConstraint("job_id", name="uq_storage_scan_job"),
        ForeignKeyConstraint(["tenant_id", "job_id"], ["jobs.tenant_id", "jobs.id"], ondelete="CASCADE"),
        CheckConstraint("phase IN ('versions','uploads','parts','completed')", name="phase"),
        CheckConstraint("bucket_slot BETWEEN 0 AND 1", name="bucket_slot"),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    job_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    buckets: Mapped[list] = mapped_column(JSONB, nullable=False)
    location_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    phase: Mapped[str] = mapped_column(String(16), nullable=False, default="versions")
    bucket_slot: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cursor: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_by: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    reason: Mapped[str] = mapped_column(String(2000), nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StorageInventoryItem(Base):
    __tablename__ = "storage_inventory_items"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "scan_id"], ["storage_scans.tenant_id", "storage_scans.id"], ondelete="CASCADE"),
        CheckConstraint("byte_size>=0 AND bucket_slot BETWEEN 0 AND 1 AND parts_cursor BETWEEN 0 AND 10000", name="bounds"),
        CheckConstraint("category IN ('documents','exports','staging','media','ai_responses','other')", name="category"),
        CheckConstraint("(kind IN ('version','delete_marker') AND version_id IS NOT NULL AND is_latest IS NOT NULL AND upload_id IS NULL AND part_number IS NULL AND upload_state IS NULL) OR "
                        "(kind='upload' AND upload_id IS NOT NULL AND upload_state IS NOT NULL AND upload_state IN ('pending','listed','unavailable') AND version_id IS NULL AND is_latest IS NULL AND part_number IS NULL) OR "
                        "(kind='part' AND upload_id IS NOT NULL AND part_number IS NOT NULL AND part_number BETWEEN 1 AND 10000 AND version_id IS NULL AND is_latest IS NULL AND upload_state IS NULL)", name="identity"),
        CheckConstraint("kind NOT IN ('upload','delete_marker') OR byte_size=0", name="non_payload"),
        Index("ix_storage_inventory_pending", "scan_id", "id", postgresql_where=text("kind='upload' AND upload_state='pending'")),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    scan_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    bucket_slot: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    category: Mapped[str] = mapped_column(String(24), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    version_id: Mapped[str | None] = mapped_column(String(1024))
    upload_id: Mapped[str | None] = mapped_column(String(2048))
    part_number: Mapped[int | None] = mapped_column(Integer)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    is_latest: Mapped[bool | None] = mapped_column(Boolean)
    upload_state: Mapped[str | None] = mapped_column(String(16))
    parts_cursor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
