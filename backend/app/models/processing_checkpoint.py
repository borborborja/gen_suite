from __future__ import annotations

import uuid
from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from ..db.base import Base, TimestampMixin, uuid_pk


class ProcessingCheckpoint(Base, TimestampMixin):
    """A validated act/index extraction result, including explicitly empty pages."""
    __tablename__ = "processing_checkpoints"
    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    transcription_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("transcriptions.id", ondelete="CASCADE"), nullable=False)
    job_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL"))
    stage: Mapped[str] = mapped_column(String(24), nullable=False)
    records_count: Mapped[int] = mapped_column(Integer, nullable=False)
    __table_args__ = (UniqueConstraint("tenant_id", "transcription_id", "stage", name="uq_processing_checkpoint"),)
