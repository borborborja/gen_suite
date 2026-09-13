from __future__ import annotations

import uuid
from sqlalchemy import BigInteger, CheckConstraint, ForeignKeyConstraint, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from ..db.base import Base, TimestampMixin


class TreeImport(Base, TimestampMixin):
    __tablename__ = "tree_imports"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "id"], ["jobs.tenant_id", "jobs.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["tenant_id", "apply_job_id"], ["jobs.tenant_id", "jobs.id"], name="fk_tree_imports_apply_job"),
        UniqueConstraint("tenant_id", "client_id"),
        CheckConstraint("kind IN ('gedcom7','gedzip')", name="kind"),
        CheckConstraint("byte_size > 0", name="positive_size"),
        CheckConstraint("checksum ~ '^[a-f0-9]{64}$'", name="checksum"),
        CheckConstraint("storage_key LIKE tenant_id::text || '/tree-imports/' || id::text || '/%'", name="private_prefix"),
    )
    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    client_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    apply_job_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    report: Mapped[dict | None] = mapped_column(JSONB)


class SourceDocument(Base, TimestampMixin):
    """A source can have several digital volumes; these are not new person citations."""
    __tablename__ = "source_documents"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "source_id"], ["research_sources.tenant_id", "research_sources.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["tenant_id", "document_id"], ["documents.tenant_id", "documents.id"], ondelete="CASCADE"),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    source_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
