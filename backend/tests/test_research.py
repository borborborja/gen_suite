"""Research workspace: structured sources, claims and work are tenant-isolated."""
from __future__ import annotations

from httpx import AsyncClient

from .test_tree import _auth


async def _tenant_token(client: AsyncClient, email: str, tenant_name: str) -> str:
    registered = await client.post(
        "/api/auth/register", json={"email": email, "password": "supersecret123"}
    )
    token = registered.json()["access_token"]
    tenant = await client.post("/api/tenants", json={"name": tenant_name}, headers=_auth(token))
    switched = await client.post(f"/api/auth/switch/{tenant.json()['id']}", headers=_auth(token))
    return switched.json()["access_token"]


async def test_research_workspace_and_source_citations(client: AsyncClient):
    headers = _auth(await _tenant_token(client, "research@example.com", "Archivo familiar"))

    repository = await client.post(
        "/api/research/repositories",
        headers=headers,
        json={"name": "Archivo Histórico", "url": "https://archivo.example.test"},
    )
    assert repository.status_code == 201, repository.text

    source = await client.post(
        "/api/research/sources",
        headers=headers,
        json={
            "title": "Padrones de Belmez, 1880",
            "source_type": "census",
            "repository_id": repository.json()["id"],
            "call_number": "Caja 17, leg. 4",
        },
    )
    assert source.status_code == 201, source.text
    source_id = source.json()["id"]

    project = await client.post(
        "/api/research/projects",
        headers=headers,
        json={"title": "Reconstruir la rama Balsera"},
    )
    assert project.status_code == 201, project.text
    project_id = project.json()["id"]

    task = await client.post(
        "/api/research/tasks",
        headers=headers,
        json={"project_id": project_id, "title": "Buscar el padrón de 1880", "priority": "high"},
    )
    assert task.status_code == 201, task.text
    updated = await client.patch(
        f"/api/research/tasks/{task.json()['id']}", headers=headers, json={"status": "done"}
    )
    assert updated.status_code == 200 and updated.json()["status"] == "done"

    person = await client.post(
        "/api/tree/persons", headers=headers, json={"given": "Juan", "surname": "Balsera"}
    )
    person_id = person.json()["id"]
    assertion = await client.post(
        "/api/research/assertions",
        headers=headers,
        json={
            "subject_type": "person", "subject_id": person_id, "predicate": "residence",
            "value": {"place": "Belmez", "year": 1880}, "confidence": 75,
        },
    )
    assert assertion.status_code == 201, assertion.text

    citation = await client.post(
        "/api/tree/citations",
        headers=headers,
        json={
            "target_type": "person", "target_id": person_id, "source_id": source_id,
            "assertion_id": assertion.json()["id"], "locator": {"folio": "12v", "line_start": 4},
            "quote": "Juan Balsera, jornalero", "quality": "original",
        },
    )
    assert citation.status_code == 200, citation.text
    listed_citation = (await client.get(f"/api/tree/persons/{person_id}/citations", headers=headers)).json()[0]
    assert listed_citation["source_id"] == source_id
    assert listed_citation["source_title"] == "Padrones de Belmez, 1880"
    assert listed_citation["locator"]["folio"] == "12v"

    tag = await client.post(
        "/api/research/tags", headers=headers, json={"name": "pendiente", "color": "amber"}
    )
    assert tag.status_code == 201
    applied = await client.post(
        f"/api/research/tags/{tag.json()['id']}/objects",
        headers=headers,
        json={"target_type": "assertion", "target_id": assertion.json()["id"]},
    )
    assert applied.status_code == 204


async def test_research_workspace_is_tenant_isolated(client: AsyncClient):
    first = _auth(await _tenant_token(client, "first-research@example.com", "Primer archivo"))
    repository = await client.post(
        "/api/research/repositories", headers=first, json={"name": "Repositorio privado"}
    )
    source = await client.post(
        "/api/research/sources",
        headers=first,
        json={"title": "Fuente privada", "repository_id": repository.json()["id"]},
    )
    assert source.status_code == 201

    second = _auth(await _tenant_token(client, "second-research@example.com", "Segundo archivo"))
    assert (await client.get("/api/research/sources", headers=second)).json() == []
    cross_tenant = await client.post(
        "/api/research/sources", headers=second,
        json={"title": "No debe enlazar", "repository_id": repository.json()["id"]},
    )
    assert cross_tenant.status_code == 404
