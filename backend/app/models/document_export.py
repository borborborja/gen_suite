from __future__ import annotations

import uuid
from sqlalchemy import ForeignKey, BigInteger, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from ..db.base import Base, TimestampMixin, uuid_pk


class DocumentExport(Base, TimestampMixin):
    """Private, immutable derivative. Access also checks the current parent document."""
    __tablename__ = "document_exports"
    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    manifest: Mapped[dict] = mapped_column(JSONB, nullable=False)
