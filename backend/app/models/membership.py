from __future__ import annotations

import enum
import uuid

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, TimestampMixin, uuid_pk


class MembershipRole(str, enum.Enum):
    tenant_admin = "tenant_admin"
    researcher = "researcher"
    viewer = "viewer"


class Membership(Base, TimestampMixin):
    """Links a user to a tenant with a role. TENANT-SCOPED (RLS enforced).

    The RLS policy allows a row to be read when it belongs to the active tenant OR to
    the authenticated user (so a user can always enumerate their own memberships).
    """

    __tablename__ = "memberships"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (UniqueConstraint("tenant_id", "user_id", name="uq_memberships_tenant_user"),)
