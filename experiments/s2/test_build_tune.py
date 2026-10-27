"""build_tune's wiki range options (docs/contracts/rerank-formal.md §3.1): the defaults are wikitune's, so the default
output is unchanged; wikijudge is the same sampler over another article range."""
import bz2
import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_tune as bt  # noqa: E402


class WikiRange(unittest.TestCase):
    def test_defaults_are_wikitune(self):
        a = bt.parser().parse_args([])
        self.assertEqual((a.wiki_range, a.wiki_name, a.wiki_rows), ([300_000, 305_000], "wikitune", 3000))

    def test_wikijudge_range(self):
        a = bt.parser().parse_args(["--part", "wiki", "--wiki-range", "310000", "315000", "--wiki-name", "wikijudge"])
        self.assertEqual((a.part, a.wiki_range, a.wiki_name), ("wiki", [310_000, 315_000], "wikijudge"))

    def test_articles_in_takes_lo_to_hi_minus_one(self):
        calls = []
        def fake_articles(limit):
            calls.append(limit)
            return iter(range(limit))
        orig = bt.bc.articles
        bt.bc.articles = fake_articles
        try:
            self.assertEqual(list(bt.articles_in(3, 7)), [3, 4, 5, 6])
            self.assertEqual(list(bt.articles_in(0, 2)), [0, 1])
        finally:
            bt.bc.articles = orig
        self.assertEqual(calls, [7, 2])


# SJ-LM-7 section 2.2: --train-only-new-conv. Synthetic conversion tables (no OpenCC files), see test_trad_phrases.py.
import test_trad_phrases as ttp  # noqa: E402

ROWS = ["我們的插件很好用" + c for c in "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥天地玄黃宇宙洪荒日月盈昃辰宿列張"]


class TrainOnlyNewConv(ttp.Base):
    def files(self, d):
        a, b = os.path.join(d, "cv-a.tsv"), os.path.join(d, "cmn_sentences.tsv.bz2")
        open(a, "w", encoding="utf-8").write("".join(f"x\t{r}\n" for r in ROWS[:20]))
        with bz2.open(b, "wt", encoding="utf-8") as f:
            f.write("".join(f"1\tcmn\t{r}\n" for r in ROWS[20:]))
        return [a, b]

    def test_split_and_tune_side_do_not_move_and_plugin_survives_on_train(self):
        with tempfile.TemporaryDirectory() as d:
            files = self.files(d)
            old_train, old_tune = bt.split_colloquial(files, ttp.CONV)
            new_train, new_tune = bt.split_colloquial(files, ttp.CONV, new_train=True)
        self.assertEqual(new_tune, old_tune)
        self.assertTrue(old_tune and old_train)                                          # the fixture has both sides
        self.assertEqual(len(old_train) + len(old_tune), len(ROWS))
        self.assertTrue(all("外掛" in r and "插件" not in r for r in old_train))          # current conversion
        self.assertTrue(all("插件" in r and "外掛" not in r for r in new_train))          # new conversion, same rows in the same order
        self.assertEqual([r.replace("插件", "外掛") for r in new_train], old_train)
        self.assertTrue(all("外掛" in r for r in old_tune))                               # the tune side still uses the current conversion

    def test_main_writes_only_the_train_side(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as out:
            self.files(src)
            out_io = io.StringIO()
            with mock.patch.object(bt, "SRC", src), mock.patch.object(bt, "OUT", out), mock.patch.object(bt.bc, "load_conv", lambda: ttp.CONV), \
                 mock.patch.object(sys, "argv", ["build_tune.py", "--train-only-new-conv"]), contextlib.redirect_stdout(out_io):
                bt.main()
            self.assertEqual(os.listdir(out), ["colloquial-train.txt"])                    # no cvtune, no wikitune
            lines = open(os.path.join(out, "colloquial-train.txt"), encoding="utf-8").read().splitlines()
            self.assertTrue(lines and all("插件" in l for l in lines))
            self.assertIn(f"{len(lines)} sentences, {len(lines)} with 插件, SHA-256 ", out_io.getvalue())


if __name__ == "__main__":
    unittest.main()
