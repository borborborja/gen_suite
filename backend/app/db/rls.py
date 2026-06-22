"""Row-Level Security context helpers.

Tenant isolation is a property of the database, not of application code: every
tenant-scoped table has an RLS policy keyed on the GUCs set here. We set them with
``set_config(..., is_local => true)`` so they are scoped to the current transaction and
never leak across pooled connections.

GUCs:
  * ``app.user_id``   — the authenticated user (lets a user always see their own rows,
                        e.g. their memberships across tenants).
  * ``app.tenant_id`` — the active tenant; the main isolation key.
  * ``app.user_role`` — membership role within the active tenant.
"""
from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_SET_CONFIG = text(
    "SELECT set_config('app.user_id', :user_id, true),"
    "       set_config('app.tenant_id', :tenant_id, true),"
    "       set_config('app.user_role', :role, true),"
    "       set_config('app.is_server_admin', :is_sa, true)"
)


async def set_rls_context(
    session: AsyncSession,
    *,
    user_id: uuid.UUID | None = None,
    tenant_id: uuid.UUID | None = None,
    role: str | None = None,
    is_server_admin: bool = False,
) -> None:
    """Apply the RLS GUCs for the current transaction. Empty string == unset/NULL."""
    await session.execute(
        _SET_CONFIG,
        {
            "user_id": str(user_id) if user_id else "",
            "tenant_id": str(tenant_id) if tenant_id else "",
            "role": role or "",
            "is_sa": "true" if is_server_admin else "false",
        },
    )


async def commit_keep_rls(
    session: AsyncSession,
    *,
    user_id: uuid.UUID | None = None,
    tenant_id: uuid.UUID | None = None,
    role: str | None = None,
    is_server_admin: bool = False,
) -> None:
    """Commit and immediately re-apply the RLS GUCs. The GUCs are transaction-local (set_config
    local=true), so a plain commit silently drops them and any later query would see no rows —
    a subtle footgun in worker tasks that commit between steps. Use this instead of bare commit
    when more RLS-scoped queries follow in the same task."""
    await session.commit()
    await set_rls_context(
        session, user_id=user_id, tenant_id=tenant_id, role=role, is_server_admin=is_server_admin
    )
