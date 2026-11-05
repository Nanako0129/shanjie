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

    def test_tune_no_trad_tw_phrases_keeps_the_article_wording(self):
        """docs/contracts/sr-no-twphrases.md section 5: off by default (wikitune and wikijudge1-5 are unchanged); on, a Traditional
        sentence's 權限 stays in the gold instead of becoming 許可權; a Simplified sentence is converted the same either way."""
        self.assertFalse(bt.parser().parse_args([]).tune_no_trad_tw_phrases)
        conv = bt.bc.load_conv()
        text = "管理員權限加好了。我们的插件很好用。"
        default = bt.wiki_clauses(text, conv)
        self.assertEqual(default, [c for s in bt.bc.SENT.findall(text) for c in bt.HAN.findall(bt.bc.convert(s, *conv))])
        self.assertNotIn("管理員權限加好了", default)
        kept = bt.wiki_clauses(text, conv, tw=False)
        self.assertEqual(kept[0], "管理員權限加好了")
        self.assertEqual(kept[1:], default[1:])
        self.assertEqual(bt.wiki_clauses("已轉換的權限句子。", conv, mw=True), ["已轉換的權限句子"])

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


# --cv-no-trad-tw-phrases / --cv-name (docs/contracts/tune-no-twphrases.md section 1.1). The goldens below were taken from build_tune.py before the
# option existed, on these synthetic sources: without the new flag every output must stay as it was (the contract's regression assertion).
CV_TRAD, CV_SIMP = "我們的權限很好用戊", "我们的权限很好用丙"   # both hash into the tune 20%
CV_ROWS = ROWS + [CV_TRAD, CV_SIMP]
WIKI = ["管理員權限加好了。我们的插件很好用。", "大家都來吃飯吧。我们的权限很好用！", "今天天氣很好吧。軟件權限都改了；", "這個插件我们天天用。",
        "我们的软件權限很好用。", "天氣很好所以出門玩。"]


class SynthTables(ttp.Base):
    """The synthetic conversion tables plus 權限 -> 許可權 (Traditional keys) and 权 -> 權 (Simplified characters); no tests of its own."""
    def setUp(self):
        super().setUp()
        for k, v in (("權限", "許可權"), ("管理員權限", "管理員許可權")):
            ttp.TW_PHRASE[k] = v; self.addCleanup(ttp.TW_PHRASE.pop, k, None)
        ttp.SIMP_CHAR["权"] = "權"; self.addCleanup(ttp.SIMP_CHAR.pop, "权", None)
        ttp.TABLES["TW_MAXP"][0] = 5; self.addCleanup(ttp.TABLES["TW_MAXP"].__setitem__, 0, 2)

    def sources(self, src):
        """Write the synthetic Common Voice / Tatoeba files into src."""
        a, b = os.path.join(src, "cv-a.tsv"), os.path.join(src, "cmn_sentences.tsv.bz2")
        open(a, "w", encoding="utf-8").write("".join(f"x\t{r}\n" for r in CV_ROWS[:21]))
        with bz2.open(b, "wt", encoding="utf-8") as f:
            f.write("".join(f"1\tcmn\t{r}\n" for r in CV_ROWS[21:]))


class MainOutputs(SynthTables):
    """build_tune.main on synthetic sources: the sampled clauses handed to readings() and colloquial-train.txt."""
    def run_main(self, *argv):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as out:
            self.sources(src)
            calls, out_io = [], io.StringIO()
            with mock.patch.object(bt, "SRC", src), mock.patch.object(bt, "OUT", out), mock.patch.object(bt.bc, "load_conv", lambda: ttp.CONV), \
                 mock.patch.object(bt, "readings", lambda rows, name: calls.append((name, rows))), \
                 mock.patch.object(bt, "articles_in", lambda lo, hi, progress=0: iter(WIKI)), \
                 mock.patch.object(sys, "argv", ["build_tune.py", *argv]), contextlib.redirect_stdout(out_io):
                bt.main()
            train = open(os.path.join(out, "colloquial-train.txt"), encoding="utf-8").read() if "colloquial-train.txt" in os.listdir(out) else None
            return calls, train

    OLD_CV = ['我們的外掛很好用壬', '我們的許可權很好用戊', '我們的外掛很好用宇', '我們的外掛很好用子', '我們的外掛很好用宙', '我們的外掛很好用申', '我們的外掛很好用巳', '我們的許可權很好用丙']
    OLD_WIKI = ['管理員許可權加好了', '我們的許可權很好用', '今天天氣很好吧', '這個外掛我們天天用', '我們的外掛很好用']
    OLD_WIKI_KEPT = ['管理員權限加好了', '我們的許可權很好用', '今天天氣很好吧', '這個外掛我們天天用', '我們的外掛很好用']   # --tune-no-trad-tw-phrases alone
    OLD_TRAIN = (31, "fb43ed6a1504f8431dab29ec9abda650e3225a625fd167a19c2cb8c81e838b5c")

    def train_id(self, train):
        return (train.count("\n"), hashlib.sha256(train.encode("utf-8")).hexdigest())

    def test_without_the_new_flag_every_output_is_as_before(self):
        calls, train = self.run_main("--cv-rows", "8", "--wiki-rows", "5")
        self.assertEqual(calls, [("cvtune", self.OLD_CV), ("wikitune", self.OLD_WIKI)])
        self.assertEqual(self.train_id(train), self.OLD_TRAIN)
        calls, train = self.run_main("--part", "cv", "--cv-rows", "8")
        self.assertEqual((calls, self.train_id(train)), ([("cvtune", self.OLD_CV)], self.OLD_TRAIN))
        self.assertEqual(self.run_main("--part", "wiki", "--cv-rows", "8", "--wiki-rows", "5"), ([("wikitune", self.OLD_WIKI)], None))

    def test_wiki_part_with_only_the_wiki_flag_is_as_before(self):
        """wikijudge6's command (--part wiki ... --tune-no-trad-tw-phrases) must not move: the wiki half keeps the article wording, cv is untouched."""
        self.assertEqual(self.run_main("--part", "wiki", "--cv-rows", "8", "--wiki-rows", "5", "--tune-no-trad-tw-phrases"),
                         ([("wikitune", self.OLD_WIKI_KEPT)], None))
        calls, train = self.run_main("--cv-rows", "8", "--wiki-rows", "5", "--tune-no-trad-tw-phrases")
        self.assertEqual((calls, self.train_id(train)), ([("cvtune", self.OLD_CV), ("wikitune", self.OLD_WIKI_KEPT)], self.OLD_TRAIN))

    def test_cv_flag_keeps_a_traditional_quanxian_and_still_converts_a_simplified_one(self):
        calls, train = self.run_main("--part", "cv", "--cv-rows", "100", "--cv-no-trad-tw-phrases", "--cv-name", "cvtune-tw0")
        self.assertEqual([c[0] for c in calls], ["cvtune-tw0"])
        self.assertIsNone(train)                                                    # colloquial-train is not written
        rows = calls[0][1]
        self.assertIn("我們的權限很好用戊", rows)                                     # the Traditional source sentence keeps 權限
        self.assertNotIn("我們的許可權很好用戊", rows)
        self.assertIn("我們的許可權很好用丙", rows)                                   # the Simplified one still goes through TWPhrases
        self.assertNotIn("我們的權限很好用丙", rows)
        default = set(self.run_main("--part", "cv", "--cv-rows", "100")[0][0][1])
        self.assertEqual({r.replace("權限", "許可權").replace("插件", "外掛") for r in rows}, default)   # the same clauses, only TWPhrases wording differs
        self.assertIn("我們的插件很好用壬", rows)                                      # (the other Traditional rows keep their wording too)

    def test_wiki_part_continues_the_cv_random_state(self):
        """--part wiki re-runs the cv sampling, so it gives the wiki rows a full run gives (with both flags, as the two TW0 commands do)."""
        both = ("--cv-no-trad-tw-phrases", "--tune-no-trad-tw-phrases")
        full = self.run_main("--cv-rows", "8", "--wiki-rows", "5", *both)[0]
        wiki = self.run_main("--part", "wiki", "--cv-rows", "8", "--wiki-rows", "5", *both)[0]
        self.assertEqual([c[0] for c in full], ["cvtune", "wikitune"])
        self.assertEqual(wiki, full[1:])
        self.assertEqual(wiki[0][1][0], "管理員權限加好了")

    def test_the_cv_flag_does_not_combine_with_train_only(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.run_main("--cv-no-trad-tw-phrases", "--train-only-new-conv")
        a = bt.parser().parse_args([])
        self.assertEqual((a.cv_no_trad_tw_phrases, a.cv_name), (False, "cvtune"))


if __name__ == "__main__":
    unittest.main()
