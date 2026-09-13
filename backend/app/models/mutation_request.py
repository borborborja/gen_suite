"""Idempotency ledger for reviewable tree decisions.

The browser can retry an ``apply`` request after a network interruption.  Keeping the
response in the same tenant-scoped transaction prevents a retry from creating a second
person, citation, or family relationship.
"""
from __future__ import annotations

import datetime
import uuid

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, uuid_pk


class MutationRequest(Base):
    __tablename__ = "mutation_requests"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response: Mapped[dict] = mapped_column(JSONB, nullable=False)
    change_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("change_log.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_mutation_requests_tenant_key"),
    )
