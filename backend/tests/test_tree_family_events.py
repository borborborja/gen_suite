"""Family (couple) events: shared facts anchored on the Family, visible from both spouses."""
from __future__ import annotations

from httpx import AsyncClient

from .test_tree import _auth, _tenant_token
from .test_tree_manager import _person, _relative


async def test_family_events_lifecycle(client: AsyncClient):
    h = _auth(await _tenant_token(client))
    antonio = await _person(client, h, "Antonio", "García", "M")
    carmen = await _relative(client, h, antonio, "spouse", given="Carmen", surname="Ruiz", sex="F")

    fams = (await client.get(f"/api/tree/persons/{antonio}/families", headers=h)).json()
    assert len(fams) == 1
    fam = fams[0]
    assert fam["spouse"]["id"] == carmen and fam["children_count"] == 0

    r = await client.post(f"/api/tree/families/{fam['id']}/events",
                          json={"type": "marriage", "date_raw": "12 JUN 1878", "place": "Belmez"},
                          headers=h)
    assert r.status_code == 200, r.text
    ev_id = r.json()["id"]

    # visible en la línea de vida de AMBOS cónyuges, con family_id y nombre del otro
    for pid, other in ((antonio, "Carmen Ruiz"), (carmen, "Antonio García")):
        detail = (await client.get(f"/api/tree/persons/{pid}", headers=h)).json()
        marr = [e for e in detail["events"] if e["type"] == "marriage"]
        assert len(marr) == 1
        assert marr[0]["family_id"] == fam["id"]
        assert marr[0]["spouse_name"] == other
        assert marr[0]["date_year"] == 1878 and marr[0]["place"] == "Belmez"

    # edición y borrado con los endpoints genéricos de eventos
    r = await client.patch(f"/api/tree/events/{ev_id}", json={"type": "marriage", "date_raw": "1879"}, headers=h)
    assert r.status_code == 200
    fams = (await client.get(f"/api/tree/persons/{antonio}/families", headers=h)).json()
    assert fams[0]["events"][0]["date_year"] == 1879

    r = await client.delete(f"/api/tree/events/{ev_id}", headers=h)
    assert r.status_code == 200
    fams = (await client.get(f"/api/tree/persons/{antonio}/families", headers=h)).json()
    assert fams[0]["events"] == []


async def test_family_events_edge_cases(client: AsyncClient):
    h = _auth(await _tenant_token(client))
    a = await _person(client, h, "Ana", "Sola", "F")

    # sin familia → lista vacía; familia inexistente → 404
    assert (await client.get(f"/api/tree/persons/{a}/families", headers=h)).json() == []
    r = await client.post(f"/api/tree/families/{a}/events", json={"type": "marriage"}, headers=h)
    assert r.status_code == 404

    # fact-types ahora llevan scope y hay vocabulario de familia
    kinds = (await client.get("/api/tree/fact-types", headers=h)).json()
    scopes = {t["scope"] for t in kinds}
    assert scopes == {"person", "family"}
    assert any(t["key"] == "marriage" and t["scope"] == "family" for t in kinds)
