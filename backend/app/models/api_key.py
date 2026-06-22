from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, uuid_pk


class ApiKey(Base):
    """A personal access token for the external API. Global table (no tenant RLS, like
    refresh_tokens) so a token can be resolved by its hash before any tenant context exists; the row
    carries the tenant_id + role the token acts with. Only the SHA-256 hash is stored — the plaintext
    is shown once at creation. ``scope`` 'read' binds the token to the viewer role (GETs only);
    'write' inherits the creator's tenant role."""

    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)  # acting role for this token
    scope: Mapped[str] = mapped_column(String(16), server_default=text("'read'"), nullable=False)
    token_prefix: Mapped[str] = mapped_column(String(16), nullable=False)  # for display, e.g. gsk_ab12
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
