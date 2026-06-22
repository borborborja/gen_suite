from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, TimestampMixin, uuid_pk


class Media(Base, TimestampMixin):
    """A photo/portrait attached to a person. Stored as a private object in MinIO (streamed back
    through the API like document pages, never via a public URL). One per person can be the
    primary (used as the tree/ficha avatar). Tenant-scoped (RLS)."""

    __tablename__ = "person_media"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True
    )
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False)
    caption: Mapped[str | None] = mapped_column(String(512))
    is_primary: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)
