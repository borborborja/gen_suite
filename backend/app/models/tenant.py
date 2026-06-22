from __future__ import annotations

import uuid

from sqlalchemy import Boolean, Integer, String, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, TimestampMixin, uuid_pk


class Tenant(Base, TimestampMixin):
    """A customer account. Global table (no RLS) — rows are referenced by tenant_id."""

    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    plan: Mapped[str] = mapped_column(String(32), server_default=text("'free'"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), server_default=text("'active'"), nullable=False)
    # The "home" person the tree viewer opens on by default (set from the UI).
    home_person_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    # Optional monthly AI-spend cap (USD cents). When set and the month's spend reaches it, new
    # transcription/extraction jobs are refused. NULL = no cap.
    monthly_budget_cents: Mapped[int | None] = mapped_column(Integer)
    # Rasterization quality for uploaded PDFs (the resolution the vision model actually reads).
    raster_dpi: Mapped[int] = mapped_column(Integer, server_default=text("300"), nullable=False)
    raster_format: Mapped[str] = mapped_column(String(8), server_default=text("'webp'"), nullable=False)
    # Auto-split landscape two-page spreads into two single-page faces.
    raster_autosplit: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), nullable=False)
