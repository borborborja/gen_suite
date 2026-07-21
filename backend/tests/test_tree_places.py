"""Place manager: directory, rename (collision), hierarchy (cycles), merge, geocode."""
from __future__ import annotations

from httpx import AsyncClient

from .test_tree import _auth, _tenant_token
from .test_tree_manager import _person


async def _seed(client: AsyncClient, h: dict) -> dict[str, str]:
    """Crea eventos con lugares → lugares deduplicados por tenant. Devuelve name→place_id."""
    juan = await _person(client, h, "Juan", "Vega", "M")
    for place, typ, date in (("Belmez", "birth", "1850"), ("Cordoba", "residence", "1870"),
                             ("España", "residence", "1880"), ("Belmez", "death", "1900")):
        r = await client.post(f"/api/tree/persons/{juan}/events",
                              json={"type": typ, "date_raw": date, "place": place}, headers=h)
        assert r.status_code == 200, r.text
    page = (await client.get("/api/tree/places", params={"page_size": 100}, headers=h)).json()
    return {it["name"]: it["id"] for it in page["items"]}


async def test_places_directory_rename_and_hierarchy(client: AsyncClient):
    h = _auth(await _tenant_token(client))
    ids = await _seed(client, h)
    assert set(ids) == {"Belmez", "Cordoba", "España"}

    page = (await client.get("/api/tree/places", params={"sort": "events", "order": "desc"}, headers=h)).json()
    assert page["total"] == 3
    assert page["items"][0]["name"] == "Belmez" and page["items"][0]["event_count"] == 2

    # renombrar con tilde
    r = await client.patch(f"/api/tree/places/{ids['Cordoba']}", json={"name": "Córdoba", "place_type": "province"}, headers=h)
    assert r.status_code == 200
    # renombrar a un nombre que ya existe → 409 con sugerencia de fusionar
    r = await client.patch(f"/api/tree/places/{ids['Belmez']}", json={"name": "España"}, headers=h)
    assert r.status_code == 409

    # jerarquía: Belmez → Córdoba → España
    assert (await client.patch(f"/api/tree/places/{ids['Belmez']}", json={"parent_id": ids["Cordoba"]}, headers=h)).status_code == 200
    assert (await client.patch(f"/api/tree/places/{ids['Cordoba']}", json={"parent_id": ids['España']}, headers=h)).status_code == 200
    detail = (await client.get(f"/api/tree/places/{ids['Belmez']}", headers=h)).json()
    assert [b["name"] for b in detail["breadcrumb"]] == ["España", "Córdoba"]
    assert detail["parent_name"] == "Córdoba" and detail["event_count"] == 2

    # ciclo: España no puede colgar de Belmez
    r = await client.patch(f"/api/tree/places/{ids['España']}", json={"parent_id": ids["Belmez"]}, headers=h)
    assert r.status_code == 400

    # eventos del lugar, con persona navegable
    evs = (await client.get(f"/api/tree/places/{ids['Belmez']}/events", headers=h)).json()
    assert evs["total"] == 2 and evs["items"][0]["person_name"] == "Juan Vega"

    # quitar el padre explícitamente
    assert (await client.patch(f"/api/tree/places/{ids['Belmez']}", json={"clear_parent": True}, headers=h)).status_code == 200
    assert (await client.get(f"/api/tree/places/{ids['Belmez']}", headers=h)).json()["parent_id"] is None


async def test_place_merge_and_geocode(client: AsyncClient, monkeypatch):
    h = _auth(await _tenant_token(client))
    ids = await _seed(client, h)
    ana = await _person(client, h, "Ana", "Sola", "F")
    r = await client.post(f"/api/tree/persons/{ana}/events",
                          json={"type": "birth", "date_raw": "1855", "place": "Belmez del Río"}, headers=h)
    assert r.status_code == 200
    page = (await client.get("/api/tree/places", params={"q": "belmez", "page_size": 50}, headers=h)).json()
    dup_id = next(it["id"] for it in page["items"] if it["name"] == "Belmez del Río")

    # fusionar el duplicado dentro de Belmez → sus eventos se repuntan
    r = await client.post(f"/api/tree/places/{dup_id}/merge", json={"into_id": ids["Belmez"]}, headers=h)
    assert r.status_code == 200
    detail = (await client.get(f"/api/tree/places/{ids['Belmez']}", headers=h)).json()
    assert detail["event_count"] == 3
    assert (await client.get(f"/api/tree/places/{dup_id}", headers=h)).status_code == 404
    # fusionar consigo mismo → 400
    assert (await client.post(f"/api/tree/places/{ids['Belmez']}/merge",
                              json={"into_id": ids["Belmez"]}, headers=h)).status_code == 400

    # geocodificar usando la jerarquía como contexto (sin red: monkeypatch)
    from app.modules.geo import router as geo_router

    async def fake_geocode(q: str):
        assert q.startswith("Belmez")
        return (38.269, -5.205)

    monkeypatch.setattr(geo_router, "geocode_one", fake_geocode)
    r = await client.post(f"/api/tree/places/{ids['Belmez']}/geocode", headers=h)
    assert r.status_code == 200 and abs(r.json()["lat"] - 38.269) < 1e-6

    async def fake_miss(q: str):
        return None

    monkeypatch.setattr(geo_router, "geocode_one", fake_miss)
    assert (await client.post(f"/api/tree/places/{ids['Cordoba']}/geocode", headers=h)).status_code == 422
