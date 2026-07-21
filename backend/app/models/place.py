from __future__ import annotations

import uuid

from sqlalchemy import Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, TimestampMixin, uuid_pk


class Place(Base, TimestampMixin):
    """A place string (as it appears in GEDCOM), deduped per tenant by normalized_key."""

    __tablename__ = "places"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    normalized_key: Mapped[str] = mapped_column(String(512), nullable=False)
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)
    # Optional hierarchy (pueblo → provincia → país). Internal to the app: GEDCOM PLAC
    # keeps exporting the flat name so the round-trip stays stable.
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("places.id", ondelete="SET NULL"), index=True
    )
    place_type: Mapped[str | None] = mapped_column(String(24))  # country|region|province|municipality|parish|other

    __table_args__ = (
        UniqueConstraint("tenant_id", "normalized_key", name="uq_places_tenant_norm"),
    )
