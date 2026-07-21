"""Advanced person filters, CSV export, statistics and the printable report payload."""
from __future__ import annotations

from httpx import AsyncClient

from .test_tree import _auth, _tenant_token
from .test_tree_manager import _person, _relative


async def _seed_family(client: AsyncClient, h: dict) -> dict[str, str]:
    antonio = await _person(client, h, "Antonio", "García", "M")
    carmen = await _relative(client, h, antonio, "spouse", given="Carmen", surname="Ruiz", sex="F")
    pedro = await _relative(client, h, antonio, "child", given="Pedro", surname="García", sex="M")
    for pid, typ, date, place in ((antonio, "birth", "1850", "Belmez"), (antonio, "death", "1910", "Belmez"),
                                  (carmen, "birth", "1855", "Córdoba"), (pedro, "birth", "1880", "Belmez")):
        r = await client.post(f"/api/tree/persons/{pid}/events",
                              json={"type": typ, "date_raw": date, "place": place}, headers=h)
        assert r.status_code == 200
    return {"antonio": antonio, "carmen": carmen, "pedro": pedro}


async def test_filters(client: AsyncClient):
    h = _auth(await _tenant_token(client))
    ids = await _seed_family(client, h)

    # sexo
    men = (await client.get("/api/tree/persons", params={"sex": "M"}, headers=h)).json()
    assert {p["id"] for p in men["items"]} == {ids["antonio"], ids["pedro"]}
    # rango de años de nacimiento
    r = (await client.get("/api/tree/persons", params={"year_from": 1852, "year_to": 1879}, headers=h)).json()
    assert [p["id"] for p in r["items"]] == [ids["carmen"]]
    # lugar
    places = (await client.get("/api/tree/places", params={"q": "belmez"}, headers=h)).json()
    belmez = places["items"][0]["id"]
    r = (await client.get("/api/tree/persons", params={"place_id": belmez}, headers=h)).json()
    assert {p["id"] for p in r["items"]} == {ids["antonio"], ids["pedro"]}
    # incompletos: sin padres (Antonio y Carmen; Pedro tiene) y sin fuentes (todos)
    r = (await client.get("/api/tree/persons", params={"missing": "parents"}, headers=h)).json()
    assert {p["id"] for p in r["items"]} == {ids["antonio"], ids["carmen"]}
    r = (await client.get("/api/tree/persons", params={"missing": "sources"}, headers=h)).json()
    assert r["total"] == 3
    # con una fuente sobre Antonio, deja de estar "sin fuentes"
    await client.post("/api/tree/citations", headers=h, json={
        "target_type": "person", "target_id": ids["antonio"], "note": "padrón"})
    r = (await client.get("/api/tree/persons", params={"missing": "sources"}, headers=h)).json()
    assert {p["id"] for p in r["items"]} == {ids["carmen"], ids["pedro"]}
    # combinado: sexo M + sin fuentes → solo Pedro
    r = (await client.get("/api/tree/persons", params={"missing": "sources", "sex": "M"}, headers=h)).json()
    assert [p["id"] for p in r["items"]] == [ids["pedro"]]


async def test_csv_export(client: AsyncClient):
    h = _auth(await _tenant_token(client))
    ids = await _seed_family(client, h)
    r = await client.get("/api/tree/persons.csv", headers=h)
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    lines = r.text.strip().splitlines()
    assert lines[0].startswith("id,nombre,apellidos,sexo,año_nacimiento")
    assert len(lines) == 4  # cabecera + 3 personas
    antonio_row = next(l for l in lines if ids["antonio"] in l)
    assert "Antonio" in antonio_row and "1850" in antonio_row and "Belmez" in antonio_row
    assert antonio_row.rstrip().endswith(",1,0") or ",1," in antonio_row  # 1 hijo

    # filtrado también en CSV
    r = await client.get("/api/tree/persons.csv", params={"sex": "F"}, headers=h)
    assert len(r.text.strip().splitlines()) == 2


async def test_statistics_and_report(client: AsyncClient):
    h = _auth(await _tenant_token(client))
    ids = await _seed_family(client, h)
    fam = (await client.get(f"/api/tree/persons/{ids['antonio']}/families", headers=h)).json()[0]
    await client.post(f"/api/tree/families/{fam['id']}/events",
                      json={"type": "marriage", "date_raw": "1878", "place": "Belmez"}, headers=h)

    s = (await client.get("/api/tree/statistics", headers=h)).json()
    assert s["totals"]["persons"] == 3
    assert {i["label"]: i["count"] for i in s["surnames"]} == {"García": 2, "Ruiz": 1}
    assert {i["label"]: i["count"] for i in s["birth_decades"]} == {"1850": 2, "1880": 1}
    assert s["lifespan_by_century"] == [{"century": 1800, "avg_years": 60.0, "count": 1}]
    assert s["sex"] == {"M": 2, "F": 1}
    assert s["avg_children_per_family"] == 1.0
    top_places = {i["label"]: i["count"] for i in s["places"]}
    assert top_places["Belmez"] == 4  # 3 hechos de persona + matrimonio

    rep = (await client.get(f"/api/tree/persons/{ids['antonio']}/report", headers=h)).json()
    assert rep["person"]["names"][0]["given"] == "Antonio"
    assert rep["families"][0]["events"][0]["type"] == "marriage"
    assert isinstance(rep["citations"], list)
