"""Consistency checker: each rule fires exactly where the seeded anomaly is."""
from __future__ import annotations

from httpx import AsyncClient

from .test_tree import _auth, _tenant_token
from .test_tree_manager import _person, _relative


async def _event(client: AsyncClient, h: dict, pid: str, typ: str, date: str):
    r = await client.post(f"/api/tree/persons/{pid}/events",
                          json={"type": typ, "date_raw": date}, headers=h)
    assert r.status_code == 200, r.text


async def test_consistency_rules(client: AsyncClient):
    h = _auth(await _tenant_token(client))

    # nacido después de morir
    error_p = await _person(client, h, "Erróneo", "Uno", "M")
    await _event(client, h, error_p, "birth", "1900")
    await _event(client, h, error_p, "death", "1890")

    # vivo con más de 110 años
    old_p = await _person(client, h, "Matusalén", "Dos", "M")
    await _event(client, h, old_p, "birth", "1850")

    # familia con anomalías: madre demasiado joven + hijo tras la muerte de la madre +
    # hijo antes del matrimonio + casada demasiado joven
    madre = await _person(client, h, "Joven", "Tres", "F")
    await _event(client, h, madre, "birth", "1880")
    await _event(client, h, madre, "death", "1902")
    padre = await _relative(client, h, madre, "spouse", given="Padre", surname="Tres", sex="M")
    await _event(client, h, padre, "birth", "1860")
    hijo1 = await _relative(client, h, madre, "child", given="Hijo", surname="Tres", sex="M")
    await _event(client, h, hijo1, "birth", "1890")  # madre con 10 años + antes del matrimonio
    hijo2 = await _relative(client, h, madre, "child", given="Póstumo", surname="Tres", sex="M")
    await _event(client, h, hijo2, "birth", "1905")  # tras la muerte de la madre (1902)
    fam = (await client.get(f"/api/tree/persons/{madre}/families", headers=h)).json()[0]
    r = await client.post(f"/api/tree/families/{fam['id']}/events",
                          json={"type": "marriage", "date_raw": "1891"}, headers=h)
    assert r.status_code == 200  # casada en 1891 con 11 años

    res = (await client.get("/api/tree/consistency", headers=h)).json()
    counts = res["counts"]
    by_code: dict[str, list] = {}
    for i in res["issues"]:
        by_code.setdefault(i["code"], []).append(i)

    assert counts["birth_after_death"] == 1
    assert by_code["birth_after_death"][0]["person_id"] == error_p

    # todos los sembrados sin defunción y nacidos en el XIX disparan la regla
    assert counts["alive_over_110"] == 4
    assert old_p in {i["person_id"] for i in by_code["alive_over_110"]}

    assert counts["parent_too_young"] == 1  # madre con 10 años
    assert by_code["parent_too_young"][0]["person_id"] == madre
    assert by_code["parent_too_young"][0]["related_person_id"] == hijo1

    assert counts["child_after_mother_death"] == 1
    assert by_code["child_after_mother_death"][0]["person_id"] == hijo2

    assert counts["child_before_marriage"] == 1
    assert by_code["child_before_marriage"][0]["person_id"] == hijo1

    assert counts["spouse_too_young"] == 1
    assert by_code["spouse_too_young"][0]["person_id"] == madre

    # los errores van antes que los avisos y los mensajes llevan nombre
    severities = [i["severity"] for i in res["issues"]]
    assert severities == sorted(severities, key=lambda s: s != "error")
    assert any(i["message"].startswith("Erróneo Uno:") for i in res["issues"])


async def test_consistency_clean_tree(client: AsyncClient):
    h = _auth(await _tenant_token(client))
    p = await _person(client, h, "Normal", "Uno", "M")
    await _event(client, h, p, "birth", "1900")
    await _event(client, h, p, "death", "1980")
    res = (await client.get("/api/tree/consistency", headers=h)).json()
    assert res["issues"] == [] and res["counts"] == {}
