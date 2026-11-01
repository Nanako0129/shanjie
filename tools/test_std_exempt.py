"""tools/std_exempt.py against the shipped data/lexicon/std-exempt.tsv (docs/contracts/reading-decode-2.md): the data rows (the lines that do not start with `#`)
are what the rule gives. The rule reads the concised MOE dictionary zip, which is not in the repository: where it is absent (CI) that test is skipped and
says why on stderr; with it present (the default path `build_overlay.CONCISED`, or SHANJIE_CONCISED) it always runs.
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
    def test_the_shipped_file_holds_the_12_registered_pairs(self):
        rows = data_rows(shipped())
        self.assertEqual({tuple(r.split("\t")) for r in rows}, S.EXPECTED)
        self.assertEqual(len(rows), 12)

    def test_the_rule_gives_the_shipped_data_rows(self):
        if not os.path.exists(bo.CONCISED):
            reason = f"the concised dictionary zip is not at {bo.CONCISED} (set SHANJIE_CONCISED); the rule cannot be re-run here"
            print(f"SKIP test_std_exempt: {reason}", file=sys.stderr)
            self.skipTest(reason)
        self.assertEqual(data_rows(S.render(S.pairs())), data_rows(shipped()))

    def test_render_ends_every_row_with_a_newline_and_keeps_the_header(self):
        text = S.render([("ㄅㄛ", "波")])
        self.assertTrue(text.startswith(S.HEADER + "\n") and text.endswith("ㄅㄛ\t波\n"))
        self.assertEqual(data_rows(text), ["ㄅㄛ\t波"])


if __name__ == "__main__":
    unittest.main()
