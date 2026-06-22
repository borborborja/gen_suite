from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, TimestampMixin, uuid_pk


class IndexEntry(Base, TimestampMixin):
    """One line of a register's index (alphabetical name → folio/entry pointer). Parsed from index
    pages and cross-checked against extracted Records to find acts the extraction missed. Tenant-scoped
    (RLS). ``document_id`` is the book the index belongs to (the same doc, or the indexed book when the
    index is a separate document)."""

    __tablename__ = "index_entries"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    page_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("pages.id", ondelete="SET NULL")
    )  # the index page this entry was read from
    name_raw: Mapped[str | None] = mapped_column(String(256))
    given: Mapped[str | None] = mapped_column(String(128))
    surname: Mapped[str | None] = mapped_column(String(128))
    norm_surname: Mapped[str | None] = mapped_column(String(128), index=True)
    folio_label: Mapped[str | None] = mapped_column(String(32))  # the folio/page it points to
    record_no: Mapped[str | None] = mapped_column(String(32))
    year: Mapped[int | None] = mapped_column(Integer)
    record_type: Mapped[str | None] = mapped_column(String(24))
    raw_json: Mapped[dict | None] = mapped_column(JSONB)
    # Filled by the cross-check: did a Record matching this index entry get extracted?
    matched: Mapped[bool | None] = mapped_column(Boolean)
