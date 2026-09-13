from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, TimestampMixin, uuid_pk


class Citation(Base, TimestampMixin):
    """Provenance link: an inferred CONCLUSION (person/name/event/family/family_child) → the SOURCE
    evidence that supports it (record / page image / transcription / the exact mention, and the
    match that created it). The spine of "always with a source": every inferred tree row gets one.

    ``target_id`` is polymorphic (keyed by ``target_type``) so there is no DB-level FK on it — same
    soft-reference approach the codebase already uses (e.g. ``Job`` ids on transcriptions). A
    periodic integrity check sweeps orphans."""

    __tablename__ = "citations"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_type: Mapped[str] = mapped_column(String(16), nullable=False)  # person/name/event/family/family_child
    target_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    record_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("records.id", ondelete="SET NULL"), index=True
    )
    page_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("pages.id", ondelete="SET NULL")
    )
    transcription_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transcriptions.id", ondelete="SET NULL")
    )
    person_mention_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("person_mentions.id", ondelete="SET NULL")
    )
    match_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("match_candidates.id", ondelete="SET NULL")
    )
    # The original evidence links above remain useful for generated citations.  ``source_id``
    # introduces the bibliographic layer needed for manual and interoperable research.
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("research_sources.id", ondelete="SET NULL"), index=True
    )
    assertion_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("research_assertions.id", ondelete="SET NULL"), index=True
    )
    locator: Mapped[dict | None] = mapped_column(JSONB)  # folio, region, line range, archive ref…
    quote: Mapped[str | None] = mapped_column(Text)
    quality: Mapped[str | None] = mapped_column(String(16))  # original/derivative/uncertain
    note: Mapped[str | None] = mapped_column(Text)
