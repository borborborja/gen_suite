from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, TimestampMixin, uuid_pk


class Event(Base, TimestampMixin):
    """A dated/placed fact attached to a person OR a family (birth, marriage, death, ...).

    ``date_raw`` keeps the original GEDCOM date string (e.g. "ABT 1850"); ``date_year`` is a
    best-effort integer extracted for sorting.
    """

    __tablename__ = "events"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type: Mapped[str] = mapped_column(String(24), nullable=False)  # birth/baptism/marriage/...
    date_raw: Mapped[str | None] = mapped_column(String(128))
    date_year: Mapped[int | None] = mapped_column(Integer)
    value: Mapped[str | None] = mapped_column(Text)
    is_inferred: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)
    place_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("places.id", ondelete="SET NULL")
    )
    subject_person_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("persons.id", ondelete="CASCADE"), index=True
    )
    subject_family_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("families.id", ondelete="CASCADE"), index=True
    )
