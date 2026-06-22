from __future__ import annotations

import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, String
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, TimestampMixin, uuid_pk


class PersonMention(Base, TimestampMixin):
    """A named person + role asserted inside a Record (evidence: an *assertion*).

    ``resolved_person_id`` is set ONLY when a human confirms a match — extraction never links to
    the tree on its own. Blocking keys drive candidate retrieval: ``block_key_*`` are phonetic
    (Spanish-aware) and ``norm_*`` are accent-stripped + Latin→vernacular folded. The ``embedding``
    column is added in raw SQL by migration 0008 (vector(1024); halfvec is the M3 optimization)."""

    __tablename__ = "person_mentions"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    record_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("records.id", ondelete="CASCADE"), nullable=False, index=True
    )
    visibility: Mapped[str] = mapped_column(
        String(16), server_default=sa_text("'private'"), nullable=False
    )
    # principal/father/mother/godfather/godmother/spouse/spouse_father/spouse_mother/witness/...
    role: Mapped[str] = mapped_column(String(24), nullable=False)
    given: Mapped[str | None] = mapped_column(String(255))
    surname: Mapped[str | None] = mapped_column(String(255), index=True)
    surname_prefix: Mapped[str | None] = mapped_column(String(64))
    name_raw: Mapped[str | None] = mapped_column(String(512))  # verbatim, incl. Latin form
    sex: Mapped[str] = mapped_column(String(1), server_default=sa_text("'U'"), nullable=False)
    stated_age: Mapped[str | None] = mapped_column(String(64))
    stated_origin: Mapped[str | None] = mapped_column(String(256))
    stated_status: Mapped[str | None] = mapped_column(String(128))
    occupation: Mapped[str | None] = mapped_column(String(128))
    address: Mapped[str | None] = mapped_column(String(256))  # domicile (key for census co-residence)
    block_key_surname: Mapped[str | None] = mapped_column(String(32), index=True)
    block_key_given: Mapped[str | None] = mapped_column(String(32))
    norm_given: Mapped[str | None] = mapped_column(String(255))
    norm_surname: Mapped[str | None] = mapped_column(String(255), index=True)
    resolved_person_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("persons.id", ondelete="SET NULL"), index=True
    )
    match_status: Mapped[str] = mapped_column(
        String(16), server_default=sa_text("'unlinked'"), nullable=False
    )  # unlinked/candidate/confirmed/rejected
    raw_json: Mapped[dict | None] = mapped_column(JSONB)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024))
