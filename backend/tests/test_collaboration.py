"""Workspace roles, proposals and redacted unlisted shares."""
from __future__ import annotations

from httpx import AsyncClient

from .test_tree import _auth


async def _owner_token(client: AsyncClient) -> str:
    registered = await client.post(
        "/api/auth/register", json={"email": "owner@example.com", "password": "supersecret123"}
    )
    initial = registered.json()["access_token"]
    tenant = await client.post("/api/tenants", headers=_auth(initial), json={"name": "Casa compartida"})
    switched = await client.post(f"/api/auth/switch/{tenant.json()['id']}", headers=_auth(initial))
    return switched.json()["access_token"]


async def test_contributor_proposals_are_reviewable_and_cannot_edit_tree(client: AsyncClient):
    owner = _auth(await _owner_token(client))
    await client.post(
        "/api/auth/register", json={"email": "contributor@example.com", "password": "supersecret123"}
    )
    added = await client.post(
        "/api/tenants/members", headers=owner,
        json={"email": "contributor@example.com", "role": "contributor"},
    )
    assert added.status_code == 201, added.text
    login = await client.post(
        "/api/auth/login", json={"email": "contributor@example.com", "password": "supersecret123"}
    )
    contributor = _auth(login.json()["access_token"])

    direct = await client.post("/api/tree/persons", headers=contributor, json={"given": "No directa"})
    assert direct.status_code == 403
    proposal = await client.post(
        "/api/proposals", headers=contributor,
        json={"operation": "create_person", "patch": {"given": "Propuesta", "surname": "Balsera"}},
    )
    assert proposal.status_code == 201, proposal.text
    reviewed = await client.post(
        f"/api/proposals/{proposal.json()['id']}/review", headers=owner,
        json={"status": "accepted", "reviewer_note": "La aplicaré tras comprobar la fuente"},
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["status"] == "accepted"
    created = await client.get("/api/tree/persons/search", headers=owner, params={"q": "Propuesta"})
    assert len(created.json()) == 1


async def test_unlisted_publication_omits_nonpublic_people(client: AsyncClient):
    owner = _auth(await _owner_token(client))
    historical = await client.post(
        "/api/tree/persons", headers=owner, json={"given": "Antoni", "surname": "Vidal"}
    )
    living = await client.post(
        "/api/tree/persons", headers=owner, json={"given": "Persona", "surname": "Viva"}
    )
    historical_id, living_id = historical.json()["id"], living.json()["id"]
    marked = await client.patch(
        f"/api/tree/persons/{historical_id}", headers=owner, json={"privacy_status": "deceased"}
    )
    assert marked.status_code == 200, marked.text

    draft = await client.post(
        "/api/publications", headers=owner,
        json={"title": "Rama Vidal", "person_ids": [historical_id, living_id]},
    )
    assert draft.status_code == 201, draft.text
    data = draft.json()
    # A stale draft is rebuilt at publication time; changing visibility therefore cannot leak a
    # person that was historical when the draft was prepared.
    assert (await client.patch(
        f"/api/tree/persons/{historical_id}", headers=owner, json={"privacy_status": "living"}
    )).status_code == 200
    assert (await client.post(f"/api/publications/{data['id']}/publish", headers=owner)).status_code == 422
    assert (await client.patch(
        f"/api/tree/persons/{historical_id}", headers=owner, json={"privacy_status": "deceased"}
    )).status_code == 200
    published = await client.post(f"/api/publications/{data['id']}/publish", headers=owner)
    assert published.status_code == 200

    shared = await client.get(f"/api/public/share/{data['slug']}/{data['access_token']}")
    assert shared.status_code == 200, shared.text
    people = shared.json()["payload"]["persons"]
    assert [person["given"] for person in people] == ["Antoni"]
    assert shared.json()["payload"]["redacted_people"] == 1
    safer = await client.post(f"/api/public/share/{data['slug']}", json={"token": data["access_token"]})
    assert safer.status_code == 200
    assert safer.headers["cache-control"] == "no-store"
    assert safer.headers["x-robots-tag"].startswith("noindex")
    assert (await client.get(f"/api/public/share/{data['slug']}/incorrecte")).status_code == 404

    revoked = await client.post(f"/api/publications/{data['id']}/revoke", headers=owner)
    assert revoked.status_code == 200
    assert (await client.get(f"/api/public/share/{data['slug']}/{data['access_token']}")).status_code == 404
