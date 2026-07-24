from __future__ import annotations

import datetime
import uuid

from sqlalchemy import DateTime, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, uuid_pk


class ChangeLog(Base):
    """One audited tree mutation: who/what/when plus row-images to revert it.

    ``rows`` is a list of ``{"table", "pk", "before", "after"}`` — before=None for inserts,
    after=None for deletes; pk is a dict (supports composite keys like family_children).
    """

    __tablename__ = "change_log"

    id: Mapped[uuid.UUID] = uuid_pk()
    # no single-column index: covered by the (tenant_id, created_at DESC) index of migration 0028
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(16))
    entity_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    summary: Mapped[str | None] = mapped_column(String(512))
    rows: Mapped[list] = mapped_column(JSONB, nullable=False)
    reverted_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    revert_of: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("change_log.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
