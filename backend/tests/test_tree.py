from __future__ import annotations

from httpx import AsyncClient

from app.modules.tree import gedcom

SAMPLE = b"""0 HEAD
1 CHAR UTF-8
1 GEDC
2 VERS 5.5.1
0 @I1@ INDI
1 NAME Juan /Balsera/
1 SEX M
1 BIRT
2 DATE 1850
2 PLAC Belmez, Cordoba, Espana
1 _CUSTOM keep-me
0 @I2@ INDI
1 NAME Maria /Burgas/
1 SEX F
1 BIRT
2 DATE 1855
0 @I3@ INDI
1 NAME Pedro /Balsera/
1 SEX M
1 BIRT
2 DATE 1880
2 PLAC Belmez, Cordoba, Espana
0 @F1@ FAM
1 HUSB @I1@
1 WIFE @I2@
1 CHIL @I3@
1 MARR
2 DATE 1878
0 TRLR
"""


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _tenant_token(client: AsyncClient) -> str:
    reg = await client.post(
        "/api/auth/register", json={"email": "tree@example.com", "password": "supersecret123"}
    )
    tok = reg.json()["access_token"]
    tenant = await client.post("/api/tenants", json={"name": "Casa"}, headers=_auth(tok))
    switched = await client.post(
        f"/api/auth/switch/{tenant.json()['id']}", headers=_auth(tok)
    )
    return switched.json()["access_token"]


async def test_gedcom_import_query_and_roundtrip(client: AsyncClient):
    h = _auth(await _tenant_token(client))

    imp = await client.post(
        "/api/tree/import/gedcom",
        headers=h,
        files={"file": ("sample.ged", SAMPLE, "application/octet-stream")},
    )
    assert imp.status_code == 200, imp.text
    body = imp.json()
    assert body["individuals"] == 3
    assert body["families"] == 1
    assert body["places"] == 1  # "Belmez..." deduped across two BIRT events

    stats = (await client.get("/api/tree/stats", headers=h)).json()
    assert stats["persons"] == 3 and stats["families"] == 1 and stats["places"] == 1

    pedro = (await client.get("/api/tree/persons/search", params={"q": "Pedro"}, headers=h)).json()
    assert len(pedro) == 1
    pedro_id = pedro[0]["id"]

    sub = (
        await client.get(f"/api/tree/persons/{pedro_id}/subtree", params={"depth": 2}, headers=h)
    ).json()
    assert pedro_id in {p["id"] for p in sub["persons"]}
    assert len(sub["persons"]) == 3  # Pedro + both parents
    assert len(sub["families"]) == 1

    detail = (await client.get(f"/api/tree/persons/{pedro_id}", headers=h)).json()
    assert detail["names"][0]["surname"] == "Balsera"
    assert {p["surname"] for p in detail["parents"]} == {"Balsera", "Burgas"}

    # Round-trip: export, re-parse, counts preserved + custom tag survived.
    export = await client.get("/api/tree/export/gedcom", headers=h)
    assert export.status_code == 200
    roots, _ = gedcom.parse(export.text.encode("utf-8"))
    assert sum(1 for r in roots if r.tag == "INDI") == 3
    assert sum(1 for r in roots if r.tag == "FAM") == 1
    assert "_CUSTOM keep-me" in export.text  # unmapped tag preserved via raw


async def test_gedcom_import_is_tenant_isolated(client: AsyncClient):
    h = _auth(await _tenant_token(client))
    await client.post(
        "/api/tree/import/gedcom",
        headers=h,
        files={"file": ("sample.ged", SAMPLE, "application/octet-stream")},
    )
    # A second tenant (different user) sees an empty tree — RLS on genealogy tables.
    reg2 = await client.post(
        "/api/auth/register", json={"email": "other@example.com", "password": "supersecret123"}
    )
    tok2 = reg2.json()["access_token"]
    t2 = await client.post("/api/tenants", json={"name": "Otra"}, headers=_auth(tok2))
    s2 = (await client.post(f"/api/auth/switch/{t2.json()['id']}", headers=_auth(tok2))).json()[
        "access_token"
    ]
    stats2 = (await client.get("/api/tree/stats", headers=_auth(s2))).json()
    assert stats2 == {"persons": 0, "families": 0, "events": 0, "places": 0}
