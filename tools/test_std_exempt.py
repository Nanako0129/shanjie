"""tools/std_exempt.py against the shipped data/lexicon/std-exempt.tsv (docs/spec/decoder.md §5): the data rows (the lines that do not start with `#`)
are what the two rules give. The rules read the concised MOE dictionary zip and the revised dictionary (SHANJIE_MOEDICT), which are not in the repository:
where either is absent (CI) that test is skipped and says why on stderr; with both present (the default path `build_overlay.CONCISED` or SHANJIE_CONCISED, and
SHANJIE_MOEDICT set) it always runs.
Run: python3 -m unittest tools/test_std_exempt.py   (from the repo root)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_overlay as bo  # noqa: E402
import std_exempt as S  # noqa: E402

SHIPPED = os.path.join(S.ROOT, "data", "lexicon", "std-exempt.tsv")


def data_rows(text):
    return [l for l in text.splitlines() if l and not l.startswith("#")]


def shipped():
    with open(SHIPPED, encoding="utf-8") as f:
        return f.read()


class StdExempt(unittest.TestCase):
    def test_the_shipped_file_holds_the_registered_pairs(self):
        rows = [tuple(r.split("\t")) for r in data_rows(shipped())]
        self.assertEqual((set(rows[:12]), set(rows[12:])), (S.EXPECTED, S.EXPECTED_SOLE))   # the first rule's 12, then the only-reading 4
        self.assertEqual(len(rows), 16)

    def test_the_only_reading_rule(self):
        """A character with one dictionary reading that the lexicon has at the floor under another reading is a pair; one with two dictionary readings,
        one whose only reading is not at the floor, and one the lexicon scores nowhere above the floor are not."""
        scores = {"嗨": {"ㄏㄞ": -6.8, "ㄏㄞˋ": -5.9}, "咳": {"ㄏㄞ": -6.8, "ㄎㄜˊ": -5.0}, "波": {"ㄅㄛ": -4.7, "ㄆㄛ": -4.0}, "乙": {"a": -6.8, "b": -6.8}}
        keep, S.lexicon_scores = S.lexicon_scores, lambda: scores
        try:
            self.assertEqual(S.sole_pairs({"嗨": "ㄏㄞ", "波": "ㄅㄛ", "乙": "a"}), [("ㄏㄞ", "嗨")])   # 咳 has several dictionary readings: not in the map
        finally:
            S.lexicon_scores = keep

    def test_the_rule_gives_the_shipped_data_rows(self):
        if not os.path.exists(bo.CONCISED) or not os.path.isfile(S.readings.MOEDICT):
            reason = (f"the concised dictionary zip is not at {bo.CONCISED} (set SHANJIE_CONCISED) or SHANJIE_MOEDICT does not name the revised dictionary; "
                      "the rules cannot be re-run here")
            print(f"SKIP test_std_exempt: {reason}", file=sys.stderr)
            self.skipTest(reason)
        self.assertEqual(data_rows(S.render(S.pairs() + S.sole_pairs())), data_rows(shipped()))

    def test_render_ends_every_row_with_a_newline_and_keeps_the_header(self):
        text = S.render([("ㄅㄛ", "波")])
        self.assertTrue(text.startswith(S.HEADER + "\n") and text.endswith("ㄅㄛ\t波\n"))
        self.assertEqual(data_rows(text), ["ㄅㄛ\t波"])


if __name__ == "__main__":
    unittest.main()
