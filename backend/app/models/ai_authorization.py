"""Append-only financial renewals; execution profiles and paid receipts remain immutable."""
from __future__ import annotations
import uuid
from datetime import datetime
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from ..db.base import Base, uuid_pk


class AiBillingAuthorization(Base):
    __tablename__ = "ai_billing_authorizations"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "research_run_id"], ["research_runs.tenant_id", "research_runs.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["tenant_id", "embedding_generation_id"], ["embedding_generations.tenant_id", "embedding_generations.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["tenant_id", "job_id"], ["jobs.tenant_id", "jobs.id"], ondelete="CASCADE"),
        CheckConstraint("num_nonnulls(research_run_id,embedding_generation_id,job_id) = 1", name="one_owner"),
        CheckConstraint("version >= 1 AND length(btrim(reason)) > 0", name="approval"),
        CheckConstraint("task_type IN ('transcription','inference','embedding')", name="task"),
        *(Index(f"uq_ai_authorization_{column}", "tenant_id", column, "task_type", "version", unique=True,
                 postgresql_where=text(f"{column} IS NOT NULL"))
          for column in ("research_run_id", "embedding_generation_id", "job_id")),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    research_run_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    embedding_generation_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    job_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    task_type: Mapped[str] = mapped_column(String(24), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    base_profile_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    budget_policy: Mapped[dict] = mapped_column(JSONB, nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    preview_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    reason: Mapped[str] = mapped_column(String(2000), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
