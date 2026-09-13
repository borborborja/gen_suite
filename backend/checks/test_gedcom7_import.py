import io
import json
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
import unittest
import uuid
from unittest.mock import patch
import zipfile

from PIL import Image
from app.modules.tree import gedcom7, gedcom7_import as g, import_bundle as bundle
from app.modules.tree.gedcom import Node


def data(body=""):
    return ("0 HEAD\n1 GEDC\n2 VERS 7.0\n" + body + "0 TRLR\n").encode()


class ParseTests(unittest.TestCase):
    def test_date_index_does_not_infer_gregorian_year_from_calendar_or_phrase(self):
        from app.modules.tree.importer import _make_event
        for raw, year in (("1850", 1850), ("ABT 1850", 1850), ("JULIAN 1 JAN 1850", None),
                          ("HEBREW 1 TSH 5600", None), ("1 JAN 44 BCE", None), ("Navidad de 1850", None)):
            with self.subTest(raw=raw):
                event = _make_event(uuid.uuid4(), Node("BIRT", children=[Node("DATE", raw)]), {}, version="7.0")
                self.assertEqual(event.date_raw, raw)
                self.assertEqual(event.date_year, year)
    def test_round_trip_v7_escaping_and_empty_date_phrase(self):
        text = "@start a@b @@same@@\n  spaced  \n"
        nodes = [Node("HEAD"), Node("SUBM", xref="@U1@", children=[Node("NAME", "Test")]), Node("INDI", xref="@I1@", children=[Node("NOTE", text), Node("BIRT", children=[Node("DATE", "(Navidad)")])]), Node("TRLR")]
        output = gedcom7.serialize(gedcom7.convert(nodes)[0]).encode()
        parsed = g.parse(output)
        indi = next(n for n in parsed if n.tag == "INDI")
        self.assertEqual(indi.value_of("NOTE"), text)
        projected, report = g.project(parsed)
        self.assertEqual(next(n for n in projected if n.tag == "INDI").first("BIRT").value_of("DATE"), "(Navidad)")
        self.assertEqual(report["change_counts"]["date_phrase_used_as_raw_date"], 1)

    def test_strict_syntax_and_references_fail_before_projection(self):
        for body in ("0 @I@ INDI\n1 NAME @bad\n", "0 @I@ INDI\n2 NAME X\n", "0 @I@ INDI\n1 NOTE x\n2 CONC y\n",
                     "0 @I@ INDI\n1 SOUR @MISSING@\n", "0 @I@ INDI\n1 SOUR @I@\n", "0 @I@ INDI\n1 NAME X\n0 @I@ INDI\n1 NAME Y\n",
                     "0 @VOID@ INDI\n1 NAME X\n", "0 @I@ INDI\n1 NOTE\n", "\n", "0 @I@ INDI\n1 NOTE a\n2 LANG ca\n2 CONT b\n"):
            with self.subTest(body=body), self.assertRaises(g.ImportError): g.parse(data(body))
        for payload in (data()[:-1], data().replace(b"7.0", b"5.5.1"), data() + b"0 TRLR\n", b"\xff" + data()):
            with self.subTest(payload=payload), self.assertRaises(g.ImportError): g.parse(payload)

    def test_bounded_depth_nodes_bytes_and_empty_first_line(self):
        payload = data("0 @I@ INDI\n1 NOTE \n2 CONT text\n")
        self.assertEqual(g.parse(payload)[1].value_of("NOTE"), "\ntext")
        for limits in ({"max_bytes": 3}, {"max_nodes": 2}, {"max_depth": 1}):
            with self.assertRaises(g.ImportError): g.parse(payload, **limits)

    def test_alias_by_uri_not_spelling_and_missing_source(self):
        payload = data(f"1 SCHMA\n2 TAG _ALIAS {gedcom7.EXTENSIONS['_GS_SOURCE']}\n0 @S@ SOUR\n1 TITL Book\n1 _ALIAS {{\"version\":1,\"source_type\":\"manuscript\"}}\n0 @I@ INDI\n1 NAME X\n1 SOUR @VOID@\n2 DATA\n3 TEXT literal\n2 _GS_CITATION {{\"version\":1,\"quality\":\"forged\"}}\n")
        projected, report = g.project(g.parse(payload))
        self.assertIsNotNone(projected[1].first("_GS_SOURCE"))
        citation = projected[2].first("SOUR")
        self.assertIsNotNone(citation.first("_IMPORTED_GS_CITATION"))
        self.assertTrue(json.loads(citation.value_of("_GS_CITATION"))["source_missing"])
        self.assertEqual(report["change_counts"]["uninterpreted_extension"], 1)

    def test_shared_notes_long_identifiers_and_unsupported_structure_reported(self):
        ref = "@" + "I" * 100 + "@"
        projected, report = g.project(g.parse(data(f"0 @N@ SNOTE shared\n0 {ref} INDI\n1 NAME X /Y/ suffix\n2 NSFX suffix\n1 SNOTE @N@\n1 FAMC @F@\n0 @F@ FAM\n1 CHIL {ref}\n")))
        individual = next(n for n in projected if n.tag == "INDI")
        self.assertLess(len(individual.xref), 64)
        self.assertEqual(projected[-2].value_of("CHIL"), individual.xref)
        self.assertEqual(individual.value_of("NOTE"), "shared")
        self.assertIn("structure_retained_only_in_original", report["change_counts"])
        self.assertIn("identifier_remapped", report["change_counts"])

    def test_ambiguous_core_fields_rejected(self):
        for body in ("0 @I@ INDI\n1 SEX M\n1 SEX F\n", "0 @I@ INDI\n1 NAME X\n0 @F@ FAM\n1 CHIL @I@\n1 CHIL @I@\n"):
            with self.assertRaises(g.ImportError): g.project(g.parse(data(body)))


def png():
    stream = io.BytesIO()
    Image.new("RGB", (5, 7)).save(stream, "PNG")
    return stream.getvalue()


class ZipTests(unittest.TestCase):
    def prepare(self, payload, entries=(), **limits):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "input.gdz"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("gedcom.ged", payload)
                for name, value in entries: archive.writestr(name, value)
            with patch.multiple(bundle.settings, **limits) if limits else patch.object(bundle, "CHUNK", bundle.CHUNK):
                return bundle.prepare(path, "gedzip")

    def test_media_order_checksums_unicode_and_no_external_fetch(self):
        payload = data("0 @M@ OBJE\n1 FILE media/p%C3%A0gina%201.png\n2 FORM image/png\n1 FILE second.png\n2 FORM image/png\n0 @R@ OBJE\n1 FILE https://127.0.0.1:1/private\n2 FORM image/png\n")
        _, _, report, books = self.prepare(payload, [("media/pàgina 1.png", png()), ("second.png", png())])
        self.assertEqual(report["media"]["remote_not_fetched"], 1)
        self.assertEqual(len(books), 1)
        self.assertEqual([e["path"] for e in books[0]["files"]], ["media/pàgina 1.png", "second.png"])
        self.assertEqual(books[0]["files"][0]["width"], 5)
        self.assertEqual(len(report["review_token"]), 64)

    def test_paths_reject_traversal_nul_backslash_duplicates_symlinks(self):
        for name in ("../escape", "/absolute", "a/../../b", "a\\b", "C:/x", "a//b"):
            with self.subTest(name=name), self.assertRaises(g.ImportError): self.prepare(data(), [(name, b"x")])
        link = zipfile.ZipInfo("link"); link.external_attr = (stat.S_IFLNK | 0o777) << 16
        with self.assertRaises(g.ImportError): self.prepare(data(), [(link, b"target")])
        with self.assertWarns(UserWarning), self.assertRaises(g.ImportError): self.prepare(data(), [("same", b"x"), ("same", b"y")])
        for uri in ("%2e%2e/x", "x%00y", "x%5cy", "x?query", "x#fragment", "x%broken"):
            with self.subTest(uri=uri), self.assertRaises(g.ImportError): bundle.local_file(uri)
        self.assertEqual(bundle.local_file("a%252Fb"), "a%2Fb")
        self.assertIsNone(bundle.local_file("file:///etc/passwd"))

    def test_missing_media_alternates_type_and_expansion_limits(self):
        for body, entries in [("1 FILE missing.png\n2 FORM image/png\n", []),
            ("1 FILE image.png\n2 FORM image/jpeg\n", [("image.png", png())]),
            ("1 FILE image.png\n2 FORM image/png\n2 TRAN missing.png\n3 FORM image/png\n", [("image.png", png())])]:
            with self.subTest(body=body), self.assertRaises(g.ImportError): self.prepare(data("0 @M@ OBJE\n" + body), entries)
        with self.assertRaises(g.ImportError): self.prepare(data(), [("unused", b"x" * 100)], tree_import_max_expanded_bytes=10)

    def test_repeated_media_references_are_bounded_before_work(self):
        body = "0 @M@ OBJE\n1 FILE a.png\n2 FORM image/png\n1 FILE a.png\n2 FORM image/png\n"
        with self.assertRaises(g.ImportError): self.prepare(data(body), [("a.png", png())], tree_import_max_files=1)

    def test_standalone_retains_local_file_links_without_reading_disk(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "input.ged"; path.write_bytes(data("0 @M@ OBJE\n1 FILE secret.png\n2 FORM image/png\n"))
            _, _, report, books = bundle.prepare(path, "gedcom7")
            self.assertEqual(books, [])
            self.assertEqual(report["media"]["local_not_attached"], 1)
