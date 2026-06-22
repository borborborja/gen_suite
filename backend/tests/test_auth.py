from __future__ import annotations

from httpx import AsyncClient


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_register_first_user_is_server_admin(client: AsyncClient):
    r = await client.post(
        "/api/auth/register",
        json={"email": "admin@example.com", "password": "supersecret123"},
    )
    assert r.status_code == 201, r.text
    tokens = r.json()
    me = await client.get("/api/auth/me", headers=_auth(tokens["access_token"]))
    assert me.status_code == 200
    body = me.json()
    assert body["user"]["is_server_admin"] is True
    assert body["active_tenant_id"] is None
    assert body["memberships"] == []


async def test_login_and_refresh_rotation(client: AsyncClient):
    await client.post(
        "/api/auth/register", json={"email": "u@example.com", "password": "supersecret123"}
    )
    login = await client.post(
        "/api/auth/login", json={"email": "u@example.com", "password": "supersecret123"}
    )
    assert login.status_code == 200, login.text
    first = login.json()["refresh_token"]

    rotated = await client.post("/api/auth/refresh", json={"refresh_token": first})
    assert rotated.status_code == 200

    # The original refresh token is single-use: replaying it must fail.
    replay = await client.post("/api/auth/refresh", json={"refresh_token": first})
    assert replay.status_code == 401


async def test_wrong_password_rejected(client: AsyncClient):
    await client.post(
        "/api/auth/register", json={"email": "x@example.com", "password": "supersecret123"}
    )
    bad = await client.post(
        "/api/auth/login", json={"email": "x@example.com", "password": "wrongpassword"}
    )
    assert bad.status_code == 401
