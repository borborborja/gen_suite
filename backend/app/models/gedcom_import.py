from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, uuid_pk


class GedcomImport(Base):
    """Audit record of a GEDCOM import; stores the original file for fidelity/re-processing."""

    __tablename__ = "gedcom_imports"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    filename: Mapped[str | None] = mapped_column(String(255))
    char_encoding: Mapped[str | None] = mapped_column(String(32))
    individuals_count: Mapped[int] = mapped_column(Integer, server_default=text("0"), nullable=False)
    families_count: Mapped[int] = mapped_column(Integer, server_default=text("0"), nullable=False)
    raw_gedcom: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
