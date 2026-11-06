"""build_tune's wiki range options (docs/contracts/rerank-formal.md §3.1): the defaults are wikitune's, so the default
output is unchanged; wikijudge is the same sampler over another article range."""
import bz2
import contextlib
import hashlib
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

    def test_wiki_clauses_convert_a_mixed_source_and_the_old_flags_are_gone(self):
        """docs/contracts/tw-conv.md 2.1: a wiki article is a mixed input (each sentence through convert(); mw already converted the whole article)."""
        conv = bt.bc.load_conv()
        text = "管理員權限加好了。我们的插件很好用。"
        self.assertEqual(bt.wiki_clauses(text, conv), [c for s in bt.bc.SENT.findall(text) for c in bt.HAN.findall(bt.bc.convert(s, *conv))])
        self.assertEqual(bt.wiki_clauses(text, conv)[0], "管理員權限加好了")   # 權限 is not a mainland term: the Traditional sentence is as written
        self.assertEqual(bt.wiki_clauses("已轉換的權限句子。", conv, mw=True), ["已轉換的權限句子"])
        for flag in ("--tune-no-trad-tw-phrases", "--cv-no-trad-tw-phrases", "--train-only-new-conv"):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                bt.parser().parse_args([flag])

    def test_articles_in_progress_lines_go_to_stderr_only_when_asked(self):
        orig, err = bt.bc.articles, io.StringIO()
        bt.bc.articles = lambda limit: iter(range(limit))
        try:
            self.assertEqual(list(bt.articles_in(2, 7)), [2, 3, 4, 5, 6])
            with contextlib.redirect_stderr(err):
                self.assertEqual(list(bt.articles_in(2, 7)), [2, 3, 4, 5, 6])
                self.assertEqual(err.getvalue(), "")
                self.assertEqual(list(bt.articles_in(2, 7, progress=3)), [2, 3, 4, 5, 6])
        finally:
            bt.bc.articles = orig
        self.assertEqual(err.getvalue(), "wiki articles read: 3\nwiki articles read: 6\n")
        self.assertEqual(bt.parser().parse_args([]).progress_every, 0)

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


# docs/contracts/tw-conv.md 2.1: Common Voice zh-TW is native, Tatoeba cmn is mixed. Synthetic conversion tables (no OpenCC files), see test_trad_phrases.py.
import test_trad_phrases as ttp  # noqa: E402

ROWS = ["我们的软件很好用" + c for c in "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥天地玄黃宇宙洪荒日月盈昃辰宿列張"]


class Sources(ttp.Base):
    def sources(self, d):
        """cv-a.tsv (the first 20 rows) and cmn_sentences.tsv.bz2 (the rest) in directory d."""
        open(os.path.join(d, "cv-a.tsv"), "w", encoding="utf-8").write("".join(f"x\t{r}\n" for r in ROWS[:20]))
        with bz2.open(os.path.join(d, "cmn_sentences.tsv.bz2"), "wt", encoding="utf-8") as f:
            f.write("".join(f"1\tcmn\t{r}\n" for r in ROWS[20:]))

    def files(self, d):
        self.sources(d)
        return [(os.path.join(d, "cv-a.tsv"), "native"), (os.path.join(d, "cmn_sentences.tsv.bz2"), "mixed")]


class Split(Sources):
    def test_common_voice_is_kept_as_written_and_tatoeba_is_converted(self):
        with tempfile.TemporaryDirectory() as d:
            train, tune = bt.split_colloquial(self.files(d), ttp.CONV)
        cv_rows = {r for r in ROWS[:20]}
        self.assertTrue(train and tune)
        self.assertEqual(len(train) + sum(1 for r in ROWS if bt.is_tune(r)), len(ROWS))          # the split hashes the raw sentence, kind does not move it
        for r in train:
            self.assertEqual(r in cv_rows, "软件" in r)                                          # native: Simplified wording kept; mixed: 软件 converted
            self.assertEqual("軟體" in r, r not in cv_rows)
        self.assertEqual({t for t in tune if "软件" in t}, {t for t in (r for r in cv_rows if bt.is_tune(r))})   # a native tune clause is the raw sentence
        self.assertTrue(all("軟體" in t for t in tune if "軟體" in t) and any("軟體" in t for t in tune))

    def test_an_unknown_kind_stops(self):
        with tempfile.TemporaryDirectory() as d:
            self.sources(d)
            with self.assertRaises(ValueError):
                bt.split_colloquial([(os.path.join(d, "cv-a.tsv"), "both")], ttp.CONV)


class MainOutputs(Sources):
    """build_tune.main on synthetic sources: the sampled clauses handed to readings() and colloquial-train.txt."""
    def run_main(self, *argv):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as out:
            self.sources(src)
            calls, out_io = [], io.StringIO()
            with mock.patch.object(bt, "SRC", src), mock.patch.object(bt, "OUT", out), mock.patch.object(bt.bc, "load_conv", lambda: ttp.CONV), \
                 mock.patch.object(bt, "readings", lambda rows, name: calls.append((name, rows))), \
                 mock.patch.object(bt, "articles_in", lambda lo, hi, progress=0: iter(["我们的软件很好用。", "今天天氣很好吧。"])), \
                 mock.patch.object(sys, "argv", ["build_tune.py", *argv]), contextlib.redirect_stdout(out_io):
                bt.main()
            return calls, open(os.path.join(out, "colloquial-train.txt"), encoding="utf-8").read() if "colloquial-train.txt" in os.listdir(out) else None

    def test_main_writes_the_converted_pool_and_the_sampled_tune_rows(self):
        calls, train = self.run_main("--cv-rows", "8", "--wiki-rows", "5")
        self.assertEqual([c[0] for c in calls], ["cvtune", "wikitune"])
        lines = train.splitlines()
        self.assertEqual(len(lines), sum(1 for r in ROWS if not bt.is_tune(r)))
        self.assertTrue(any("軟體" in l for l in lines) and any("软件" in l for l in lines))   # Tatoeba converted, Common Voice as written
        self.assertEqual(calls[1][1], ["我們的軟體很好用", "今天天氣很好吧"])                    # the wiki clauses are converted (mixed)

    def test_part_wiki_gives_the_wiki_rows_a_full_run_gives(self):
        """--part wiki re-runs the cv sampling to continue its random state."""
        full = self.run_main("--cv-rows", "8", "--wiki-rows", "1")[0]
        wiki, train = self.run_main("--part", "wiki", "--cv-rows", "8", "--wiki-rows", "1")
        self.assertEqual(wiki, full[1:])
        self.assertIsNone(train)

    def test_the_cv_name_option_renames_the_cv_output(self):
        self.assertEqual(self.run_main("--part", "cv", "--cv-rows", "8", "--cv-name", "cvtune-x")[0][0][0], "cvtune-x")


if __name__ == "__main__":
    unittest.main()
