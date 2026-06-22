from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, TimestampMixin, uuid_pk


class Person(Base, TimestampMixin):
    """An individual in the tree. Tenant-scoped (RLS)."""

    __tablename__ = "persons"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    gedcom_xref: Mapped[str | None] = mapped_column(String(64), index=True)
    sex: Mapped[str] = mapped_column(String(1), server_default=text("'U'"), nullable=False)  # M/F/U
    notes: Mapped[str | None] = mapped_column(Text)
    # Unmapped GEDCOM sub-records, preserved for round-trip export.
    raw: Mapped[list | None] = mapped_column(JSONB)


class Name(Base, TimestampMixin):
    """A name borne by a person (birth/married/aka). One is flagged primary."""

    __tablename__ = "names"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type: Mapped[str] = mapped_column(String(16), server_default=text("'birth'"), nullable=False)
    given: Mapped[str | None] = mapped_column(String(255))
    surname: Mapped[str | None] = mapped_column(String(255), index=True)
    surname_prefix: Mapped[str | None] = mapped_column(String(64))
    nickname: Mapped[str | None] = mapped_column(String(128))
    is_primary: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)
    is_inferred: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)
