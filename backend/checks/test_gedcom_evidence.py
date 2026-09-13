from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import AsyncMock

from fastapi import HTTPException

from app.modules.tree import gedcom, gedcom_evidence as evidence


class GedcomEvidenceTests(unittest.IsolatedAsyncioTestCase):
    def citation(self, **changes):
        return SimpleNamespace(locator={"folio": "12v", "line_start": 4, "region": [0.1, 0.2, 0.3, 0.4]},
            quality="original", quote="Joan, fill legítim\n de Pere  ", note="Revisar el margen", target_type="name", **changes)

    def test_pointer_citation_keeps_locator_quote_note_and_quality_without_inventing_quay(self):
        original = self.citation()
        node = evidence.citation_node(original, "@S1@", 3)
        self.assertEqual(node.value, "@S1@")
        self.assertIn("folio: 12v", node.value_of("PAGE"))
        self.assertEqual(node.first("DATA").value_of("TEXT"), original.quote)
        self.assertEqual(node.value_of("NOTE"), original.note)
        self.assertIsNone(node.first("QUAY"))
        self.assertEqual(evidence.metadata(node, "_GS_CITATION")["locator"], original.locator)
        roots, _ = gedcom.parse(gedcom.serialize([node]).encode())
        self.assertEqual(evidence.metadata(roots[0], "_GS_CITATION")["quality"], "original")
        self.assertEqual(roots[0].first("DATA").value_of("TEXT"), original.quote)

    async def test_inline_citation_does_not_fabricate_a_source_or_promote_locator_to_quote(self):
        original = self.citation(); original.quote = None
        node = evidence.citation_node(original, None)
        self.assertIsNone(node.first("PAGE")); self.assertIsNone(node.first("DATA"))
        rows = []; session = SimpleNamespace(add=rows.append)
        await evidence.import_citations(session, uuid.uuid4(), gedcom.Node("INDI", children=[node]), "person", uuid.uuid4(), {})
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0].source_id); self.assertIsNone(rows[0].quote)
        self.assertEqual(rows[0].locator, original.locator)

    async def test_malformed_private_metadata_does_not_crash_or_set_arbitrary_quality(self):
        for value in ('{"version":1,"quality":{}}', 'null', '[]', '{', '{"version":true}', '{"version":99}'):
            node = gedcom.Node("SOUR", "Fuente", children=[gedcom.Node("_GS_CITATION", value)])
            rows = []; session = SimpleNamespace(add=rows.append, flush=AsyncMock())
            await evidence.import_citations(session, uuid.uuid4(), gedcom.Node("INDI", children=[node]), "person", uuid.uuid4(), {})
            session.flush.assert_awaited_once()
            self.assertIsNone(rows[-1].quality)

    def test_rejects_ambiguous_and_missing_bibliographic_pointers_before_import(self):
        for content in ("0 @I@ INDI\n0 @I@ INDI\n", "0 INDI\n", "0 @I@ INDI\n1 SOUR @missing@\n",
                        "0 @S@ SOUR\n1 REPO @missing@\n"):
            roots, _ = gedcom.parse(content.encode())
            with self.subTest(content=content), self.assertRaises(HTTPException): evidence.validate_import(roots)
        roots, _ = gedcom.parse(b"0 @I@ INDI\n1 SOUR @@literal@@\n")
        evidence.validate_import(roots)

    def test_folded_unicode_whitespace_at_signs_and_literal_pointers_roundtrip(self):
        text = "  " + "Pàgina àé漢字@ " * 90 + "  \n\n  segona línia  \n"
        roots = [gedcom.Node("HEAD"), gedcom.Node("NOTE", value=text),
                 gedcom.Node("SOUR", "@S1@"), gedcom.Node("TEXT", "@literal@"),
                 gedcom.Node("DATE", "@#DJULIAN@ 1 JAN 1800")]
        encoded = gedcom.serialize(roots)
        self.assertTrue(all(len(line) + 1 <= 255 for line in encoded.splitlines()))
        self.assertIn("CONC", encoded); self.assertIn("@@literal@@", encoded)
        parsed, _ = gedcom.parse(encoded.encode("utf-8"))
        self.assertEqual([r.value for r in parsed], [r.value for r in roots])
        self.assertTrue(parsed[2].is_pointer); self.assertFalse(parsed[3].is_pointer)
        self.assertEqual(gedcom.serialize([gedcom.from_dict(gedcom.to_dict(r)) for r in parsed]), encoded)

    def test_original_gedcom_page_text_is_not_reformatted(self):
        self.assertEqual(evidence.locator_text({"gedcom_page": "folio XII v.  "}, 7), "folio XII v.  ")
        self.assertEqual(evidence.locator_text(None, 7), "pág. 7")

    async def test_child_citation_keeps_original_note_and_target_on_reimport(self):
        original = self.citation(); original.target_type = "family_child"
        node = evidence.citation_node(original, "@S1@")
        node.children.append(gedcom.Node("NOTE", evidence.CHILD_NOTE))
        rows = []; session = SimpleNamespace(add=rows.append)
        await evidence.import_citations(session, uuid.uuid4(), gedcom.Node("INDI", children=[node]), "person", uuid.uuid4(),
                                  {"@S1@": SimpleNamespace(id=uuid.uuid4())})
        self.assertEqual(rows[0].target_type, "family_child")
        self.assertEqual(rows[0].note, original.note)

    def test_note_that_looks_like_a_pointer_remains_literal(self):
        original = self.citation(); original.note = "@N1@"
        encoded = gedcom.serialize([evidence.citation_node(original, "@S1@")])
        self.assertIn("NOTE @@N1@@", encoded)
        roots, _ = gedcom.parse(encoded.encode())
        self.assertEqual(roots[0].value_of("NOTE"), "@N1@")
        self.assertFalse(roots[0].first("NOTE").is_pointer)

    async def test_external_edits_to_standard_fields_are_not_overwritten_by_private_extensions(self):
        original = self.citation()
        node = evidence.citation_node(original, "@S1@")
        node.first("PAGE").value = "folio 99"
        node.first("DATA").first("TEXT").value = "Nueva lectura"
        node.first("NOTE").value = "Nueva nota"
        rows = []; session = SimpleNamespace(add=rows.append)
        await evidence.import_citations(session, uuid.uuid4(), gedcom.Node("INDI", children=[node]), "person", uuid.uuid4(),
                                  {"@S1@": SimpleNamespace(id=uuid.uuid4())})
        self.assertEqual(rows[0].quote, "Nueva lectura")
        self.assertEqual(rows[0].note, "Nueva nota")
        self.assertEqual(rows[0].locator["gedcom_page"], "folio 99")
        self.assertEqual(rows[0].locator["folio"], "12v")  # preserve the old context, don't invent a resolution
