from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, TimestampMixin, uuid_pk


class ProviderCredential(Base, TimestampMixin):
    """An AI provider credential. ``scope='server'`` rows (tenant_id NULL) are operator-owned
    shared keys; ``scope='tenant'`` rows belong to a tenant. Keys are AES-256-GCM encrypted;
    the plaintext is only ever decrypted inside the backend, never returned by the API."""

    __tablename__ = "provider_credentials"

    id: Mapped[uuid.UUID] = uuid_pk()
    scope: Mapped[str] = mapped_column(String(16), nullable=False)  # server/tenant
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    provider_key: Mapped[str] = mapped_column(String(32), nullable=False)  # catalog key
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(256))
    model_default: Mapped[str | None] = mapped_column(String(128))
    api_key_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    api_key_nonce: Mapped[bytes | None] = mapped_column(LargeBinary)
    key_version: Mapped[int] = mapped_column(Integer, server_default=text("1"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))


class TaskProviderBinding(Base, TimestampMixin):
    """The tenant's chosen default credential for a task type (transcription/embedding/inference)."""

    __tablename__ = "task_provider_bindings"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_type: Mapped[str] = mapped_column(String(24), nullable=False)
    credential_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("provider_credentials.id", ondelete="CASCADE"), nullable=False
    )
    model: Mapped[str | None] = mapped_column(String(128))
    params: Mapped[dict | None] = mapped_column(JSONB)

    __table_args__ = (
        UniqueConstraint("tenant_id", "task_type", name="uq_task_provider_bindings_tenant_task"),
    )
