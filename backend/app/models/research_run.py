"""Persistent research execution, separate from accepted genealogical conclusions."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from ..db.base import Base, TimestampMixin, uuid_pk


class ResearchRun(Base, TimestampMixin):
    __tablename__ = "research_runs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_research_run_tenant_id"),
        CheckConstraint("budget_microusd >= 0 AND version >= 1", name="bounds"),
        CheckConstraint("status IN ('planned','running','paused','needs_attention','completed','cancelled')", name="status"),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    project_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    created_by: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    budget_microusd: Mapped[int] = mapped_column(BigInteger, nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    profiles: Mapped[dict] = mapped_column(JSONB, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ResearchRunStage(Base, TimestampMixin):
    __tablename__ = "research_run_stages"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "run_id"], ["research_runs.tenant_id", "research_runs.id"], ondelete="CASCADE"),
        UniqueConstraint("run_id", "position", name="uq_research_stage_position"),
        CheckConstraint("position >= 0 AND attempts >= 0", name="bounds"),
        CheckConstraint("status IN ('pending','running','paused','needs_attention','completed','skipped','cancelled')", name="status"),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    run_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    document_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    person_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    job_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    result: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)


class ResearchRunEvent(Base):
    __tablename__ = "research_run_events"
    __table_args__ = (ForeignKeyConstraint(["tenant_id", "run_id"], ["research_runs.tenant_id", "research_runs.id"], ondelete="CASCADE"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    run_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
