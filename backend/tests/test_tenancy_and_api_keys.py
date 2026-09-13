"""Coverage for two previously-untested surfaces: the destructive /tenants/reset endpoint
and the external API-key (gsk_) auth path."""
from __future__ import annotations

from httpx import AsyncClient


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _tenant_token(client: AsyncClient, email: str, tenant: str) -> str:
    reg = await client.post("/api/auth/register", json={"email": email, "password": "supersecret123"})
    tok = reg.json()["access_token"]
    t = await client.post("/api/tenants", json={"name": tenant}, headers=_auth(tok))
    sw = await client.post(f"/api/auth/switch/{t.json()['id']}", headers=_auth(tok))
    return sw.json()["access_token"]


async def test_tenant_reset_wipes_tree(client: AsyncClient):
    h = _auth(await _tenant_token(client, "reset@example.com", "Casa Reset"))

    p = await client.post("/api/tree/persons", headers=h, json={"given": "Ana", "surname": "Prova"})
    assert p.status_code in (200, 201), p.text
    stats = (await client.get("/api/tree/stats", headers=h)).json()
    assert stats["persons"] == 1

    # scope goes in the body (like every other mutating POST)
    bad = await client.post("/api/tenants/reset", headers=h, json={"scope": "everything"})
    assert bad.status_code == 400

    r = await client.post("/api/tenants/reset", headers=h, json={"scope": "tree"})
    assert r.status_code == 200, r.text

    stats = (await client.get("/api/tree/stats", headers=h)).json()
    assert stats["persons"] == 0


async def test_tenant_reset_requires_admin_role(client: AsyncClient):
    admin_tok = await _tenant_token(client, "admin2@example.com", "Casa Roles")
    # add a viewer member and log in as them
    await client.post("/api/auth/register", json={"email": "viewer@example.com", "password": "supersecret123"})
    add = await client.post(
        "/api/tenants/members", headers=_auth(admin_tok),
        json={"email": "viewer@example.com", "role": "viewer"},
    )
    assert add.status_code in (200, 201), add.text
    login = await client.post("/api/auth/login", json={"email": "viewer@example.com", "password": "supersecret123"})
    viewer_tok = login.json()["access_token"]
    r = await client.post("/api/tenants/reset", headers=_auth(viewer_tok), json={"scope": "tree"})
    assert r.status_code == 403


async def test_api_key_auth_and_revocation(client: AsyncClient):
    h = _auth(await _tenant_token(client, "keys@example.com", "Casa Keys"))

    created = await client.post("/api/api-keys", headers=h, json={"name": "script", "scope": "read"})
    assert created.status_code == 201, created.text
    token = created.json()["token"]
    assert token.startswith("gsk_")

    # the gsk_ token authenticates like a bearer JWT, scoped to the tenant
    kh = {"Authorization": f"Bearer {token}"}
    stats = await client.get("/api/tree/stats", headers=kh)
    assert stats.status_code == 200, stats.text

    # read-scope key cannot write
    w = await client.post("/api/tree/persons", headers=kh, json={"given": "X", "surname": "Y"})
    assert w.status_code == 403, w.text

    # revoked key stops working
    key_id = (await client.get("/api/api-keys", headers=h)).json()[0]["id"]
    rv = await client.delete(f"/api/api-keys/{key_id}", headers=h)
    assert rv.status_code == 204
    again = await client.get("/api/tree/stats", headers=kh)
    assert again.status_code == 401


async def test_membership_demotion_takes_effect_for_existing_jwt_and_api_key(client: AsyncClient):
    owner = _auth(await _tenant_token(client, "workspace-owner@example.com", "Casa permisos"))
    registered = await client.post(
        "/api/auth/register", json={"email": "editor@example.com", "password": "supersecret123"}
    )
    assert registered.status_code == 201
    added = await client.post(
        "/api/tenants/members", headers=owner, json={"email": "editor@example.com", "role": "editor"}
    )
    assert added.status_code == 201, added.text
    editor_login = await client.post(
        "/api/auth/login", json={"email": "editor@example.com", "password": "supersecret123"}
    )
    editor = _auth(editor_login.json()["access_token"])
    key = await client.post("/api/api-keys", headers=editor, json={"name": "companion", "scope": "write"})
    assert key.status_code == 201, key.text
    key_headers = _auth(key.json()["token"])

    demoted = await client.patch(
        f"/api/tenants/members/{added.json()['user_id']}", headers=owner, json={"role": "viewer"}
    )
    assert demoted.status_code == 200, demoted.text
    # Both the old JWT and an already-created write token now see the current viewer role.
    for headers in (editor, key_headers):
        blocked = await client.post("/api/tree/persons", headers=headers, json={"given": "No", "surname": "Permiso"})
        assert blocked.status_code == 403, blocked.text
