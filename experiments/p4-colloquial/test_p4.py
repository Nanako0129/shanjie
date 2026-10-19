"""Tests for p4.py: a hand-built tiny table, synthetic rows; no model, no network, no private data.
Run from the repo root: python3 experiments/p4-colloquial/test_p4.py"""
import math, os, pickle, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import p4 as P  # noqa: E402
import build_lm  # noqa: E402

T = P.T
LEN = T.load_lenient()
NOCELL = ("none", 0.0, 0.0)


def row(truth, cands, ll=None, c=0):
    """cands: [(surface, ng score)] (one word each); the scores record covers the first 8 as the CLI writes it."""
    cs = [(s, sc, [s]) for s, sc in cands]
    rec = {"i": 1, "n": min(8, len(cs)), "ng": [sc for _, sc in cands[:8]], "c": c}
    if ll is not None:
        rec["ll"] = ll
    return {"ctx": "", "truth": truth, "syls": ["x"], "cands": cs, "gold0": None, "c": c, "scores": rec}


def pre(k, lc=None):
    return {"k": k, "lc": {"T1": lc or [0.0] * len(k)}}


class Lc(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        # uni 甲 4, 乙 2 (N = 6, V = 4 with <s> </s>); bigrams with count >= 2 are kept: t(<s>) = 4 (<s>甲 3 kept, <s>乙 1 pruned),
        # t(甲) = 4 (乙 2, </s> 2), t(乙) = 2 (</s> 2); eos_total = 4, D = 0.75.
        pickle.dump({"uni": {"甲": 4, "乙": 2}, "bi": {("<s>", "甲"): 3, ("<s>", "乙"): 1, ("甲", "乙"): 2, ("甲", "</s>"): 2, ("乙", "</s>"): 2}},
                    open(os.path.join(cls.tmp.name, "tiny.pkl"), "wb"))
        old, build_lm.WORK = build_lm.WORK, cls.tmp.name
        try:
            data = build_lm.build({}, corpora=[("tiny.pkl", 1)])[0]
        finally:
            build_lm.WORK = old
        path = os.path.join(cls.tmp.name, "tiny.sjlm")
        open(path, "wb").write(data)
        cls.tb = P.Table(path)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_hand_computed(self):
        # n1 = N + V + 1 = 11. P(甲|<s>) = 2.25/4 + (1 - 2.25/4) * 5/11; P(乙|甲) = 1.25/4 + (1 - 2.5/4) * 3/11; P(</s>|乙) = 1.25/2 + (1 - 1.25/2) * p_eos, p_eos = 4/6
        want = math.log10(0.5625 + 0.4375 * 5 / 11) + math.log10(0.3125 + 0.375 * 3 / 11) + math.log10(0.625 + 0.375 * 4 / 6)
        self.assertAlmostEqual(self.tb.lc(["甲", "乙"]), want, places=12)
        self.assertAlmostEqual(want, -0.55859, places=5)   # guards the hand arithmetic itself

    def test_word_absent_from_the_table(self):
        # 丙: c = 0 -> unigram 1/11 under <s> (back 0.4375); no context of its own -> P(</s>|丙) = p_eos = 4/6
        want = math.log10(0.4375 / 11) + math.log10(4 / 6)
        self.assertAlmostEqual(self.tb.lc(["丙"]), want, places=12)

    def test_pruned_bigram_gets_back_off_mass_only(self):
        self.assertAlmostEqual(self.tb.logp("<s>", "乙"), math.log10(0.4375 * 3 / 11), places=12)


class Counting(unittest.TestCase):
    def test_k_counts_overlay_words_not_in_base_with_multiplicity(self):
        ovl, base = {"新詞", "舊詞"}, {"舊詞"}
        self.assertEqual(P.k_of(["新詞", "舊詞", "新詞", "別的"], ovl, base), 2)
        self.assertEqual(P.k_of(["舊詞"], ovl, base), 0)


class Rerank(unittest.TestCase):
    def test_rank_changes_by_lc_and_k(self):
        r = row("乙", [("甲", -1.0), ("乙", -1.2)], ll=[0.0, 0.0])
        order, choice, affected = P.eval_row(r, pre([0, 0], [0.0, 0.5]), ("T1", 1.0, 0.0))
        self.assertEqual((order, choice, affected), ([1, 0], 1, True))
        order, choice, affected = P.eval_row(r, pre([1, 0]), ("none", 0.0, 0.5))   # penalise the first's overlay word: -1.5 against -1.2
        self.assertEqual((order, choice, affected), ([1, 0], 1, True))
        _, _, affected = P.eval_row(r, pre([1, 1]), ("none", 0.0, 0.5))           # a uniform shift changes nothing
        self.assertFalse(affected)

    def test_new_top8_member_without_ll_needs_rescoring(self):
        cands = [(f"w{i}", -1.0 - 0.1 * i) for i in range(10)]
        r = row("w9", cands, ll=[0.0] * 8)
        order, choice, affected = P.eval_row(r, pre([0] * 10, [0.0] * 9 + [5.0]), ("T1", 1.0, 0.0))   # candidate 9 jumps in
        self.assertEqual((order[0], choice, affected), (9, None, True))
        order, choice, _ = P.eval_row(r, pre([0] * 10, [0.0] * 5 + [1.0] + [0.0] * 4), ("T1", 1.0, 0.0))     # candidate 5 reorders inside the 8
        self.assertEqual(order[0], 5)
        self.assertIsNotNone(choice)

    def test_row_without_ll_follows_the_ngram_first(self):
        r = row("乙", [("甲", -1.0), ("乙", -1.2)])
        self.assertEqual(P.eval_row(r, pre([0, 0], [0.0, 0.5]), ("T1", 1.0, 0.0))[:2], ([1, 0], 1))

    def test_tau_sees_s_not_ng(self):
        # ng margin 3 >= tau 2: the baseline keeps the first. With lc the margin of s drops to 1 < tau and the LL (alpha 0.3, +10) moves the pick.
        r = row("乙", [("甲", -1.0), ("乙", -4.0)], ll=[0.0, 10.0])
        self.assertEqual(P.eval_row(r, pre([0, 0]), NOCELL)[1], 0)
        self.assertEqual(P.eval_row(r, pre([0, 0], [0.0, 2.0]), ("T1", 1.0, 0.0))[1], 1)

    def test_fusion_sum_uses_s(self):
        # baseline: gap 1.0, fused gain 0.3 * 2 - 1.0 < 0 -> keep the first. With s the gap is 0.1 and the gain 0.5 >= delta 0.25 -> the second.
        r = row("乙", [("甲", -1.0), ("乙", -2.0)], ll=[0.0, 2.0])
        self.assertEqual(P.eval_row(r, pre([0, 0]), NOCELL)[1], 0)
        self.assertEqual(P.eval_row(r, pre([0, 0], [0.0, 0.9]), ("T1", 1.0, 0.0))[1], 1)


class CellStats(unittest.TestCase):
    def setUp(self):
        # r0: the n-gram first is wrong, lc fixes it; r1: the n-gram first is right, lc breaks it; r2: right and untouched
        self.rows = [row("乙", [("甲", -1.0), ("乙", -1.2)], ll=[0.0, 0.0]), row("甲", [("甲", -1.0), ("乙", -1.2)], ll=[0.0, 0.0]),
                     row("甲", [("甲", -1.0), ("乙", -9.0)], ll=[0.0, 0.0])]
        self.ok = [LEN(r["cands"][r["c"]][0]) == LEN(r["truth"]) for r in self.rows]
        self.pre = [pre([0, 0], [0.0, 0.5]), pre([0, 0], [0.0, 0.5]), pre([0, 0], [0.0, 0.5])]

    def test_fixes_breaks_net_and_p(self):
        o = P.eval_cell(self.rows, self.ok, self.pre, ("T1", 1.0, 0.0), LEN, {})
        self.assertEqual((o["ng_fixes"], o["ng_breaks"], o["ng_net"]), (1, 1, 0))
        self.assertEqual((o["fixes"], o["breaks"], o["net"], o["rescoring"], o["affected"]), (1, 1, 0, 0, 2))
        self.assertEqual(o["ng_p"], 1.0)

    def test_baseline_cell_is_all_zero(self):
        o = P.eval_cell(self.rows, self.ok, self.pre, NOCELL, LEN, {})
        self.assertEqual((o["ng_net"], o["net"], o["affected"]), (0, 0, 0))

    def test_mcnemar_exact_two_sided(self):
        rows = [row("乙", [("甲", -1.0), ("乙", -1.2)], ll=[0.0, 0.0]) for _ in range(5)]
        o = P.eval_cell(rows, [False] * 5, [pre([0, 0], [0.0, 0.5])] * 5, ("T1", 1.0, 0.0), LEN, {})
        self.assertEqual((o["ng_fixes"], o["ng_breaks"]), (5, 0))
        self.assertAlmostEqual(o["ng_p"], 0.0625)    # 2 / 2**5

    def test_d3_rows_fixed_at_the_ngram_level(self):
        o = P.eval_cell(self.rows, self.ok, self.pre, ("T1", 1.0, 0.0), LEN, {0: "ii", 1: "iii"})
        self.assertEqual((o["ii_fixed"], o["iii_fixed"]), (1, 0))    # row 0 turns right; row 1 (iii) turns wrong; row 2 is not a classified row

    def test_rescoring_not_counted_as_fix_or_break_and_flags_unreliable(self):
        cands = [(f"w{i}", -1.0 - 0.1 * i) for i in range(10)]
        rows = [row("w9", cands, ll=[0.0] * 8)] * 3
        o = P.eval_cell(rows, [False] * 3, [pre([0] * 10, [0.0] * 9 + [5.0])] * 3, ("T1", 1.0, 0.0), LEN, {})
        self.assertEqual((o["rescoring"], o["fixes"], o["breaks"], o["ng_fixes"]), (3, 0, 0, 3))
        self.assertTrue(o["unreliable"])
        self.assertFalse(P.unreliable(1, 10))      # exactly 10% is not more than 10%
        self.assertTrue(P.unreliable(2, 10))


class Selection(unittest.TestCase):
    def cells(self, nets):
        return {k: {"ng_net": v} for k, v in nets.items()}

    def test_max_net_then_small_mu_then_small_gamma_then_t1(self):
        self.assertEqual(P.choose(self.cells({("T1", 0.5, 0.0): 5, ("T2", 0.2, 0.5): 9, ("T1", 1.0, 0.0): 7})), ("T2", 0.2, 0.5))
        self.assertEqual(P.choose(self.cells({("T1", 0.5, 0.0): 9, ("T2", 0.2, 0.5): 9, ("T1", 1.0, 0.0): 9})), ("T2", 0.2, 0.5))
        self.assertEqual(P.choose(self.cells({("T1", 0.5, 0.25): 9, ("T2", 0.5, 0.0): 9})), ("T2", 0.5, 0.0))
        self.assertEqual(P.choose(self.cells({("T2", 0.5, 0.25): 9, ("T1", 0.5, 0.25): 9})), ("T1", 0.5, 0.25))

    def test_baseline_cell_is_never_chosen(self):
        self.assertEqual(P.choose(self.cells({NOCELL: 0, ("none", 0.0, 0.25): -3})), ("none", 0.0, 0.25))

    def test_grid_shape(self):
        self.assertEqual(len(P.GRID), 20)
        self.assertEqual(len(set(P.GRID)), 20)
        self.assertNotIn(NOCELL, P.GRID)


class BaselineCheck(unittest.TestCase):
    def good(self):
        rows = [row("乙", [("甲", -1.0), ("乙", -1.2)], ll=[0.0, 0.0]), row("甲", [("甲", -1.0), ("乙", -1.2)], ll=[0.0, 0.0])]
        ok = [LEN(r["cands"][r["c"]][0]) == LEN(r["truth"]) for r in rows]
        return rows, ok, [pre([0, 0]), pre([0, 0])]

    def test_consistent_baseline_passes(self):
        rows, ok, p = self.good()
        self.assertEqual(P.check_baseline(rows, ok, p, "chatclause", LEN), {"final_fixes_vs_ng_first": 0, "final_breaks_vs_ng_first": 0})

    def test_fusion_disagreeing_with_rowstats_stops(self):
        rows, ok, p = self.good()
        rows[0]["c"] = 1          # the CLI would have chosen the second; the recomputed fusion keeps the first
        with self.assertRaisesRegex(P.Stop, "row 1"):
            P.check_baseline(rows, ok, p, "chatclause", LEN)

    def test_ok_flag_disagreeing_stops(self):
        rows, ok, p = self.good()
        with self.assertRaisesRegex(P.Stop, "row 2"):
            P.check_baseline(rows, [ok[0], not ok[1]], p, "chatclause", LEN)

    def test_discordtune_requires_d1_numbers(self):
        rows, ok, p = self.good()
        with self.assertRaisesRegex(P.Stop, "D1"):
            P.check_baseline(rows, ok, p, "discordtune", LEN)

    def test_unsorted_dump_stops(self):
        rows, ok, p = self.good()
        rows[0]["scores"]["ng"] = [-1.2, -1.0]     # the n-gram first would not be dump rank 0
        with self.assertRaisesRegex(P.Stop, "rank 0"):
            P.check_baseline(rows, ok, p, "chatclause", LEN)


if __name__ == "__main__":
    unittest.main()
