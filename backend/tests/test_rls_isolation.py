from __future__ import annotations

import uuid

from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.rls import set_rls_context
from app.db.session import SessionLocal
from app.models.membership import Membership
from app.settings import settings


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _register(client: AsyncClient, email: str) -> str:
    r = await client.post("/api/auth/register", json={"email": email, "password": "supersecret123"})
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


async def test_tenant_member_lists_are_isolated(client: AsyncClient):
    a = await _register(client, "a@example.com")
    b = await _register(client, "b@example.com")

    t1 = (await client.post("/api/tenants", json={"name": "Familia A"}, headers=_auth(a))).json()
    t2 = (await client.post("/api/tenants", json={"name": "Familia B"}, headers=_auth(b))).json()

    # Switch each user into their own tenant.
    a_t1 = (await client.post(f"/api/auth/switch/{t1['id']}", headers=_auth(a))).json()["access_token"]
    b_t2 = (await client.post(f"/api/auth/switch/{t2['id']}", headers=_auth(b))).json()["access_token"]

    members_a = await client.get("/api/tenants/members", headers=_auth(a_t1))
    members_b = await client.get("/api/tenants/members", headers=_auth(b_t2))
    assert {m["email"] for m in members_a.json()} == {"a@example.com"}
    assert {m["email"] for m in members_b.json()} == {"b@example.com"}

    # A cannot switch into B's tenant.
    forbidden = await client.post(f"/api/auth/switch/{t2['id']}", headers=_auth(a))
    assert forbidden.status_code == 403


async def test_rls_policy_blocks_cross_tenant_rows():
    """Direct DB proof: with T1 context, the app role never sees T2's membership rows."""
    t1, t2 = uuid.uuid4(), uuid.uuid4()
    u1, u2 = uuid.uuid4(), uuid.uuid4()

    admin = create_async_engine(settings.admin_database_url)
    async with admin.begin() as conn:
        for tid, name, slug in [(t1, "T1", f"t1-{t1.hex[:6]}"), (t2, "T2", f"t2-{t2.hex[:6]}")]:
            await conn.execute(
                text("INSERT INTO tenants (id, name, slug) VALUES (:id, :n, :s)"),
                {"id": tid, "n": name, "s": slug},
            )
        for uid, email in [(u1, f"{u1.hex[:6]}@e.com"), (u2, f"{u2.hex[:6]}@e.com")]:
            await conn.execute(
                text("INSERT INTO users (id, email, password_hash) VALUES (:id, :e, 'x')"),
                {"id": uid, "e": email},
            )
        for tid, uid in [(t1, u1), (t2, u2)]:
            await conn.execute(
                text(
                    "INSERT INTO memberships (tenant_id, user_id, role) "
                    "VALUES (:t, :u, 'tenant_admin')"
                ),
                {"t": tid, "u": uid},
            )
    await admin.dispose()

    async with SessionLocal() as session:
        await set_rls_context(session, user_id=u1, tenant_id=t1, role="tenant_admin")
        visible = (await session.scalars(select(Membership.tenant_id))).all()

    assert t2 not in visible
    assert set(visible) == {t1}
