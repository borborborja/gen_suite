"""Tree-manager endpoints: person directory (list/sort/filter/pagination) and the
kinship calculator (Spanish relationship labels + step chain)."""
from __future__ import annotations

from httpx import AsyncClient

from .test_tree import SAMPLE, _auth, _tenant_token


async def _person(client: AsyncClient, h: dict, given: str, surname: str, sex: str) -> str:
    r = await client.post("/api/tree/persons", json={"given": given, "surname": surname, "sex": sex}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["id"]


async def _relative(client: AsyncClient, h: dict, person_id: str, relation: str, **body) -> str:
    r = await client.post(f"/api/tree/persons/{person_id}/relatives",
                          json={"relation": relation, **body}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["id"]


async def _rel_label(client: AsyncClient, h: dict, a: str, b: str) -> dict:
    r = await client.get("/api/tree/relationship", params={"a": a, "b": b}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


async def test_person_directory_sort_filter_pagination(client: AsyncClient):
    h = _auth(await _tenant_token(client))
    imp = await client.post("/api/tree/import/gedcom", headers=h,
                            files={"file": ("s.ged", SAMPLE, "application/octet-stream")})
    assert imp.status_code == 200, imp.text

    page = (await client.get("/api/tree/persons", headers=h)).json()
    assert page["total"] == 3 and len(page["items"]) == 3
    # default sort: surname asc → Balsera, Balsera, Burgas
    assert [p["surname"] for p in page["items"]] == ["Balsera", "Balsera", "Burgas"]

    by_birth = (await client.get("/api/tree/persons",
                                 params={"sort": "birth", "order": "desc"}, headers=h)).json()
    assert by_birth["items"][0]["birth_year"] == 1880  # Pedro, el más reciente

    filtered = (await client.get("/api/tree/persons", params={"surname": "balsera"}, headers=h)).json()
    assert filtered["total"] == 2

    paged = (await client.get("/api/tree/persons",
                              params={"page_size": 2, "page": 2}, headers=h)).json()
    assert paged["total"] == 3 and len(paged["items"]) == 1


async def test_kinship_blood_and_affinity(client: AsyncClient):
    h = _auth(await _tenant_token(client))

    # Tres generaciones: Antonio ∞ Carmen → Pedro y Luisa; Pedro ∞ María → Juan; Luisa → Ana.
    antonio = await _person(client, h, "Antonio", "García", "M")
    carmen = await _relative(client, h, antonio, "spouse", given="Carmen", surname="Ruiz", sex="F")
    pedro = await _relative(client, h, antonio, "child", given="Pedro", surname="García", sex="M")
    luisa = await _relative(client, h, antonio, "child", given="Luisa", surname="García", sex="F")
    maria = await _relative(client, h, pedro, "spouse", given="María", surname="López", sex="F")
    juan = await _relative(client, h, pedro, "child", given="Juan", surname="García", sex="M")
    ana = await _relative(client, h, luisa, "child", given="Ana", surname="Vega", sex="F")

    assert (await _rel_label(client, h, juan, antonio))["label"] == "abuelo"
    assert (await _rel_label(client, h, antonio, juan))["label"] == "nieto"
    assert (await _rel_label(client, h, juan, luisa))["label"] == "tía"
    assert (await _rel_label(client, h, luisa, juan))["label"] == "sobrino"
    assert (await _rel_label(client, h, juan, ana))["label"] == "prima hermana"
    assert (await _rel_label(client, h, pedro, luisa))["label"] == "hermana"
    assert (await _rel_label(client, h, juan, maria))["label"] == "madre"
    assert (await _rel_label(client, h, pedro, maria))["label"] == "esposa"
    # política: cuñada (hermana del cónyuge) y nuera (esposa del hijo)
    assert (await _rel_label(client, h, maria, luisa))["label"] == "cuñada"
    assert (await _rel_label(client, h, antonio, maria))["label"] == "nuera"

    # la cadena de pasos va de A a B y está etiquetada
    rel = await _rel_label(client, h, juan, antonio)
    assert rel["related"] is True
    assert [s["step"] for s in rel["path"]] == [None, "padre", "padre"]

    # sin ningún vínculo → no emparentados
    zoe = await _person(client, h, "Zoe", "Sola", "F")
    none = await _rel_label(client, h, juan, zoe)
    assert none["related"] is False and none["path"] == []


async def test_link_relative_slots_and_second_marriage(client: AsyncClient):
    """Slots de FAM correctos: sin auto-matrimonios, sin no-ops silenciosos, madre soltera
    en el slot wife, segundo matrimonio en familia nueva, y sexo en minúscula normalizado."""
    h = _auth(await _tenant_token(client))

    # madre soltera (sexo en minúscula → normalizado a F) + hijo → ella ocupa el slot wife
    madre = await _person(client, h, "Rosa", "Vega", "f")
    detail = (await client.get(f"/api/tree/persons/{madre}", headers=h)).json()
    assert detail["sex"] == "F"
    hijo = await _relative(client, h, madre, "child", given="Luis", surname="Vega", sex="M")
    sub = (await client.get(f"/api/tree/persons/{madre}/subtree", headers=h)).json()
    fam = next(f for f in sub["families"] if hijo in f["child_ids"])
    assert fam["wife_id"] == madre and fam["husband_id"] is None

    # añadir cónyuge a la madre: completa el hueco husband (nada de casarse consigo misma)
    esposo = await _relative(client, h, madre, "spouse", given="Blas", surname="Sol", sex="M")
    fams = (await client.get(f"/api/tree/persons/{madre}/families", headers=h)).json()
    assert len(fams) == 1 and fams[0]["spouse"]["id"] == esposo

    # segundo cónyuge → familia NUEVA (antes: no-op silencioso con 200)
    esposo2 = await _relative(client, h, madre, "spouse", given="Otto", surname="Dos", sex="M")
    fams = (await client.get(f"/api/tree/persons/{madre}/families", headers=h)).json()
    assert len(fams) == 2
    assert {f["spouse"]["id"] for f in fams} == {esposo, esposo2}

    # plaza de padre ya ocupada → 409 explícito (antes: padre acababa en el slot de madre)
    r = await client.post(f"/api/tree/persons/{hijo}/relatives",
                          json={"relation": "mother", "given": "Impostora", "surname": "X", "sex": "F"},
                          headers=h)
    assert r.status_code == 409

    # una persona no puede ser pariente de sí misma
    r = await client.post(f"/api/tree/persons/{madre}/relatives",
                          json={"relation": "spouse", "relative_id": madre}, headers=h)
    assert r.status_code == 400


async def test_add_relative_links_existing_person(client: AsyncClient):
    """El diálogo «vincular existente» usa relative_id: no debe crear personas nuevas."""
    h = _auth(await _tenant_token(client))
    a = await _person(client, h, "Padre", "Uno", "M")
    b = await _person(client, h, "Hijo", "Uno", "M")
    linked = await _relative(client, h, a, "child", relative_id=b)
    assert linked == b
    stats = (await client.get("/api/tree/stats", headers=h)).json()
    assert stats["persons"] == 2  # no se creó una tercera
    detail = (await client.get(f"/api/tree/persons/{a}", headers=h)).json()
    assert [c["id"] for c in detail["children"]] == [b]
