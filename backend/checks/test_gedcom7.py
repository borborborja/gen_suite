import hashlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch
import zipfile

from app.modules.tree import export_bundle, gedcom7
from app.modules.tree.gedcom import Node
from app.modules.tree.gedcom_evidence import extension


def graph(children):
    return [Node("HEAD"), Node("SUBM", xref="@U1@", children=[Node("NAME", "gen_suite")]),
            Node("INDI", xref="@I1@", children=children), Node("TRLR")]


class Gedcom7Tests(unittest.TestCase):
    def test_dates_keep_precision_validate_days_and_never_guess(self):
        for value in ("1850", "FEB 1850", "29 FEB 2000", "JULIAN 29 FEB 1900", "ABT 1850",
                      "BET 1850 AND 1852", "FROM 1 JAN 1850 TO 1852", "TO 1850", "1 JAN 44 BCE"):
            with self.subTest(value=value): self.assertTrue(gedcom7.date_value(value))
        for value in ("29 FEB 1900", "31 APR 1850", "0 JAN 1850", "13 1850", "0", "1850-01-01",
                      "INT 1850 (interpretada)", "@#DHEBREW@ 1 TSH 5600", "BET 1850 AND 1851 AND 1852",
                      "(hacia Navidad)", " 1850", "1 JAN 0000", "BET 1850 AND"):
            with self.subTest(value=value): self.assertFalse(gedcom7.date_value(value))

    def test_v7_escaping_multiline_unicode_and_no_conc(self):
        value = "@I1@ literal a@b @@c@@ " + "à漢字 " * 200 + "\n@inicio\n  final  \n"
        nodes, _ = gedcom7.convert(graph([Node("NOTE", value, is_pointer=False)]))
        output = gedcom7.serialize(nodes)
        self.assertIn("1 NOTE @@I1@ literal a@b @@c@@", output)
        self.assertIn("2 CONT @@inicio\n2 CONT   final  \n2 CONT\n", output)
        self.assertNotIn("CONC", output); self.assertNotIn("CHAR", output)
        self.assertIn("2 VERS 7.0\n", output)

    def test_dates_names_pedigree_and_legacy_have_explicit_semantics(self):
        raw = {"tag": "_UNKNOWN", "value": "@foreign@"}
        nodes = graph([Node("NAME", "Joan /Riera/", children=[Node("TYPE", "religioso")]),
            Node("BIRT", children=[Node("DATE", "(Navidad de 1850)")]),
            extension("_GS_LEGACY", structure=raw)])
        result, changes = gedcom7.convert(nodes)
        self.assertEqual(changes["date_phrases"], 1)
        self.assertEqual(changes["name_type_phrases"], 1)
        self.assertEqual(changes["legacy_structures"], 1)
        output = gedcom7.serialize(result)
        self.assertIn("2 TYPE OTHER\n3 PHRASE religioso", output)
        self.assertIn("2 DATE\n3 PHRASE (Navidad de 1850)", output)
        self.assertIn("2 TAG _GS_LEGACY urn:uuid:", output)
        self.assertEqual(nodes[2].first("NAME").value_of("TYPE"), "religioso")  # no input mutation

    def test_missing_source_uses_void_and_quote_stays_data(self):
        nodes, _ = gedcom7.convert(graph([Node("SOUR", "Fuente no identificada", children=[Node("TEXT", "Lectura literal")])]))
        output = gedcom7.serialize(nodes)
        self.assertIn("1 SOUR @VOID@\n2 NOTE Fuente no identificada\n2 DATA\n3 TEXT Lectura literal", output)

    def test_serializer_rejects_dangling_wrong_type_controls_and_reserved_xrefs(self):
        for node in [Node("SOUR", "@MISSING@", is_pointer=True), Node("SOUR", "@I1@", is_pointer=True),
                     Node("NOTE", "NUL\x00"), Node("NOTE", "DEL\x7f"), Node("NOTE", "C1\x85"), Node("NOTE", "INVALID\uffff")]:
            with self.subTest(node=node), self.assertRaises(ValueError):
                gedcom7.serialize(gedcom7.convert(graph([node]))[0])
        nodes = graph([]); nodes[2].xref = "@VOID@"
        with self.assertRaises(ValueError): gedcom7.serialize(gedcom7.convert(nodes)[0])


class BundleTests(unittest.IsolatedAsyncioTestCase):
    def item(self, payload):
        return {"bucket": "private", "key": "own/key", "sha256": hashlib.sha256(payload).hexdigest(),
            "mime": "image/jpeg", "size": len(payload), "filename": "../../nom % à.jpg", "document_id": "id",
            "path": "media/id/1.jpg"}

    async def test_zip_contains_exact_original_bytes_and_manifest_not_untrusted_paths(self):
        payload = b"original" * 100
        body = io.BytesIO(payload)
        with TemporaryDirectory() as tmp, patch.object(export_bundle.storage, "open_object", AsyncMock(return_value={"Body": body, "ContentLength": len(payload)})):
            path = Path(tmp) / "export.gdz"
            report = {"is_backup": False}
            await export_bundle.write_bundle(path, "0 HEAD\n0 TRLR\n", report, [self.item(payload)], AsyncMock())
            with zipfile.ZipFile(path) as archive:
                self.assertEqual(set(archive.namelist()), {"gedcom.ged", "media/id/1.jpg", "gen-suite/media-manifest.json", "gen-suite/interchange-report.json"})
                self.assertEqual(archive.read("media/id/1.jpg"), payload)
                manifest = json.loads(archive.read("gen-suite/media-manifest.json"))
                self.assertEqual(manifest[0]["filename"], "../../nom % à.jpg")
                self.assertNotIn("key", manifest[0]); self.assertNotIn("bucket", manifest[0])
                self.assertEqual(hashlib.sha256(archive.read("gen-suite/media-manifest.json")).hexdigest(), report["media_manifest_sha256"])
        self.assertTrue(body.closed)

    async def test_missing_truncated_changed_or_oversized_content_never_completes(self):
        for payload, size, expected in [(b"bad", 3, b"yes"), (b"x", 3, b"yes"), (b"abcd", 3, b"abc"), (b"x", None, b"x")]:
            body = io.BytesIO(payload)
            with self.subTest(payload=payload, size=size), TemporaryDirectory() as tmp, patch.object(export_bundle.storage, "open_object", AsyncMock(return_value={"Body": body, "ContentLength": size})):
                with self.assertRaises(export_bundle.ExportError):
                    await export_bundle.write_bundle(Path(tmp) / "result", "0 HEAD\n", {}, [self.item(expected)], AsyncMock())
            self.assertTrue(body.closed)

    async def test_reads_are_bounded_and_cancellation_closes_body(self):
        class Body(io.BytesIO):
            def read(self, size=-1):
                if not 0 < size <= export_bundle.CHUNK: raise AssertionError("unbounded read")
                return super().read(size)
        payload = b"x" * (export_bundle.CHUNK + 1)
        body = Body(payload)
        with TemporaryDirectory() as tmp, patch.object(export_bundle.storage, "open_object", AsyncMock(return_value={"Body": body, "ContentLength": len(payload)})):
            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                await export_bundle.write_bundle(Path(tmp) / "result", "0 HEAD\n", {}, [self.item(payload)],
                    AsyncMock(side_effect=[None, None, RuntimeError("cancelled")]))
        self.assertTrue(body.closed)
