from __future__ import annotations

import uuid

from httpx import AsyncClient

from app.db.rls import set_rls_context
from app.db.session import SessionLocal
from app.modules.providers.service import ProviderService


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _two_tenants(client: AsyncClient) -> tuple[str, str]:
    # First registered user is the server-admin; the second is a plain tenant user.
    await client.post("/api/auth/register", json={"email": "root@example.com", "password": "supersecret123"})
    reg = await client.post("/api/auth/register", json={"email": "u@example.com", "password": "supersecret123"})
    tok = reg.json()["access_token"]
    t = await client.post("/api/tenants", json={"name": "Casa"}, headers=_auth(tok))
    tid = t.json()["id"]
    sw = await client.post(f"/api/auth/switch/{tid}", headers=_auth(tok))
    return sw.json()["access_token"], tid


async def test_credential_is_encrypted_masked_and_resolvable(client: AsyncClient):
    token, tenant_id = await _two_tenants(client)
    h = _auth(token)

    created = await client.post(
        "/api/providers/credentials",
        headers=h,
        json={"scope": "tenant", "provider_key": "openai", "label": "Mi OpenAI", "api_key": "sk-test-abcd1234"},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert "api_key" not in body  # plaintext key never returned
    assert body["key_masked"] == "••••1234"
    cred_id = body["id"]

    listed = (await client.get("/api/providers/credentials", headers=h)).json()
    assert len(listed) == 1 and listed[0]["provider_key"] == "openai"

    binding = await client.put(
        "/api/providers/bindings",
        headers=h,
        json={"task_type": "transcription", "credential_id": cred_id},
    )
    assert binding.status_code == 200

    # Resolution (worker path) returns the decrypted key + catalog default model.
    async with SessionLocal() as session:
        await set_rls_context(session, tenant_id=uuid.UUID(tenant_id))
        rc = await ProviderService(session).resolve(
            tenant_id=uuid.UUID(tenant_id), task_type="transcription"
        )
    assert rc.engine == "openai"
    assert rc.api_key == "sk-test-abcd1234"
    assert rc.model == "gpt-4o-mini"

    # An inline override (e.g. local engine) needs no stored credential.
    async with SessionLocal() as session:
        await set_rls_context(session, tenant_id=uuid.UUID(tenant_id))
        rc2 = await ProviderService(session).resolve(
            tenant_id=uuid.UUID(tenant_id), task_type="transcription", override={"engine": "tesseract"}
        )
    assert rc2.engine == "tesseract" and rc2.api_key is None


async def test_keyless_provider_needs_no_key(client: AsyncClient):
    token, _ = await _two_tenants(client)
    resp = await client.post(
        "/api/providers/credentials",
        headers=_auth(token),
        json={"scope": "tenant", "provider_key": "ollama", "label": "Local"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["key_masked"] is None


async def test_server_credential_requires_server_admin(client: AsyncClient):
    token, _ = await _two_tenants(client)  # this token is a tenant_admin, NOT server-admin
    resp = await client.post(
        "/api/providers/credentials",
        headers=_auth(token),
        json={"scope": "server", "provider_key": "openai", "label": "Shared", "api_key": "sk-x-1234"},
    )
    assert resp.status_code == 403


async def test_openai_requires_key(client: AsyncClient):
    token, _ = await _two_tenants(client)
    resp = await client.post(
        "/api/providers/credentials",
        headers=_auth(token),
        json={"scope": "tenant", "provider_key": "openai", "label": "No key"},
    )
    assert resp.status_code == 400
