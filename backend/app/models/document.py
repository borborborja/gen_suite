from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, TimestampMixin, uuid_pk


class Document(Base, TimestampMixin):
    """An uploaded/derived source (image set, PDF). Tenant-scoped, but PUBLIC docs are
    readable cross-tenant (RLS exception). Publishing requires a rights declaration."""

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(32), nullable=False)  # image_set/pdf/other
    visibility: Mapped[str] = mapped_column(
        String(16), server_default=text("'private'"), nullable=False
    )
    rights_declaration: Mapped[str | None] = mapped_column(String(32))
    rights_declared_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    rights_declared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rights_declared_ip: Mapped[str | None] = mapped_column(String(64))
    source_kind: Mapped[str] = mapped_column(
        String(32), server_default=text("'upload'"), nullable=False
    )  # upload/familysearch/transcription_output
    # Mode A (retain) keeps page images so the source page can be re-shown; Mode B (data_only)
    # keeps only the extracted facts + citation. ``may_contain_living`` is a GDPR flag.
    image_policy: Mapped[str] = mapped_column(
        String(16), server_default=text("'retain'"), nullable=False
    )  # retain | data_only
    may_contain_living: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), nullable=False
    )
    source_origin: Mapped[str | None] = mapped_column(String(64))  # own_photo/public_archive/...
    # Provenance: the external origin (e.g. the FamilySearch book URL) and, for a derived document
    # (compacted PDF), the parent it was built from. So every act traces back to its source.
    source_ref: Mapped[str | None] = mapped_column(Text)
    derived_from_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL")
    )
    default_record_type: Mapped[str | None] = mapped_column(String(32))  # baptism/census/death/...
    # Parish book series: each parish numbers its books (Belmez baptisms 11, 12, 13…). The series is
    # (place_id + default_record_type); book_number is this book's ordinal in it → gap detection.
    book_number: Mapped[int | None] = mapped_column(Integer)
    # An index document (alphabetical name→folio) rather than a register of acts. ``indexes_for_id``
    # links a SEPARATE index document to the book it indexes (some dioceses ship the index apart).
    is_index: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)
    indexes_for_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL")
    )
    storage_bucket: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_prefix: Mapped[str] = mapped_column(String(256), nullable=False)
    page_count: Mapped[int] = mapped_column(Integer, server_default=text("0"), nullable=False)
    fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)
    place_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("places.id", ondelete="SET NULL")
    )
    year_from: Mapped[int | None] = mapped_column(Integer)
    year_to: Mapped[int | None] = mapped_column(Integer)
    created_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))


class Page(Base, TimestampMixin):
    """One page/image of a document. ``visibility`` is denormalized from the parent document
    so the RLS policy needs no subquery."""

    __tablename__ = "pages"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    visibility: Mapped[str] = mapped_column(
        String(16), server_default=text("'private'"), nullable=False
    )
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)  # sequential upload order
    # The page's OWN printed/written number as it appears on the sheet ("23v", "fol. 145"), captured
    # by extraction — distinct from page_no (upload order). NULL when the register isn't foliated.
    folio_label: Mapped[str | None] = mapped_column(String(32))
    # Page role within the book: a normal act page, an index range (name→folio), or a cover/blank.
    # Index/cover/blank pages are skipped by record extraction; index pages get parsed separately.
    kind: Mapped[str] = mapped_column(
        String(16), server_default=text("'record'"), nullable=False
    )  # record | index | cover | blank
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(Text)  # exact image origin (e.g. FamilySearch ARK)
    image_purged: Mapped[bool] = mapped_column(  # Mode B: data kept, image discarded
        Boolean, server_default=text("false"), nullable=False
    )
    content_type: Mapped[str | None] = mapped_column(String(64))
    byte_size: Mapped[int | None] = mapped_column(Integer)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (UniqueConstraint("document_id", "page_no", name="uq_pages_document_page"),)
