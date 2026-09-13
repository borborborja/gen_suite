"""Durable admission ledger. Never contains credentials or the source manuscript."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, TimestampMixin, uuid_pk


class AiCall(Base, TimestampMixin):
    __tablename__ = "ai_calls"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "research_run_id"], ["research_runs.tenant_id", "research_runs.id"], name="fk_ai_call_research_run"),
        ForeignKeyConstraint(["tenant_id", "response_write_id"], ["storage_writes.tenant_id", "storage_writes.id"], name="fk_ai_call_response_write"),
        UniqueConstraint("tenant_id", "operation_key", name="uq_ai_call_operation"),
        CheckConstraint("reserved_microusd >= 0 AND (cost_microusd IS NULL OR cost_microusd >= 0)", name="amounts"),
        CheckConstraint("state IN ('inflight', 'succeeded', 'unknown', 'reconciled')", name="state"),
        Index("ix_ai_calls_job_task", "tenant_id", "job_id", "task_type"),
        Index("ix_ai_calls_accounting", "tenant_id", "state", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    # Historical identifiers intentionally survive job/credential deletion.
    job_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    research_run_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)
    task_type: Mapped[str] = mapped_column(String(24), nullable=False)
    operation_key: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    profile: Mapped[dict] = mapped_column(JSONB, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    reserved_microusd: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cost_microusd: Mapped[int | None] = mapped_column(BigInteger)
    usage: Mapped[dict | None] = mapped_column(JSONB)
    provider_request_id: Mapped[str | None] = mapped_column(String(256))
    result_key: Mapped[str | None] = mapped_column(Text)
    result_checksum: Mapped[str | None] = mapped_column(String(64))
    response_write_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reconciliation: Mapped[dict | None] = mapped_column(JSONB)
