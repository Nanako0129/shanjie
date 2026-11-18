"""Unit tests for reference/proto/english_score.py (docs/spec/english-mixed.md §16.2, §16.3, §16.5). No model, no corpus.
Run: python3 -W ignore -m unittest reference/proto/test_english_score.py"""
import math
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import english_score as es  # noqa: E402

TINY = "# t\nT\t1000\nN\t40\nA\t10\nD\t用\t5\t20\nE\tok\t30\nE\tspeech\t10\nG\t^^s\t2\nG\t^sp\t1\nG\tspe\t1\nG\tee$\t1\nL\t5\t3\nL\t6\t1\n"


class Constants(unittest.TestCase):
    def test_constants_match_english_auto_rs(self):
        src = open(os.path.join(es.ROOT, "core", "src", "english_auto.rs"), encoding="utf-8").read()

        def const(name):
            return float(re.search(rf"const {name}: (?:f64|usize) = ([0-9.]+);", src).group(1))
        self.assertEqual((const("THETA"), const("KAPPA"), const("KAPPA_DUP"), const("KAPPA_REV")), (es.THETA, es.KAPPA, es.KAPPA_DUP, es.KAPPA_REV))
        self.assertEqual((const("BETA"), const("MAX_LEN"), const("UNSEEN_MIN"), const("TRI_ADD"), const("TRI_KINDS")),
                         (es.BETA, es.MAX_LEN, es.UNSEEN_MIN, es.TRI_ADD, es.TRI_KINDS))

    def test_the_golden_file_is_what_this_script_writes(self):
        with open(es.GOLDEN, encoding="utf-8") as f:
            self.assertEqual(f.read(), es.golden_text())


class Formulas(unittest.TestCase):
    def setUp(self):
        self.tb = es.Table(TINY)

    def test_valid(self):
        for e, ok in [("ok", True), ("gta6", True), ("a", True), ("", False), ("6a", False), ("Ok", False), ("a.b", False), ("a" * 32, True), ("a" * 33, False)]:
            self.assertEqual(es.valid(e), ok, e)

    def test_P_E_given_C(self):
        self.assertEqual(self.tb.p_E_given("用"), (5 + 0.04) / 21)
        self.assertEqual(self.tb.p_E_given("我"), 0.04)                  # no D row: P(E)

    def test_a_table_word(self):
        self.assertEqual(self.tb.lp_word("ok"), math.log10(30 / 50))
        self.assertEqual(self.tb.lp_e("用", "ok"), math.log10((5 + 0.04) / 21) + math.log10(30 / 50))

    def test_an_unseen_word_by_its_letters(self):
        # spell: ^^s ^sp spe pel ell ll$ ; G(^^.) = 2, G(^s.) = 1, G(sp.) = 1, the rest unseen; P_L(5) = (3 + 1) / (4 + 28)
        want = math.log10(4 / 32)
        for g3, g2 in [(2, 2), (1, 1), (1, 1), (0, 0), (0, 0), (0, 0)]:
            want += math.log10((g3 + 0.5) / (g2 + 13.5))
        self.assertEqual(self.tb.lp_char("spell"), want)
        self.assertEqual(self.tb.lp_word("spell"), math.log10(10 / 50) + want)
        self.assertTrue(math.isfinite(self.tb.lp_word("zzzzz")))         # trigrams the table never saw still have a probability
        self.assertGreater(self.tb.lp_word("spell"), self.tb.lp_word("zzzzz"))

    def test_minus_infinity(self):
        for e in ["zzzz", "abcd9", "ls", "a" * 33]:
            self.assertEqual(self.tb.lp_word(e), es.NEG, e)
        no_mass = es.Table(TINY.replace("A\t10", "A\t0"))
        self.assertEqual(no_mass.lp_word("spell"), es.NEG)
        self.assertTrue(math.isfinite(no_mass.lp_word("ok")))

    def test_a_bad_table_is_no_table(self):
        for bad in ["", "T\t0\nN\t1\n", "T\t9\nN\t1\nX\t1\n", "T\t9\nN\t1\nE\tOk\t1\n", "T\t9\nN\t1\nE\tok\t1\nE\tok\t2\n", "T\t9\nN\t1\nG\tab\t1\n",
                    "T\t9\nN\t1\nL\t4\t1\n", "T\t9\nN\t1\nG\ta$b\t1\n", "T\t9\nN\t+1\n", "T\t9\nN\t1\nD\t用\t1\n"]:
            with self.assertRaises(ValueError, msg=repr(bad)):
                es.Table(bad)

    def test_lp_z_and_the_tie(self):
        self.assertEqual(es.lp_z(-2.0, -0.5, 4, 1, 1, kappa=1.0, kappa_dup=3.0, kappa_rev=2.0), -2.0 - 0.5 - 4.0 - 3.0 - 2.0)
        self.assertFalse(es.takes_english(-1.0, -2.0, 1.0))               # a tie is Zhuyin
        self.assertTrue(es.takes_english(-0.9, -2.0, 1.0))
        self.assertFalse(es.takes_english(es.NEG, es.NEG, 3.0))

    def test_sum_and_best(self):
        class LM:
            def prob(self, ctx, w, pb):
                return {"甲": 0.25, "乙": 0.5, "丙": 0.5}[w] * (2 if ctx == "用" else 1) * pb

        class Scorer:
            lm = LM()

            def candidates(self, units):
                return [("甲", -1.0, 0.1), ("乙", -2.0, 0.1), ("丙", -3.0, 0.1)] if units else []
        s, best = es.sum_and_best(Scorer(), "<s>", ("x",))
        self.assertEqual((s, best), (math.log10(0.025 + 0.05 + 0.05), "乙"))   # the first of equals
        self.assertEqual(es.sum_and_best(Scorer(), "用", ("x",))[0], math.log10(0.05 + 0.1 + 0.1))
        self.assertEqual(es.sum_and_best(Scorer(), "<s>", ()), (es.NEG, None))


if __name__ == "__main__":
    unittest.main()
