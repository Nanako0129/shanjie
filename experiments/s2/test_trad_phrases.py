"""docs/contracts/tw-conv.md sections 2.1, 2.4 and 2.5 with synthetic tables (no OpenCC files): a `native` input is never converted, a `mixed` one goes
sentence by sentence through convert() (Traditional sentences get only the glyph layer, Simplified ones also the mainland-term subset), every input
must name its kind, and the removed TWPhrases flags are errors. (The file was the SJ-LM-7 section 2.2 flag test before tw-conv removed the flag.)"""
import contextlib
import io
import os
import pickle
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_counts as bc  # noqa: E402
import build_counts_text as bct  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "tools"))
import holdout_guard as hg  # noqa: E402

# Module-level so the tables outlive the tests (bc._longest caches by id(table)).
TW_PHRASE = {"軟件": "軟體"}
CHAR = {"们": "們", "软": "軟"}
CONV = ({}, CHAR, 1)   # (phrase, char, maxp)
TABLES = dict(TRAD_SIMP_ONLY={"们", "软"}, MARKERS={"们", "软"}, TRAD_ONLY={"們", "軟"}, FOLD_TRANS={ord("着"): "著", ord("喫"): "吃"},
              TW_PHRASE=TW_PHRASE, TW_MAXP=[2])

TRAD, SIMP = "我們的插件隨着軟件", "我们的插件隨着软件"
LEX = SimpleNamespace(by_word={w: (None, -2.0) for w in ["我們", "的", "插件", "隨著", "軟件", "軟體", "隨着"]}, max_len=2)


class Base(unittest.TestCase):
    def setUp(self):
        for k, v in TABLES.items():
            p = mock.patch.object(bc, k, v); p.start(); self.addCleanup(p.stop)


class Convert(Base):
    def test_traditional_sentence_gets_the_glyph_layer_only(self):
        self.assertFalse(bc.is_simplified(TRAD))
        self.assertEqual(bc.convert(TRAD, *CONV), "我們的插件隨著軟件")   # 着→著 fold; 軟件 is a mainland term but a Traditional sentence has no phrase table

    def test_simplified_sentence_gets_characters_terms_and_fold(self):
        self.assertTrue(bc.is_simplified(SIMP))
        self.assertEqual(bc.convert(SIMP, *CONV), "我們的插件隨著軟體")

    def test_native_is_never_converted_and_mixed_is(self):
        self.assertEqual(bc.text_runs(SIMP, CONV, "native"), ["我们的插件隨着软件"])
        self.assertEqual(bc.text_runs(SIMP, CONV, "mixed"), ["我們的插件隨著軟體"])
        self.assertEqual(bc.text_runs(TRAD, CONV, "native"), ["我們的插件隨着軟件"])

    def test_the_kind_is_required_and_checked(self):
        with self.assertRaises(TypeError):
            bc.text_runs(SIMP, CONV)
        with self.assertRaises(ValueError):
            bc.text_runs(SIMP, CONV, "both")

    def test_a_wiki_article_is_a_mixed_input(self):
        self.assertEqual(list(bc.article_runs(SIMP + "。", CONV)), ["我們的插件隨著軟體"])
        self.assertEqual(list(bc.article_sentences(TRAD + "。", CONV)), ["我們的插件隨著軟件"])


class FakePool:
    """multiprocessing.Pool stand-in that runs the initializer and the map in this process."""
    def __init__(self, procs, initializer=None, initargs=()):
        initializer(*initargs)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def imap_unordered(self, f, it):
        return map(f, it)


class Counting(Base):
    def test_a_wiki_count_converts_its_sentences(self):
        with mock.patch.dict(bc._W, {"lex": LEX, "conv": CONV, "mw": None, "expected": False, "guard": None}):
            self.assertEqual(dict(bc._count([SIMP + "。"])[0]), {"我們": 1, "的": 1, "插件": 1, "隨著": 1, "軟體": 1})

    def test_build_counts_main_has_no_twphrases_flag(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(bc, "OUT", d), mock.patch.object(bc, "load_conv", lambda: CONV), \
             mock.patch.object(bc, "articles", lambda limit: iter([TRAD])), mock.patch("multiprocessing.Pool", FakePool), \
             mock.patch.object(sys, "argv", ["build_counts.py", "--articles", "1", "--no-trad-tw-phrases"]), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                bc.main()

    def counted(self, kind, text):
        with tempfile.TemporaryDirectory() as d:
            src, out = os.path.join(d, "s.txt"), os.path.join(d, "o.pkl")
            open(src, "w", encoding="utf-8").write(text + "\n")
            init = lambda *a, **k: bc._W.update(lex=LEX, conv=CONV)
            with mock.patch.object(bc, "_init", init), contextlib.redirect_stdout(io.StringIO()):
                bct.main([out, kind, src])
            return dict(pickle.load(open(out, "rb"))["uni"])

    def test_build_counts_text_native_and_mixed(self):
        self.assertEqual(self.counted("--native", SIMP), {})                   # a native input is not converted: 软件 etc. do not segment
        self.assertIn("軟體", self.counted("--mixed", SIMP))
        self.assertIn("隨着", self.counted("--native", TRAD))                  # kept as written
        self.assertIn("隨著", self.counted("--mixed", TRAD))

    def test_build_counts_text_needs_a_kind_and_has_no_old_options(self):
        with contextlib.redirect_stderr(io.StringIO()):
            for argv in (["o.pkl"], ["o.pkl", "src.txt"], ["--no-trad-tw-phrases", "o.pkl", "--native", "src.txt"]):
                with self.assertRaises(SystemExit):
                    bct.main(argv)


class Guard(Base):
    """The guard needs no input kind: its patterns hold the sentence as written and converted, and a scanned text is searched in both forms."""
    ROW = "|我们喫插件很好用呢|ㄅ\n"

    def setUp(self):
        super().setUp()
        self.d = tempfile.TemporaryDirectory(); self.addCleanup(self.d.cleanup)
        self.rows = os.path.join(self.d.name, "r.txt")
        open(self.rows, "w", encoding="utf-8").write(self.ROW)

    def hits(self, s):
        p, out = os.path.join(self.d.name, "t.txt"), io.StringIO()
        open(p, "w", encoding="utf-8").write(s + "\n")
        with contextlib.redirect_stdout(out):
            hg.main(["report", "--rows", self.rows, "--text", p], conv=CONV)
        return [l for l in out.getvalue().splitlines() if l.startswith(p)]

    def test_patterns_are_the_sentence_as_written_and_converted(self):
        self.assertEqual(set(hg.build_matcher([self.rows], CONV)[1]), {bc.fold("我们喫插件很好用呢"), bc.fold("我們吃插件很好用呢")})

    def test_report_finds_a_native_text_and_a_converted_one(self):
        self.assertEqual(self.hits("今天我们喫插件很好用呢真的")[0].split("\t")[1:], ["1", "1"])    # as written: what a native count holds
        self.assertEqual(self.hits("今天我們吃插件很好用呢真的")[0].split("\t")[1:], ["1", "1"])    # the converted form: what a mixed count holds

    def test_the_removed_flag_is_an_error(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            hg.main(["report", "--rows", self.rows, "--no-trad-tw-phrases"], conv=CONV)

    def test_guard_matcher_takes_no_setting(self):
        with mock.patch.object(bc, "load_conv", lambda: CONV), contextlib.redirect_stdout(io.StringIO()):
            m = bc.guard_matcher([self.rows])
        self.assertTrue(m.any(bc.fold("我們吃插件很好用呢")))


if __name__ == "__main__":
    unittest.main()
