from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, TimestampMixin, uuid_pk


class UsageEvent(Base, TimestampMixin):
    """One AI-spend event (usually one per job): token usage + computed cost, for the spending
    control. Tenant-scoped (RLS). Cost is in USD cents, estimated from the model price table."""

    __tablename__ = "usage_events"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    task_type: Mapped[str] = mapped_column(String(24), nullable=False)  # extraction/transcription/...
    model: Mapped[str | None] = mapped_column(String(128))
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
