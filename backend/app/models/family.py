from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Integer, PrimaryKeyConstraint, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, TimestampMixin, uuid_pk


class Family(Base, TimestampMixin):
    """A spousal/parental unit. Husband/wife nullable (single-parent records exist)."""

    __tablename__ = "families"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    gedcom_xref: Mapped[str | None] = mapped_column(String(64), index=True)
    husband_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("persons.id", ondelete="SET NULL"), index=True
    )
    wife_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("persons.id", ondelete="SET NULL"), index=True
    )
    raw: Mapped[list | None] = mapped_column(JSONB)


class FamilyChild(Base):
    """Child membership in a family. Composite PK; relation captures birth/adopted/inferred."""

    __tablename__ = "family_children"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    family_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("families.id", ondelete="CASCADE"), nullable=False
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True
    )
    relation: Mapped[str] = mapped_column(String(16), server_default=text("'birth'"), nullable=False)
    seq: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (PrimaryKeyConstraint("family_id", "person_id", name="pk_family_children"),)
