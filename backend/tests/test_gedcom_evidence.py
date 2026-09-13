"""Real PostgreSQL round trips. Uses the guarded, disposable integration database."""
import hashlib
import uuid

from sqlalchemy import select

from app.core.security import decode_token
from app.db.rls import set_rls_context
from app.db.session import SessionLocal
from app.models.citation import Citation
from app.models.family import Family, FamilyChild
from app.models.person import Person
from app.modules.tree import gedcom

from .test_research import _tenant_token
from .test_tree import _auth

RICH = ("""0 HEAD
1 GEDC
2 VERS 5.5.1
1 CHAR UTF-8
0 @ARCHIVE@ REPO
1 NAME Arxiu parroquial
1 ADDR Carrer Major 1
2 CONT Barcelona
1 WWW https://archive.example.test
1 NOTE Custodia del libro
0 @BOOK@ SOUR
1 TITL Llibre de bateigs — 1850
1 AUTH Parròquia
1 PUBL Barcelona, 1850
1 REPO @ARCHIVE@
2 CALN Llibre 7
1 NOTE Consulta de original
0 @UNUSED@ SOUR
1 TITL Fuente todavía no citada
0 @PARENT@ INDI
1 NAME Pere /Riera/
1 SEX M
0 @CHILD@ INDI
1 NAME Joan /Riera/
2 TYPE birth
2 SOUR @BOOK@
3 PAGE folio 12v, línea 4
3 DATA
4 TEXT Joan, fill de Pere
1 BIRT
2 DATE 1850
2 SOUR @BOOK@
3 PAGE folio 12v
3 DATA
4 TEXT nat el primer dia
1 SOUR @BOOK@
2 PAGE Índice, entrada 42
1 FAMC @FAMILY@
2 PEDI adopted
1 NOTE Nota personal
""" + "2 CONT  con sangría y espacios finales  \n" + """
0 @FAMILY@ FAM
1 HUSB @PARENT@
1 CHIL @CHILD@
1 SOUR @BOOK@
2 PAGE Familia, entrada 8
0 TRLR
""").encode("utf-8")


async def setup(client, suffix):
    token = await _tenant_token(client, f"gedcom-{suffix}@example.com", f"GEDCOM {suffix}")
    return _auth(token), uuid.UUID(decode_token(token, "access")["tid"])


async def upload(client, headers, data=RICH):
    return await client.post("/api/tree/import/gedcom", headers=headers,
                             files={"file": ("synthetic.ged", data, "text/plain")})


async def test_bibliography_citations_exact_targets_notes_and_pedigree_roundtrip(client):
    headers, tenant = await setup(client, "source")
    imported = await upload(client, headers)
    assert imported.status_code == 200, imported.text
    assert {k: imported.json()[k] for k in ("sources", "repositories", "citations")} == {
        "sources": 2, "repositories": 1, "citations": 4}
    sources = (await client.get("/api/research/sources", headers=headers)).json()
    book = next(s for s in sources if s["title"].startswith("Llibre"))
    assert book["call_number"] == "Llibre 7" and book["repository_name"] == "Arxiu parroquial"
    async with SessionLocal() as db:
        await set_rls_context(db, tenant_id=tenant)
        name_citation = await db.scalar(select(Citation).where(Citation.target_type == "name"))
        name_citation.locator = {"folio": "12v", "line_start": 4, "line_end": 6, "region": [0.1, 0.2, 0.5, 0.6]}
        name_citation.quality = "original"
        name_citation.note = "Corrección humana; no certificar la lectura"
        name_citation.quote = "@nom literal@ " + "Joan, fill legítim de Pere. " * 30 + "\n  segona línia  "
        expected_locator, expected_quote = name_citation.locator, name_citation.quote
        await db.commit()
    audit = await client.get("/api/tree/export/gedcom/audit", headers=headers)
    assert audit.status_code == 200, audit.text
    report = audit.json()
    assert report["counts"]["citations_exported"] == 4 and report["counts"]["sources"] == 2
    assert report["is_backup"] is False and report["unattached_citations"] == 0
    assert audit.headers["cache-control"] == "private, no-store"
    exported = await client.get("/api/tree/export/gedcom", params={"expected_sha256": report["sha256"]}, headers=headers)
    assert exported.status_code == 200, exported.text
    assert hashlib.sha256(exported.content).hexdigest() == report["sha256"]
    assert all(len(line) + 1 <= 255 for line in exported.text.splitlines())
    roots, _ = gedcom.parse(exported.content)
    child = next(n for n in roots if n.tag == "INDI" and n.value_of("NAME").startswith("Joan"))
    assert len(child.all("SOUR")) == 1  # name source stays on NAME, not on the person
    assert child.first("NAME").first("SOUR").first("DATA").value_of("TEXT") == expected_quote
    assert child.first("FAMC").value_of("PEDI") == "adopted"
    assert child.value_of("NOTE") == "Nota personal\n con sangría y espacios finales  "
    destinations = {n.xref for n in roots if n.xref}
    pending = list(roots)
    while pending:
        node = pending.pop()
        if node.is_pointer: assert node.value in destinations
        pending.extend(node.children)

    other, other_tenant = await setup(client, "target")
    imported_again = await upload(client, other, exported.content)
    assert imported_again.status_code == 200, imported_again.text
    assert imported_again.json()["citations"] == 4
    async with SessionLocal() as db:
        await set_rls_context(db, tenant_id=other_tenant)
        citation = await db.scalar(select(Citation).where(Citation.target_type == "name"))
        assert citation.locator == expected_locator and citation.quote == expected_quote
        assert citation.quality == "original" and citation.note == "Corrección humana; no certificar la lectura"
        assert (await db.scalar(select(FamilyChild))).relation == "adopted"
        assert {c.target_type for c in (await db.scalars(select(Citation))).all()} == {"person", "name", "event", "family"}
    next_export = await client.get("/api/tree/export/gedcom", headers=other)
    next_roots, _ = gedcom.parse(next_export.content)
    assert len([r for r in next_roots if r.tag == "SOUR"]) == 2
    assert len([r for r in next_roots if r.tag == "REPO"]) == 1


async def test_audit_is_private_tenant_scoped_and_changed_evidence_refuses_download(client):
    headers, _ = await setup(client, "private")
    assert (await upload(client, headers)).status_code == 200
    report = (await client.get("/api/tree/export/gedcom/audit", headers=headers)).json()
    # A new excluded record changes the loss report even if GEDCOM bytes are unchanged.
    project = await client.post("/api/research/projects", headers=headers, json={"title": "Investigación no exportable"})
    assert project.status_code == 201, project.text
    current = (await client.get("/api/tree/export/gedcom/audit", headers=headers)).json()
    assert current["sha256"] == report["sha256"] and current["review_token"] != report["review_token"]
    assert (await client.get("/api/tree/export/gedcom", headers=headers, params={
        "expected_sha256": report["sha256"], "expected_review_token": report["review_token"]})).status_code == 409
    person = (await client.get("/api/tree/persons/search?q=Joan", headers=headers)).json()[0]
    changed = await client.patch(f"/api/tree/persons/{person['id']}", headers=headers, json={"notes": "Nueva evidencia"})
    assert changed.status_code == 200
    assert (await client.get("/api/tree/export/gedcom", headers=headers,
                            params={"expected_sha256": report["sha256"]})).status_code == 409
    other, _ = await setup(client, "empty")
    empty = (await client.get("/api/tree/export/gedcom/audit", headers=other)).json()
    assert not any(empty["counts"].values()) and not any(empty["excluded_records"].values())
    assert "Llibre" not in (await client.get("/api/tree/export/gedcom", headers=other)).text
    assert (await client.get("/api/tree/export/gedcom/audit")).status_code == 401


async def test_bad_bibliography_and_unsupported_version_leave_no_partial_import(client):
    headers, _ = await setup(client, "rejected")
    for data in (RICH.replace(b"1 SOUR @BOOK@", b"1 SOUR @MISSING@"),
                 RICH.replace(b"0 @UNUSED@ SOUR", b"0 @BOOK@ SOUR"),
                 RICH.replace(b"2 VERS 5.5.1", b"2 VERS 7.0.18")):
        response = await upload(client, headers, data)
        assert response.status_code == 422, response.text
        assert (await client.get("/api/tree/stats", headers=headers)).json()["persons"] == 0
        assert (await client.get("/api/research/sources", headers=headers)).json() == []
        assert (await client.get("/api/research/repositories", headers=headers)).json() == []
    inline = await upload(client, headers, b"0 HEAD\n0 @I@ INDI\n1 NAME Ana /Riera/\n1 SOUR Entrevista familiar\n2 TEXT Declaracion literal\n0 TRLR\n")
    assert inline.status_code == 200, inline.text
    assert inline.json()["sources"] == 1 and inline.json()["citations"] == 1


async def test_legacy_unresolved_source_is_not_accidentally_rebound_to_new_source(client):
    headers, tenant = await setup(client, "legacy")
    assert (await upload(client, headers)).status_code == 200
    async with SessionLocal() as db:
        await set_rls_context(db, tenant_id=tenant)
        person = await db.scalar(select(Person).order_by(Person.id))
        person.gedcom_xref = "@S1@"  # an old source ID must never become an INDI pointer
        person.notes = "@N1@"  # a literal note must never become a dangling pointer
        person.raw = [{"tag": "SOUR", "value": "@S1@", "children": [{"tag": "PAGE", "value": "Fuente antigua distinta"}]}]
        await db.commit()
    report = (await client.get("/api/tree/export/gedcom/audit", headers=headers)).json()
    assert report["unresolved_raw_references"] == 1
    exported = await client.get("/api/tree/export/gedcom", headers=headers)
    assert "Estructura GEDCOM original con referencias no resueltas" in exported.text
    assert "Fuente antigua distinta" in exported.text
    assert "NOTE @@N1@@" in exported.text


async def test_note_only_and_ambiguous_child_citations_are_not_assigned_to_a_fake_source_or_family(client):
    headers, tenant = await setup(client, "child")
    assert (await upload(client, headers)).status_code == 200
    async with SessionLocal() as db:
        await set_rls_context(db, tenant_id=tenant)
        child = (await db.scalar(select(FamilyChild))).person_id
        second = Family(tenant_id=tenant); db.add(second); await db.flush()
        db.add(FamilyChild(tenant_id=tenant, family_id=second.id, person_id=child, relation="birth"))
        db.add(Citation(tenant_id=tenant, target_type="family_child", target_id=child,
                       quote="Filiación por revisar", note="Sin familia identificada", quality="uncertain"))
        await db.commit()
    exported = await client.get("/api/tree/export/gedcom", headers=headers)
    assert exported.status_code == 200, exported.text
    roots, _ = gedcom.parse(exported.content)
    person = next(n for n in roots if n.tag == "INDI" and n.value_of("NAME").startswith("Joan"))
    assert len(person.all("SOUR")) == 2
    assert sum(len(f.all("SOUR")) for f in roots if f.tag == "FAM") == 1
    report = (await client.get("/api/tree/export/gedcom/audit", headers=headers)).json()
    assert report["relocated_child_citations"] == 1 and report["counts"]["citations_exported"] == 5
    other, other_tenant = await setup(client, "child-copy")
    result = await upload(client, other, exported.content)
    assert result.status_code == 200, result.text
    async with SessionLocal() as db:
        await set_rls_context(db, tenant_id=other_tenant)
        citation = await db.scalar(select(Citation).where(Citation.target_type == "family_child"))
        assert citation.source_id is None and citation.quote == "Filiación por revisar"
        assert citation.note == "Sin familia identificada" and citation.quality == "uncertain"
