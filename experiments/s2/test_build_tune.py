"""build_tune's wiki range options (docs/contracts/rerank-formal.md §3.1): the defaults are wikitune's, so the default
output is unchanged; wikijudge is the same sampler over another article range."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_tune as bt  # noqa: E402


class WikiRange(unittest.TestCase):
    def test_defaults_are_wikitune(self):
        a = bt.parser().parse_args([])
        self.assertEqual((a.wiki_range, a.wiki_name, a.wiki_rows), ([300_000, 305_000], "wikitune", 3000))

    def test_wikijudge_range(self):
        a = bt.parser().parse_args(["--part", "wiki", "--wiki-range", "310000", "315000", "--wiki-name", "wikijudge"])
        self.assertEqual((a.part, a.wiki_range, a.wiki_name), ("wiki", [310_000, 315_000], "wikijudge"))


if __name__ == "__main__":
    unittest.main()
