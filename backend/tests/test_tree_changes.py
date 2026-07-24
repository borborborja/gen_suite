"""Change log + revert: every tree mutation is audited and reversible (all-or-nothing)."""
from __future__ import annotations

from httpx import AsyncClient

from .test_tree import _auth, _tenant_token
from .test_tree_manager import _person, _relative


async def _changes(client: AsyncClient, h: dict) -> list[dict]:
    r = await client.get("/api/tree/changes", headers=h)
    assert r.status_code == 200, r.text
    return r.json()["items"]


async def _revert(client: AsyncClient, h: dict, change_id: str):
    return await client.post(f"/api/tree/changes/{change_id}/revert", headers=h)


async def test_mutations_are_logged(client: AsyncClient):
    h = _auth(await _tenant_token(client))
    juan = await _person(client, h, "Juan", "Vega", "M")
    ev = (await client.post(f"/api/tree/persons/{juan}/events",
                            json={"type": "birth", "date_raw": "1850"}, headers=h)).json()["id"]
    await client.patch(f"/api/tree/events/{ev}", json={"type": "birth", "date_raw": "1851"}, headers=h)

    items = await _changes(client, h)
    actions = [c["action"] for c in items]
    assert actions[:3] == ["event_edit", "event_add", "person_create"]  # más reciente primero
    assert items[-1]["rows_count"] == 2  # persona + nombre
    assert items[0]["actor_email"] == "tree@example.com"

    detail = (await client.get(f"/api/tree/changes/{items[0]['id']}", headers=h)).json()
    assert detail["rows"][0]["table"] == "events"
    assert detail["rows"][0]["before"]["date_raw"] == "1850"
    assert detail["rows"][0]["after"]["date_raw"] == "1851"


async def test_revert_edit_delete_and_double_revert(client: AsyncClient):
    h = _auth(await _tenant_token(client))
    juan = await _person(client, h, "Juan", "Vega", "M")
    ev = (await client.post(f"/api/tree/persons/{juan}/events",
                            json={"type": "birth", "date_raw": "1850"}, headers=h)).json()["id"]
    await client.patch(f"/api/tree/events/{ev}", json={"type": "birth", "date_raw": "1860"}, headers=h)

    edit_change = (await _changes(client, h))[0]
    assert edit_change["action"] == "event_edit"
    assert (await _revert(client, h, edit_change["id"])).status_code == 200
    detail = (await client.get(f"/api/tree/persons/{juan}", headers=h)).json()
    assert [e["date_raw"] for e in detail["events"]] == ["1850"]

    # doble revert del mismo cambio → 409; revert del revert → vuelve a 1860
    assert (await _revert(client, h, edit_change["id"])).status_code == 409
    revert_change = (await _changes(client, h))[0]
    assert revert_change["action"] == "revert" and revert_change["revert_of"] == edit_change["id"]
    assert (await _revert(client, h, revert_change["id"])).status_code == 200
    detail = (await client.get(f"/api/tree/persons/{juan}", headers=h)).json()
    assert [e["date_raw"] for e in detail["events"]] == ["1860"]

    # revert de un borrado de hecho → el hecho resucita
    await client.delete(f"/api/tree/events/{ev}", headers=h)
    del_change = (await _changes(client, h))[0]
    assert del_change["action"] == "event_delete"
    assert (await _revert(client, h, del_change["id"])).status_code == 200
    detail = (await client.get(f"/api/tree/persons/{juan}", headers=h)).json()
    assert [e["date_raw"] for e in detail["events"]] == ["1860"]


async def test_revert_delete_person_restores_everything(client: AsyncClient):
    h = _auth(await _tenant_token(client))
    padre = await _person(client, h, "Antonio", "García", "M")
    hijo = await _relative(client, h, padre, "child", given="Pedro", surname="García", sex="M")
    await client.post(f"/api/tree/persons/{hijo}/events",
                      json={"type": "birth", "date_raw": "1880"}, headers=h)

    assert (await client.delete(f"/api/tree/persons/{hijo}", headers=h)).status_code == 200
    assert (await client.get(f"/api/tree/persons/{hijo}", headers=h)).status_code == 404

    del_change = (await _changes(client, h))[0]
    assert del_change["action"] == "person_delete"
    assert (await _revert(client, h, del_change["id"])).status_code == 200, "revert de persona borrada"

    detail = (await client.get(f"/api/tree/persons/{hijo}", headers=h)).json()
    assert detail["names"][0]["given"] == "Pedro"
    assert [e["date_raw"] for e in detail["events"] if not e.get("family_id")] == ["1880"]
    assert [p["id"] for p in detail["parents"]] == [padre]


async def test_merge_shared_family_regression_and_revert(client: AsyncClient):
    """Regresión del bug FamilyChild.id: fusionar dos hijos de la misma familia, y revertirlo."""
    h = _auth(await _tenant_token(client))
    padre = await _person(client, h, "Antonio", "García", "M")
    a = await _relative(client, h, padre, "child", given="Pedro", surname="García", sex="M")
    b = await _relative(client, h, padre, "child", given="Pero", surname="García", sex="M")

    r = await client.post(f"/api/tree/persons/{a}/merge", json={"dup_id": b}, headers=h)
    assert r.status_code == 200, r.text  # antes del fix: AttributeError FamilyChild.id
    assert (await client.get(f"/api/tree/persons/{b}", headers=h)).status_code == 404

    merge_change = (await _changes(client, h))[0]
    assert merge_change["action"] == "person_merge"
    assert (await _revert(client, h, merge_change["id"])).status_code == 200
    assert (await client.get(f"/api/tree/persons/{b}", headers=h)).status_code == 200
    padre_detail = (await client.get(f"/api/tree/persons/{padre}", headers=h)).json()
    assert len(padre_detail["children"]) == 2


async def test_revert_blocked_by_later_uncaptured_dependents(client: AsyncClient):
    """Revertir una creación cuyo objeto ganó dependientes después (cascade de BD) debe dar
    409, no destruirlos en silencio."""
    h = _auth(await _tenant_token(client))
    ana = await _person(client, h, "Ana", "Vega", "F")
    # cambio A: crea cónyuge (persona nueva + familia)
    beto = await _relative(client, h, ana, "spouse", given="Beto", surname="Sol", sex="M")
    change_a = (await _changes(client, h))[0]
    assert change_a["action"] == "relative_add"
    # cambio B: matrimonio sobre esa familia (dependiente NO capturado en A)
    fam = (await client.get(f"/api/tree/persons/{ana}/families", headers=h)).json()[0]
    r = await client.post(f"/api/tree/families/{fam['id']}/events",
                          json={"type": "marriage", "date_raw": "1900"}, headers=h)
    assert r.status_code == 200

    # revertir A borraría la familia y el cascade destruiría el matrimonio de B → 409
    assert (await _revert(client, h, change_a["id"])).status_code == 409
    # nada se ha tocado: Beto y el matrimonio siguen
    assert (await client.get(f"/api/tree/persons/{beto}", headers=h)).status_code == 200
    fams = (await client.get(f"/api/tree/persons/{ana}/families", headers=h)).json()
    assert fams[0]["events"][0]["type"] == "marriage"

    # si primero se deshace B (el matrimonio), entonces A ya es revertible
    change_b = (await _changes(client, h))[0]
    assert change_b["action"] == "family_event_add"
    assert (await _revert(client, h, change_b["id"])).status_code == 200
    assert (await _revert(client, h, change_a["id"])).status_code == 200
    assert (await client.get(f"/api/tree/persons/{beto}", headers=h)).status_code == 404


async def test_revert_conflict_and_isolation(client: AsyncClient):
    h = _auth(await _tenant_token(client))
    juan = await _person(client, h, "Juan", "Vega", "M")
    ev = (await client.post(f"/api/tree/persons/{juan}/events",
                            json={"type": "birth", "date_raw": "1850"}, headers=h)).json()["id"]
    await client.patch(f"/api/tree/events/{ev}", json={"type": "birth", "date_raw": "1860"}, headers=h)
    first_edit = (await _changes(client, h))[0]
    await client.patch(f"/api/tree/events/{ev}", json={"type": "birth", "date_raw": "1870"}, headers=h)

    # el estado actual (1870) ya no coincide con el after del primer edit (1860) → 409
    assert (await _revert(client, h, first_edit["id"])).status_code == 409

    # otro tenant no ve el historial ni puede revertir (RLS en change_log)
    reg = await client.post("/api/auth/register",
                            json={"email": "otro@example.com", "password": "supersecret123"})
    tok = reg.json()["access_token"]
    t = await client.post("/api/tenants", json={"name": "Otra"}, headers=_auth(tok))
    sw = await client.post(f"/api/auth/switch/{t.json()['id']}", headers=_auth(tok))
    h2 = _auth(sw.json()["access_token"])
    assert (await _changes(client, h2)) == []
    assert (await _revert(client, h2, first_edit["id"])).status_code == 404
