import copy
import unittest

from app.recovery.schema import comparison_key, metadata_matches

OLD = "CHECK (state::text = ANY (ARRAY['pending'::character varying, 'ready'::character varying]::text[]))"
NEW = "CHECK (state::text = ANY (ARRAY['pending'::character varying::text, 'ready'::character varying::text]))"
OLD_INDEX = "CREATE UNIQUE INDEX i ON public.t USING btree (id) WHERE ((state)::text = ANY ((ARRAY['pending'::character varying, 'ready'::character varying])::text[]))"
NEW_INDEX = "CREATE UNIQUE INDEX i ON public.t USING btree (id) WHERE ((state)::text = ANY (ARRAY[('pending'::character varying)::text, ('ready'::character varying)::text]))"


class SchemaComparisonTests(unittest.TestCase):
    def test_only_equivalent_literal_array_casts_match(self):
        self.assertEqual(comparison_key(OLD), comparison_key(NEW))
        self.assertEqual(comparison_key(OLD_INDEX), comparison_key(NEW_INDEX))
        self.assertEqual(comparison_key(OLD.replace("pending", "l''avi")), comparison_key(NEW.replace("pending", "l''avi")))

    def test_actual_semantic_changes_do_not_match(self):
        for changed in (NEW.replace("ready", "deleted"), NEW.replace("ANY", "ALL"),
                NEW.replace("state", "other"), NEW + " NOT VALID", NEW.replace("varying", "varying(2)"),
                NEW.replace("= ANY", "<> ANY"), NEW.replace("'ready'", "NULL"),
                NEW.replace("'ready'", "lower('ready')"), NEW.replace("::text", "::citext"),
                NEW.replace("'pending'", "'PENDING'"), NEW.replace("'pending'", "'pending '")):
            with self.subTest(changed=changed): self.assertNotEqual(comparison_key(OLD), comparison_key(changed))
        self.assertNotEqual(comparison_key(OLD_INDEX), comparison_key(NEW_INDEX.replace("UNIQUE ", "")))

    def test_quotes_identifiers_escapes_comments_and_literal_content_are_preserved(self):
        for wrapper in (lambda sql: "'" + sql.replace("'", "''") + "'", lambda sql: '"' + sql + '"',
                lambda sql: "E'\\n' || " + sql, lambda sql: "-- note\n" + sql,
                lambda sql: "$$" + sql + "$$", lambda sql: "/* note */ " + sql):
            self.assertNotEqual(comparison_key(wrapper(OLD)), comparison_key(wrapper(NEW)))
        self.assertNotEqual(comparison_key(OLD.replace("ready", "ARRAY['x']")), comparison_key(NEW))

    def test_malformed_and_nonliteral_arrays_do_not_get_normalized(self):
        for sql in ("(", "ARRAY[", "ARRAY['x'", "ARRAY['x'::character varying", "ARRAY[]::text[]",
                "ARRAY[column::character varying]::text[]", "ARRAY['x'::character varying(1)]::text[]"):
            with self.subTest(sql=sql): self.assertEqual(comparison_key(sql), ("exact", sql))

    def test_metadata_keeps_grants_counts_policies_names_and_every_other_field(self):
        source = {"constraints": [{"relname": "t", "conname": "c", "definition": OLD}],
            "indexes": [{"tablename": "t", "indexname": "i", "indexdef": OLD_INDEX}],
            "counts": {"t": 1}, "grants": ["SELECT"], "policies": ["tenant-only"]}
        target = copy.deepcopy(source)
        target["constraints"][0]["definition"] = NEW; target["indexes"][0]["indexdef"] = NEW_INDEX
        before = copy.deepcopy(source)
        self.assertTrue(metadata_matches(target, source)); self.assertEqual(before, source)
        for key, value in (("counts", {"t": 0}), ("grants", []), ("policies", []), ("indexes", [])):
            with self.subTest(key=key): self.assertFalse(metadata_matches({**target, key: value}, source))
        target["constraints"][0]["conname"] = "another"
        self.assertFalse(metadata_matches(target, source))
