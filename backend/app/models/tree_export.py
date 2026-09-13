from __future__ import annotations

import uuid
from sqlalchemy import BigInteger, CheckConstraint, ForeignKeyConstraint, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from ..db.base import Base, TimestampMixin


class TreeExport(Base, TimestampMixin):
    """Published private artifact; one immutable result per durable job."""
    __tablename__ = "tree_exports"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "id"], ["jobs.tenant_id", "jobs.id"], ondelete="CASCADE"),
        CheckConstraint("kind IN ('gedcom551', 'gedcom7', 'gedzip')", name="kind"),
        CheckConstraint("byte_size > 0", name="positive_size"),
        CheckConstraint("checksum ~ '^[a-f0-9]{64}$'", name="checksum"),
        CheckConstraint("storage_key LIKE tenant_id::text || '/tree-exports/' || id::text || '/%'", name="private_prefix"),
    )
    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    report: Mapped[dict] = mapped_column(JSONB, nullable=False)
