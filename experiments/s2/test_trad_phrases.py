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

# Module-level so the tables outlive the tests (bc._longest caches by id(table)).
SIMP_PHRASE, SIMP_CHAR, TW_PHRASE = {}, {"们": "們"}, {"插件": "外掛", "軟件": "軟體"}
POST_TRAD, POST_SIMP = {"着": "著"}, {"着": "著", "臺": "台"}
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


class Counting(Base):
    def test_build_counts_counts_the_traditional_wording(self):
        with mock.patch.dict(bc._W, {"lex": LEX, "conv": CONV, "mw": None, "expected": False, "guard": None}):
            uni, *_ = bc._count([TRAD + "。"])
            self.assertEqual(dict(uni), {"我們": 1, "的": 1, "插件": 1, "隨著": 1, "軟件": 1})
            uni, *_ = bc._count([SIMP + "。"])
            self.assertEqual(dict(uni), {"我們": 1, "的": 1, "外掛": 1, "隨著": 1, "軟體": 1})

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


if __name__ == "__main__":
    unittest.main()
