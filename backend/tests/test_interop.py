"""The direct Gramps sync contract requires an explicit baseline and retains conflicts/tombstones."""
from __future__ import annotations

from httpx import AsyncClient

from .test_tree import _auth


async def _token(client: AsyncClient) -> dict[str, str]:
    reg = await client.post("/api/auth/register", json={"email": "sync@example.com", "password": "supersecret123"})
    initial = reg.json()["access_token"]
    tenant = await client.post("/api/tenants", headers=_auth(initial), json={"name": "Sincronización"})
    switched = await client.post(f"/api/auth/switch/{tenant.json()['id']}", headers=_auth(initial))
    return _auth(switched.json()["access_token"])


async def test_gramps_sync_requires_baseline_and_preserves_conflicts(client: AsyncClient):
    headers = await _token(client)
    connection = await client.post(
        "/api/interop/connections", headers=headers,
        json={"provider": "gramps_desktop", "name": "Portátil de Ana"},
    )
    assert connection.status_code == 201, connection.text
    connection_id = connection.json()["id"]
    body = {"changes": [{
        "source_system": "gramps_desktop", "object_type": "person", "object_key": "I0001",
        "remote_revision": "7", "payload": {"names": [{"given": "Ana"}]},
    }]}
    assert (await client.post(f"/api/interop/connections/{connection_id}/changes", headers=headers, json=body)).status_code == 409
    baseline = await client.post(
        f"/api/interop/connections/{connection_id}/baselines", headers=headers,
        json={"snapshot_hash": "a" * 64, "object_counts": {"person": 1}, "note": "Exportación revisada"},
    )
    assert baseline.status_code == 201, baseline.text
    first = await client.post(f"/api/interop/connections/{connection_id}/changes", headers=headers, json=body)
    assert first.status_code == 201, first.text
    assert first.json()[0]["state"] == "queued"

    conflict_body = {"changes": [{
        "source_system": "gramps_web", "object_type": "person", "object_key": "I0001",
        "remote_revision": "8", "payload": {"names": [{"given": "Anna"}]},
    }]}
    conflict = await client.post(f"/api/interop/connections/{connection_id}/changes", headers=headers, json=conflict_body)
    assert conflict.status_code == 201
    assert conflict.json()[0]["state"] == "conflict"
    inbox = await client.get(f"/api/interop/connections/{connection_id}/changes?state=conflict", headers=headers)
    assert len(inbox.json()) == 2
    resolved = await client.post(
        f"/api/interop/changes/{conflict.json()[0]['id']}/resolve", headers=headers,
        json={"resolution": "accept_remote", "note": "La web contiene la corrección documentada"},
    )
    assert resolved.status_code == 200 and resolved.json()["state"] == "applied"
    # The accepted remote edit is not merely an inbox marker: it is projected into the tree.
    people = await client.get("/api/tree/persons", headers=headers)
    assert people.status_code == 200
    assert people.json()["items"][0]["given"] == "Anna"

    tombstone = await client.post(f"/api/interop/connections/{connection_id}/changes", headers=headers, json={"changes": [{
        "source_system": "gramps_desktop", "object_type": "note", "object_key": "N0003", "operation": "tombstone",
    }]})
    assert tombstone.status_code == 201 and tombstone.json()[0]["state"] == "tombstone"
    applied = await client.post(f"/api/interop/connections/{connection_id}/apply", headers=headers)
    assert applied.status_code == 200
    assert applied.json()["tombstones"] == 1


async def test_gramps_sync_applies_a_family_after_its_people_and_exports_snapshot(client: AsyncClient):
    headers = await _token(client)
    connection = await client.post("/api/interop/connections", headers=headers, json={
        "provider": "gramps_web", "name": "Árbol compartido",
    })
    connection_id = connection.json()["id"]
    await client.post(f"/api/interop/connections/{connection_id}/baselines", headers=headers, json={
        "snapshot_hash": "b" * 64, "object_counts": {"person": 2, "family": 1},
    })
    changes = {"changes": [
        {"source_system": "gramps_web", "object_type": "family", "object_key": "F1",
         "payload": {"husband_key": "I1", "wife_key": "I2", "child_keys": []}},
        {"source_system": "gramps_web", "object_type": "person", "object_key": "I1",
         "payload": {"sex": "M", "names": [{"given": "Joan", "surname": "Serra", "primary": True}]}},
        {"source_system": "gramps_web", "object_type": "person", "object_key": "I2",
         "payload": {"sex": "F", "names": [{"given": "Maria", "surname": "Pons", "primary": True}]}},
    ]}
    ingested = await client.post(f"/api/interop/connections/{connection_id}/changes", headers=headers, json=changes)
    assert ingested.status_code == 201, ingested.text
    applied = await client.post(f"/api/interop/connections/{connection_id}/apply", headers=headers)
    assert applied.status_code == 200 and applied.json()["applied"] == 3
    snapshot = await client.get(f"/api/interop/connections/{connection_id}/snapshot", headers=headers)
    assert snapshot.status_code == 200
    family = next(item for item in snapshot.json()["objects"] if item["object_type"] == "family")
    assert family["object_key"] == "F1"
    assert family["payload"]["husband_key"] == "I1"


async def test_snapshot_assigns_a_stable_peer_key_before_a_companion_echo(client: AsyncClient):
    headers = await _token(client)
    person = await client.post("/api/tree/persons", headers=headers, json={"given": "Lluís", "surname": "Riera"})
    assert person.status_code == 200
    connection = await client.post("/api/interop/connections", headers=headers, json={
        "provider": "gramps_desktop", "name": "Equipo local",
    })
    connection_id = connection.json()["id"]
    await client.post(f"/api/interop/connections/{connection_id}/baselines", headers=headers, json={
        "snapshot_hash": "c" * 64, "object_counts": {"person": 1},
    })
    snapshot = await client.get(f"/api/interop/connections/{connection_id}/snapshot", headers=headers)
    item = next(row for row in snapshot.json()["objects"] if row["object_type"] == "person")
    assert item["object_key"].startswith("gensuite:")

    echo = await client.post(f"/api/interop/connections/{connection_id}/changes", headers=headers, json={"changes": [{
        "source_system": "gramps_desktop", "object_type": "person", "object_key": item["object_key"],
        "payload": {"names": [{"given": "Lluís", "surname": "Riera", "primary": True}]},
    }]})
    assert echo.status_code == 201, echo.text
    assert (await client.post(f"/api/interop/connections/{connection_id}/apply", headers=headers)).json()["applied"] == 1
    directory = await client.get("/api/tree/persons", headers=headers)
    assert directory.json()["total"] == 1
