from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, uuid_pk


class Reconstruction(Base):
    """A proposed family-tree reconstruction from the corpus (super-discovery). Lives as JSON (the
    ``graph``) — NOT as Person/Family rows — so the real tree is untouched until the user merges.
    Tenant-scoped (RLS)."""

    __tablename__ = "reconstructions"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)
    status: Mapped[str] = mapped_column(String(16), server_default=text("'running'"), nullable=False)
    conservative: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), nullable=False)
    include_census: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)
    link_to_tree: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), nullable=False)
    graph: Mapped[dict | None] = mapped_column(JSONB)
    stats: Mapped[dict | None] = mapped_column(JSONB)
    merged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
