"""SJ-LM-7 contract section 2.2: convert(..., trad_tw_phrases=False) leaves a Traditional sentence's wording alone (no OpenCC TWPhrases:
插件 stays), keeps the glyph layer, and converts Simplified sentences as before. The default is the old behaviour, so the other callers
(holdout_guard.py, build_digits.py, build_counts_text without its flag) do not move. Synthetic tables only: no OpenCC files."""
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
SIMP_PHRASE, SIMP_CHAR, TW_PHRASE = {}, {"们": "們"}, {"插件": "外掛", "軟件": "軟體"}
POST_TRAD, POST_SIMP = {"着": "著", "喫": "吃"}, {"着": "著", "臺": "台"}
CONV = ({}, {}, 1)   # (phrase, char, maxp) for Traditional sentences: no stray Simplified characters in these rows
TABLES = dict(SIMP_PHRASE=SIMP_PHRASE, SIMP_CHAR=SIMP_CHAR, SIMP_MAXP=[1], MARKERS={"们"}, SIMP_ONLY={"们"}, TRAD_ONLY={"們"},
              TW_PHRASE=TW_PHRASE, TW_MAXP=[2], POST_TRAD=POST_TRAD, POST_SIMP=POST_SIMP, PROTECT={}, PROTECT_TRAD={}, PROTECT_MAXP=[1])

TRAD, SIMP = "我們的插件隨着軟件", "我们的插件隨着软件"
LEX = SimpleNamespace(by_word={w: (None, -2.0) for w in ["我們", "的", "插件", "外掛", "隨著", "軟件", "軟體"]}, max_len=2)


class Base(unittest.TestCase):
    def setUp(self):
        for k, v in TABLES.items():
            p = mock.patch.object(bc, k, v); p.start(); self.addCleanup(p.stop)
        TW_PHRASE["软件"] = "軟體"   # the Simplified sentence reaches TWPhrases after the character table
        self.addCleanup(TW_PHRASE.pop, "软件", None)
        SIMP_CHAR["软"] = "軟"; SIMP_CHAR["件"] = "件"
        self.addCleanup(SIMP_CHAR.pop, "软", None); self.addCleanup(SIMP_CHAR.pop, "件", None)

    def convert(self, s, **kw):
        return bc.convert(s, *CONV, **kw)


class Convert(Base):
    def test_default_is_the_old_behaviour(self):
        self.assertFalse(bc.is_simplified(TRAD))
        self.assertEqual(self.convert(TRAD), "我們的外掛隨著軟體")
        self.assertEqual(self.convert(TRAD, trad_tw_phrases=True), self.convert(TRAD))

    def test_traditional_sentence_keeps_its_wording_but_gets_the_glyph_layer(self):
        self.assertEqual(self.convert(TRAD, trad_tw_phrases=False), "我們的插件隨著軟件")   # 插件 kept, 着→著 still applied

    def test_simplified_sentence_is_converted_as_before(self):
        self.assertTrue(bc.is_simplified(SIMP))
        self.assertEqual(self.convert(SIMP, trad_tw_phrases=False), self.convert(SIMP))
        self.assertEqual(self.convert(SIMP, trad_tw_phrases=False), "我們的外掛隨著軟體")

    def test_the_wrappers_default_to_the_old_behaviour(self):
        conv = (*CONV[:2], 1)
        self.assertEqual(bc.text_runs(TRAD, conv), ["我們的外掛隨著軟體"])
        self.assertEqual(bc.text_runs(TRAD, conv, trad_tw_phrases=False), ["我們的插件隨著軟件"])
        self.assertEqual(list(bc.article_sentences(TRAD + "。", conv)), ["我們的外掛隨著軟體"])
        self.assertEqual(list(bc.article_runs(TRAD + "。", conv)), ["我們的外掛隨著軟體"])
        self.assertEqual(list(bc.article_runs(TRAD + "。", conv, trad_tw_phrases=False)), ["我們的插件隨著軟件"])


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
    def test_the_counter_follows_its_setting_and_the_default_is_model_v6(self):
        for tw, want in ((True, {"我們": 1, "的": 1, "外掛": 1, "隨著": 1, "軟體": 1}), (False, {"我們": 1, "的": 1, "插件": 1, "隨著": 1, "軟件": 1})):
            with mock.patch.dict(bc._W, {"lex": LEX, "conv": CONV, "mw": None, "expected": False, "guard": None, "trad_tw": tw}):
                self.assertEqual(dict(bc._count([TRAD + "。"])[0]), want)
        with mock.patch.dict(bc._W, {"lex": LEX, "conv": CONV, "mw": None, "expected": False, "guard": None}):     # no setting at all: the old behaviour
            self.assertEqual(dict(bc._count([TRAD + "。"])[0]), {"我們": 1, "的": 1, "外掛": 1, "隨著": 1, "軟體": 1})
            self.assertEqual(dict(bc._count([SIMP + "。"])[0]), {"我們": 1, "的": 1, "外掛": 1, "隨著": 1, "軟體": 1})

    def test_build_counts_main_flag_defaults_to_off(self):
        for argv, want in (([], True), (["--no-trad-tw-phrases"], False)):
            with tempfile.TemporaryDirectory() as d, mock.patch.object(bc, "OUT", d), mock.patch.object(bc, "load_conv", lambda: CONV), \
                 mock.patch.object(bc, "articles", lambda limit: iter([TRAD])), mock.patch("multiprocessing.Pool", FakePool), \
                 mock.patch.object(sys, "argv", ["build_counts.py", "--articles", "1", *argv]), mock.patch.dict(bc._W), contextlib.redirect_stdout(io.StringIO()):
                bc.main()
                self.assertEqual(bc._W["trad_tw"], want, argv)

    def test_build_counts_text_flag(self):
        def counted(*flags):
            with tempfile.TemporaryDirectory() as d:
                src, out = os.path.join(d, "s.txt"), os.path.join(d, "o.pkl")
                open(src, "w", encoding="utf-8").write(TRAD + "\n")
                init = lambda *a, **k: bc._W.update(lex=LEX, conv=CONV)
                with mock.patch.object(bc, "_init", init), contextlib.redirect_stdout(io.StringIO()):
                    bct.main([*flags, out, src])
                return dict(pickle.load(open(out, "rb"))["uni"])
        self.assertIn("外掛", counted())                      # default: model-v6 behaviour
        self.assertNotIn("外掛", counted("--no-trad-tw-phrases"))
        self.assertIn("插件", counted("--no-trad-tw-phrases"))


class Guard(Base):
    """The guard scans the text that is counted (contract 2.2): with the flag its patterns and its scan both use the no-TWPhrases form."""
    ROW = "|我們喫插件很好用呢|ㄅ\n"

    def setUp(self):
        super().setUp()
        self.d = tempfile.TemporaryDirectory(); self.addCleanup(self.d.cleanup)
        self.rows = os.path.join(self.d.name, "r.txt")
        open(self.rows, "w", encoding="utf-8").write(self.ROW)
        self.text = os.path.join(self.d.name, "t.txt")
        open(self.text, "w", encoding="utf-8").write("今天我們吃插件很好用呢真的\n")

    def patterns(self, **kw):
        return set(hg.build_matcher([self.rows], CONV, **kw)[1])

    def test_patterns_gain_the_form_the_counter_sees_only_with_the_flag(self):
        counted = bc.fold("我們吃插件很好用呢")                 # glyph layer only
        self.assertNotIn(counted, self.patterns())            # default: raw and the TWPhrases form
        self.assertEqual(self.patterns(), {bc.fold("我們喫插件很好用呢"), bc.fold("我們吃外掛很好用呢")})
        self.assertIn(counted, self.patterns(trad_tw_phrases=False))

    def test_report_hits_the_counted_text_with_the_flag_and_not_without(self):
        def hits(*flags):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                hg.main(["report", "--rows", self.rows, "--text", self.text, *flags], conv=CONV)
            return [l for l in out.getvalue().splitlines() if l.startswith(self.text)]
        self.assertEqual(hits(), [f"{self.text}\t1\t1"])                              # the TWPhrases form of the text against the TWPhrases pattern
        self.assertEqual(hits("--no-trad-tw-phrases"), [f"{self.text}\t1\t1"])         # glyph-only text vs the glyph-only pattern

    def test_guard_matcher_takes_the_setting(self):
        with mock.patch.object(bc, "load_conv", lambda: CONV), contextlib.redirect_stdout(io.StringIO()):
            m_default = bc.guard_matcher([self.rows])
            m_flag = bc.guard_matcher([self.rows], False)
        self.assertFalse(m_default.any(bc.fold("我們吃插件很好用呢")))
        self.assertTrue(m_flag.any(bc.fold("我們吃插件很好用呢")))


if __name__ == "__main__":
    unittest.main()
