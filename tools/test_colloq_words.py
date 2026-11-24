"""tools/colloq_words.py (docs/spec/decoder.md §8.7): the rules on synthetic dictionary rows, the format of the two committed lists, and, where the two
dictionaries are present (SHANJIE_REVISED and tools/build_overlay.py's CONCISED; neither is in the repository), that the rules still give the committed lists.
Without them that test is skipped and says why on stderr.
Run: python3 -W ignore -m unittest tools/test_colloq_words.py   (from the repo root)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_overlay as bo  # noqa: E402
import colloq_words as C  # noqa: E402

S = lambda r: r.split()


class Rules(unittest.TestCase):
    def test_variant_changes_truth_table(self):
        v = lambda w, a, b: C.variant_changes(w, S(a), S(b))
        self.assertEqual(v("熱騰騰", "ㄖㄜˋ ㄊㄥˊ ㄊㄥˊ", "ㄖㄜˋ ㄊㄥ ㄊㄥ"), [(1, "level"), (2, "level")])
        self.assertEqual(v("迷迷糊糊", "ㄇㄧˊ ㄇㄧˊ ㄏㄨˊ ㄏㄨˊ", "ㄇㄧˊ ㄇㄧ˙ ㄏㄨ ㄏㄨ"), [(1, "neutral"), (2, "level"), (3, "level")])
        self.assertEqual(v("厲害", "ㄌㄧˋ ㄏㄞˋ", "ㄌㄧˋ ㄏㄞ˙"), [(1, "neutral")])
        self.assertEqual(v("派不是", "ㄆㄞˋ ㄅㄨˋ ㄕˋ", "ㄆㄞˋ ㄅㄨˊ ㄕ˙"), [(2, "neutral")], "the tone of 不 is the lexicon's own sandhi")
        self.assertIsNone(v("暖和和", "ㄋㄨㄢˇ ㄏㄜˊ ㄏㄜˊ", "ㄋㄨㄢˇ ㄏㄨㄛ ㄏㄨㄛ"), "another rime: the whole row is out")
        self.assertIsNone(v("出漏子", "ㄔㄨ ㄌㄡˋ ㄗ˙", "ㄔㄨ ㄌㄡˊ ㄗ˙"), "another tone that is neither level nor neutral")
        self.assertIsNone(v("一點兒", "ㄧˋ ㄉㄧㄢˇ ㄦ", "ㄧˋ ㄉㄧㄚˇㄦ"), "another syllable count")
        self.assertIsNone(v("厲害", "ㄌㄧˋ ㄏㄞˋ", "ㄌㄧˋ ㄏㄞˋ"), "no difference")
        self.assertIsNone(v("不好", "ㄅㄨˋ ㄏㄠˇ", "ㄅㄨˊ ㄏㄠˇ"), "only the sandhi of 不")

    def test_the_intersection_condition_truth_table(self):
        ok = lambda r, cs: C.concised_agrees(S(r), [S(c) for c in cs])
        self.assertTrue(ok("ㄗㄠˇ ㄕㄤ˙", ["ㄗㄠˇ ㄕㄤˋ", "ㄗㄠˇ ㄕㄤ˙"]), "a variant row has it")
        self.assertFalse(ok("ㄓ ㄉㄠ˙", ["ㄓ ㄉㄠˋ"]), "the concised dictionary reads the base tone")
        self.assertFalse(ok("ㄓ ㄉㄠ˙", []), "the word is not in the concised dictionary")
        self.assertFalse(ok("ㄇㄚ˙ ㄈㄢ˙", ["ㄇㄚˊ ㄈㄢ˙"]), "every neutral position must agree")
        self.assertTrue(ok("ㄇㄚ˙ ㄈㄢ˙", ["ㄇㄚ˙ ㄈㄢˊ", "ㄇㄚˊ ㄈㄢ˙"]), "each position in some row")
        self.assertFalse(ok("ㄓ ㄉㄠ˙", ["ㄓ ㄉㄠ˙ ㄉㄜ˙"]), "the same number of syllables")
        self.assertFalse(ok("ㄓ ㄉㄠ˙", ["ㄓ ㄉㄜ˙"]), "the same neutral syllable")

    def test_the_groups_of_the_main_readings(self):
        base = {"早上": [S("ㄗㄠˇ ㄕㄤˋ")], "東西": [S("ㄉㄨㄥ ㄒㄧ"), S("ㄉㄨㄥ ㄒㄧ˙")], "便宜": [S("ㄅㄧㄢˋ ㄧˊ")]}
        rows = [("早上", S("ㄗㄠˇ ㄕㄤ˙"), "", ""), ("早上", S("ㄗㄠˇ ㄕㄤˋ"), "", ""),   # the second has no neutral tone
                ("東西", S("ㄉㄨㄥ ㄒㄧ˙"), "", ""),                                    # the base has the reading
                ("便宜", S("ㄆㄧㄢˊ ㄧ˙"), "", ""),                                    # differs from every base reading in more than tone
                ("晚上", S("ㄨㄢˇ ㄕㄤ˙"), "", ""), ("上", S("ㄕㄤ˙"), "", "")]         # not a base word; one character
        self.assertEqual(C.main_groups(rows, base), {("早上", "ㄗㄠˇ-ㄕㄤ˙"): S("ㄗㄠˇ ㄕㄤ˙")})

    def test_build_takes_the_three_rules_and_only_lexicon_words(self):
        revised = [("熱騰騰", S("ㄖㄜˋ ㄊㄥˊ ㄊㄥˊ"), "變", "ㄖㄜˋ ㄊㄥ ㄊㄥ"), ("厲害", S("ㄌㄧˋ ㄏㄞˋ"), "變", "ㄌㄧˋ ˙ㄏㄞ"),
                   ("氣騰騰", S("ㄑㄧˋ ㄊㄥˊ ㄊㄥˊ"), "變", "ㄑㄧˋ ㄊㄥ ㄊㄥ"),       # in no layer
                   ("一點兒", S("ㄧˋ ㄉㄧㄢˇ ㄦ"), "變", "ㄧˋ ㄉㄧㄚˇㄦ"),              # ㄦ: not counted further
                   ("派不是", S("ㄆㄞˋ ㄅㄨˋ ㄕˋ"), "變", "ㄆㄞˋ ㄅㄨˊ ˙ㄕ"),          # an overlay word
                   ("厲害", S("ㄌㄧˋ ㄏㄞˋ"), "又音", "ㄌㄧˋ ㄏㄞˇ"),                   # another kind of variant
                   ("早上", S("ㄗㄠˇ ㄕㄤ˙"), "", ""), ("知道", S("ㄓ ㄉㄠ˙"), "", "")]
        concised = [("早上", S("ㄗㄠˇ ㄕㄤˋ"), "變", "ㄗㄠˇ ˙ㄕㄤ"), ("知道", S("ㄓ ㄉㄠˋ"), "", "")]
        base = {w: [S(r)] for w, r in (("熱騰騰", "ㄖㄜˋ ㄊㄥˊ ㄊㄥˊ"), ("厲害", "ㄌㄧˋ ㄏㄞˋ"), ("早上", "ㄗㄠˇ ㄕㄤˋ"), ("知道", "ㄓ ㄉㄠˋ"), ("不是", "ㄅㄨˊ ㄕˋ"))}
        table, count = C.build(revised, concised, base, {"派不是"})
        self.assertEqual(table, {("熱騰騰", 1): ("level", C.SRC_VARIANT), ("熱騰騰", 2): ("level", C.SRC_VARIANT), ("厲害", 1): ("neutral", C.SRC_VARIANT),
                                 ("派不是", 2): ("neutral", C.SRC_VARIANT), ("早上", 1): ("neutral", C.SRC_MAIN), ("不是", 1): ("neutral", C.SRC_BU)})
        self.assertEqual((count["variant rows"], count["variant rows without ㄦ"], count["variant words"], count["main groups"], count["main groups kept"]), (5, 4, 3, 2, 1))
        self.assertEqual(set(C.level_words(table)), {("熱騰騰", 1), ("熱騰騰", 2)}, "the rows' list: the words with a level position")
        self.assertEqual(C.render(C.level_words(table)), C.HEADER + f"熱騰騰\t2\tlevel\t{C.SRC_VARIANT}\n熱騰騰\t3\tlevel\t{C.SRC_VARIANT}\n")


class Committed(unittest.TestCase):
    def test_the_format_and_what_each_list_holds(self):
        rows, probe = C.load(C.OUT), C.load(C.PROBE)
        self.assertTrue(rows and all(any(c == "level" for _, c in v) for v in rows.values()), "tools/colloq-words.tsv: reduplicated words only")
        self.assertTrue(all(probe.get(w) == v for w, v in rows.items()), "every word of the rows' list is in the probe list, the same positions")
        self.assertEqual({w for w, v in probe.items() if any(c == "level" for _, c in v)}, set(rows))
        self.assertTrue({"熱騰騰", "迷迷糊糊"} <= set(rows) and {"厲害", "早上", "不是", "賠不是", "派不是"} <= set(probe))
        base, overlay = C.lexicon()
        self.assertTrue(all(w in base or w in overlay for w in probe), "only words of the lexicon")
        for path, header, versions in ((C.OUT, C.HEADER, [C.REVISED_VERSION]), (C.PROBE, C.PROBE_HEADER, [C.REVISED_VERSION, C.CONCISED_VERSION])):
            text = open(path, encoding="utf-8").read()
            self.assertTrue(text.startswith(header) and all(v in header for v in versions) and "Ministry of Education, R.O.C." in header)
            body = [l.split("\t") for l in text[len(header):].splitlines()]
            self.assertEqual(body, sorted(body, key=lambda p: (p[0].encode(), int(p[1]))), "sorted by word bytes, then position")
        self.assertNotIn("簡編本", open(C.OUT, encoding="utf-8").read(), "the rows' list comes from the revised dictionary alone")

    def test_a_malformed_list_stops(self):
        import tempfile
        for bad in ("厲害\t3\tneutral\t修訂本變\n", "厲害\t2\tfalling\t修訂本變\n", "厲害\t2\tneutral\n", "厲害\t2\tneutral\t別的來源\n"):
            with tempfile.TemporaryDirectory() as d:
                p = os.path.join(d, "w.tsv")
                open(p, "w", encoding="utf-8").write(bad)
                with self.assertRaises(SystemExit):
                    C.load(p)

    def test_the_rules_give_the_committed_lists(self):
        if not os.path.exists(C.REVISED) or not os.path.exists(bo.CONCISED):
            reason = (f"the revised dictionary spreadsheet is not at {C.REVISED} (set SHANJIE_REVISED) or the concised dictionary zip is not at {bo.CONCISED} "
                      "(set SHANJIE_CONCISED); the rules cannot be re-run here")
            print(f"SKIP test_colloq_words: {reason}", file=sys.stderr)
            self.skipTest(reason)
        table, _ = C.build(C.load_revised(), C.load_concised(), *C.lexicon())
        self.assertEqual(C.render(C.level_words(table)), open(C.OUT, encoding="utf-8").read())
        self.assertEqual(C.render(table, C.PROBE_HEADER), open(C.PROBE, encoding="utf-8").read())


if __name__ == "__main__":
    unittest.main()
