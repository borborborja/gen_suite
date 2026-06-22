from __future__ import annotations

import uuid

from sqlalchemy import Boolean, Integer, LargeBinary, String, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, TimestampMixin, uuid_pk


class ConnectorCredential(Base, TimestampMixin):
    """Operator-owned (server-scope) secret for a connector, e.g. FamilySearch session cookies.

    Rows are readable (encrypted, so useless without the master key) but only server-admins may
    write them. The plaintext secret is decrypted only inside the backend/worker."""

    __tablename__ = "connector_credentials"

    id: Mapped[uuid.UUID] = uuid_pk()
    connector: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    secret_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    secret_nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_version: Mapped[int] = mapped_column(Integer, server_default=text("1"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
