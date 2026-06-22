from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, UniqueConstraint
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, TimestampMixin, uuid_pk


class MatchCandidate(Base, TimestampMixin):
    """A scored hypothesis linking a tree Person to a PersonMention in the corpus.

    Private research artifact (tenant-only RLS). ``evidence`` stores the transparent per-signal
    breakdown (name/date/place/relational/llm) so the review UI can show *why*. Confirming a
    candidate is the ONLY path that writes ``resolved_person_id`` on the mention and (optionally)
    inferred conclusions into the tree — never automatic."""

    __tablename__ = "match_candidates"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tree_person_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True
    )
    person_mention_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("person_mentions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    record_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("records.id", ondelete="SET NULL"), index=True
    )
    score: Mapped[float] = mapped_column(Float, nullable=False)
    evidence: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(
        String(16), server_default=sa_text("'pending'"), nullable=False
    )  # pending/confirmed/rejected/superseded
    method: Mapped[str] = mapped_column(
        String(24), server_default=sa_text("'auto'"), nullable=False
    )  # auto/llm_adjudicated/manual
    # 'self' = the mention IS this tree person; 'sibling' = the mention is a NEW sibling of this
    # person (found via the shared parent-pair), and confirming it materializes/links the parents.
    relation: Mapped[str] = mapped_column(
        String(16), server_default=sa_text("'self'"), nullable=False
    )
    decided_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("tree_person_id", "person_mention_id", name="uq_match_candidates_person_mention"),
    )
