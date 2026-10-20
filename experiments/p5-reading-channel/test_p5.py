import math, os, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import p5 as Q  # noqa: E402
import p4c  # noqa: E402

P = Q.P


def sc(ng, ll, ok, c=0):
    return {"ng": ng, "ll": ll, "ok": ok, "ed": [1 - o for o in ok], "glen": 4, "c": c}


def stat(f, b):
    return {"fixes": f, "breaks": b, "net": f - b, "p": 0.001, "error_ratio": b / f if f else 0.0}


class ReadingLogP(unittest.TestCase):
    def test_one_reading_has_no_penalty(self):
        self.assertEqual(Q.reading_logp({(("a",), "w"): -6.8})[(("a",), "w")], 0.0)

    def test_normalised_over_the_readings_of_the_same_word_only(self):
        lp = Q.reading_logp({(("a",), "w"): -1.0, (("b",), "w"): -2.0, (("a",), "v"): -3.0})
        self.assertAlmostEqual(10 ** lp[(("a",), "w")] + 10 ** lp[(("b",), "w")], 1.0)
        self.assertAlmostEqual(lp[(("a",), "w")], -math.log10(1 + 0.1))
        self.assertEqual(lp[(("a",), "v")], 0.0)                      # another word's readings do not count

    def test_no_overflow_or_underflow_far_from_zero(self):
        lp = Q.reading_logp({(("a",), "w"): -400.0, (("b",), "w"): -400.0})
        self.assertAlmostEqual(lp[(("a",), "w")], -math.log10(2))

    def test_real_lexicon_hui_kuai(self):
        # README: 會 reads ㄏㄨㄟˋ -2.617, ㄎㄨㄞˋ -6.8, ㄏㄨㄟˇ -6.8
        import d23 as D
        lp = Q.reading_logp(D.load_lex_scores()[0])
        self.assertAlmostEqual(lp[(("ㄎㄨㄞˋ",), "會")], -6.8 - math.log10(10 ** -2.61726162 + 2 * 10 ** -6.8), places=6)
        self.assertAlmostEqual(lp[(("ㄎㄨㄞˋ",), "會")], -4.1828, places=3)
        self.assertLess(lp[(("ㄏㄨㄟˋ",), "會")], 0)
        self.assertGreater(lp[(("ㄏㄨㄟˋ",), "會")], -1e-3)


class Channel(unittest.TestCase):
    LP = {(("x", "y"), "ab"): -0.5, (("z",), "c"): -3.0, (("q",), "d"): 0.0}

    def test_syllables_are_cut_over_multi_character_words_in_order(self):
        t = Q.word_terms(["x", "y", "z", "q"], ["ab", "c", "d"], self.LP)
        self.assertEqual(t, [-0.5, -3.0, 0.0])
        # the same syllables cut differently look up other readings: (z,) 'c' is not (x,y) 'ab'
        self.assertEqual(Q.word_terms(["z", "x", "y", "q"], ["c", "ab", "d"], self.LP), [-3.0, -0.5, 0.0])

    def test_missing_pair_is_none_and_counts_zero(self):
        t = Q.word_terms(["x", "y", "unseen"], ["ab", "c"], self.LP)                          # (unseen,) c is not in the lexicon
        self.assertEqual(t, [-0.5, None])
        self.assertEqual(Q.ch_of(t, 4), -0.5)

    def test_words_that_do_not_cover_the_syllables_are_uncut(self):
        self.assertIsNone(Q.word_terms(["x", "y"], ["ab", "c"], self.LP))
        self.assertIsNone(Q.word_terms(["x", "y"], None, self.LP))
        self.assertEqual(Q.ch_of(None, 4), 0.0)

    def test_the_clip_at_minus_c(self):
        self.assertEqual(Q.ch_of([-6.0], 2), -2.0)
        self.assertEqual(Q.ch_of([-6.0, -0.5, -3.0], 1), -2.5)        # -1, -0.5, -1
        self.assertEqual(Q.ch_of([-1.5], 2), -1.5)                    # above the clip: untouched
        self.assertEqual(Q.ch_of([-6.0], 4), -4.0)

    def test_counts_missing_and_clipped(self):
        rows = [[[-0.5, None, -2.5], None, [-5.0]]]
        n = Q.term_counts(rows)
        self.assertEqual((n["candidates"], n["uncut_candidates"], n["word_slots"], n["missing_pairs"]), (3, 1, 4, 1))
        self.assertEqual(n["clipped"], {"1": 2, "2": 2, "4": 1})      # -2.5 and -5 below -1 and -2; only -5 below -4


class Fusion(unittest.TestCase):
    def test_anchor_keeps_its_own_ll_and_others_move_by_the_difference(self):
        r = sc([0.0, -0.5, -0.9], [0.0, 1.0, 2.0], [0, 1, 0])
        ng, eff, alpha = Q.fused(r, [0.0] * 3, [-5.0, -3.0, -6.0], 0.5, False)
        self.assertEqual(alpha, 0.3)
        self.assertEqual(eff[0], 0.0)                                  # ch_0 subtracted: the anchor's own term is 0
        self.assertAlmostEqual(eff[1], 1.0 + (0.5 / 0.3) * 2.0)
        self.assertAlmostEqual(eff[2], 2.0 + (0.5 / 0.3) * -1.0)

    def test_alpha_is_03_off_and_05_on(self):
        r = sc([0.0, -0.5], [0.0, 0.0], [0, 1])
        self.assertEqual(Q.fused(r, [0.0, 0.0], [0.0, 0.0], 0.5, False)[2], 0.3)
        self.assertEqual(Q.fused(r, [0.0, 0.0], [0.0, 0.0], 0.5, True)[2], 0.5)
        # the channel gain is (nu / alpha) in ll, so nu * d in the fused score at either alpha (colloquial col 0: no other term)
        off = Q.fused(r, [0.0, 0.0], [0.0, 2.0], 0.5, False)
        on = Q.fused(r, [0.0, 0.0], [0.0, 2.0], 0.5, True)
        self.assertAlmostEqual(0.3 * off[1][1], 0.5 * 2.0)
        self.assertAlmostEqual(0.5 * on[1][1], 0.5 * 2.0)

    def test_the_channel_acts_through_the_margin(self):
        r = sc([0.0, -0.5], [0.0, 0.0], [0, 1])
        # fused gain nu * d: -0.5 + 0.5 * 2 = 0.5 >= delta 0.25 switches; d 1 gives 0 and does not
        self.assertEqual(Q.final_index(r, [0.0, 0.0], [0.0, 2.0], 0.5, False), 1)
        self.assertEqual(Q.final_index(r, [0.0, 0.0], [0.0, 1.0], 0.5, False), 0)
        self.assertEqual(Q.final_index(r, [0.0, 0.0], [0.0, -2.0], 0.5, False), 0)       # a worse reading never helps
        self.assertEqual(Q.final_index(r, [0.0, 0.0], [0.0, 2.0], 0.1, False), 0)       # nu 0.1: 0.2 < 0.25 - 0.5
        self.assertEqual(Q.final_index(sc([0.0, -2.0], [0.0, 0.0], [0, 1]), [0.0, 0.0], [0.0, 9.0], 1.0, False), 0)   # n-gram gap >= tau
        self.assertEqual(Q.final_index({"ng": [0.0, -0.1], "ok": [0, 1]}, [0.0, 0.0], [0.0, 9.0], 1.0, False), 0)    # no ll

    def test_nu_zero_off_is_shipping_and_nu_zero_on_is_the_p4d_cell(self):
        rows = [sc([0.0, -0.4, -1.0], [0.0, 1.5, 3.0], [0, 1, 0]), sc([0.0, -0.1], [-21.0, -20.0], [0, 1]), sc([0.0, -0.5], [0.0, -0.2], [0, 1])]
        cols = [[0.0, 2.0, -1.0], [0.0, 4.0], [-4.0, 0.0]]
        ch = [[7.0, -3.0, 1.0], [-2.0, -9.0], [0.0, 5.0]]                                  # a channel that must not matter at nu 0
        for r, col, c in zip(rows, cols, ch):
            self.assertEqual(Q.final_index(r, col, c, 0.0, False), P.triage.pick(r["ng"], r["ll"], 0.3, P.TAU, P.DELTA))
            self.assertEqual(Q.final_index(r, col, c, 0.0, True), P.final_index(r, col, *Q.ON))

    def test_both_terms_add_before_the_pick(self):
        r = sc([0.0, -0.5], [0.0, 0.0], [0, 1])
        col, ch = [0.0, 1.0], [0.0, 1.0]
        # on: colloquial gate 1.0 at ll equal: g 0.5, gain 0.5 * (0.5 / 0.5) * 1 = 0.5 in ll; channel nu 0.5: 0.5 / 0.5 * 1 = 1.0 in ll
        ng, eff, alpha = Q.fused(r, col, ch, 0.5, True)
        self.assertAlmostEqual(eff[1], 0.5 + 1.0)
        self.assertEqual(Q.final_index(r, col, ch, 0.5, True), 1)                         # -0.5 + 0.5 * 1.5 = 0.25 >= delta


class Selection(unittest.TestCase):
    def test_largest_u2_then_ties(self):
        self.assertEqual(Q.choose([(0.5, 2, False, stat(180, 80)), (0.25, 2, False, stat(120, 40))])[:3], (0.25, 2, False))   # u2 20 vs 40
        same = stat(30, 10)
        self.assertEqual(Q.choose([(0.5, 2, False, stat(31, 10)), (0.1, 1, False, same)])[:3], (0.5, 2, False))              # larger net
        self.assertEqual(Q.choose([(0.5, 2, False, stat(30, 10)), (0.25, 2, False, same)])[:3], (0.25, 2, False))           # smaller nu
        self.assertEqual(Q.choose([(0.25, 1, False, same), (0.25, 4, False, same)])[:3], (0.25, 4, False))                  # larger c
        self.assertEqual(Q.choose([(0.25, 2, True, same), (0.25, 2, False, same)])[:3], (0.25, 2, False))                   # off first

    def test_negative_u2_never_qualifies(self):
        self.assertIsNone(Q.choose([(0.1, 1, False, stat(10, 6))]))
        self.assertIsNotNone(Q.choose([(0.1, 1, False, stat(10, 5))]))

    def test_grid_and_cells(self):
        self.assertEqual(len(Q.GRID), 24)
        self.assertEqual(len(set(Q.GRID)), 24)
        self.assertNotIn(Q.SHIP, Q.GRID)
        self.assertNotIn(Q.P4D_CELL, Q.GRID)
        self.assertEqual(Q.parse_cell("0.25,2,on"), (0.25, 2, True))
        self.assertEqual(Q.parse_cell("0.5,4,off"), (0.5, 4, False))
        self.assertEqual(Q.parse_cell("0,1,on"), Q.P4D_CELL)
        with self.assertRaises(SystemExit):
            Q.parse_cell("0.2,2,on")
        with self.assertRaises(SystemExit):
            Q.parse_cell("0.25,3,on")
        self.assertEqual(Q.label((0.25, 2, True)), "0.25,2,on")


class Verdict(unittest.TestCase):
    def test_ratio_limit_is_06_not_05(self):
        s = stat(100, 55)                                                   # ratio 0.55: only the 0.6 rule passes it
        self.assertTrue(Q.verdict("3", s))
        self.assertFalse(p4c.clause_verdict("3", s))                        # p4c is the 0.5 version and stays that way
        self.assertTrue(Q.verdict("3", stat(100, 60)))                      # 0.6 exactly passes
        self.assertFalse(Q.verdict("3", stat(100, 61)))
        self.assertFalse(Q.verdict("3", stat(0, 0)))                        # net > 0 is required

    def test_clause_1(self):
        self.assertTrue(Q.verdict("1", stat(50, 30)))                       # breaks 30 > 26 but ratio 0.6
        self.assertFalse(Q.verdict("1", stat(50, 31)))                      # ratio 0.62
        self.assertFalse(Q.verdict("1", {**stat(10, 26), "p": 0.01}))       # significantly worse
        self.assertTrue(Q.verdict("1", {**stat(30, 26), "p": 0.01}))        # net >= 0 passes whatever p says
        self.assertTrue(Q.verdict("1", {**stat(10, 20), "p": 0.2}))         # not significant, breaks <= 26

    def test_clause_2a(self):
        self.assertTrue(Q.verdict("2a", stat(0, 0)))
        self.assertTrue(Q.verdict("2a", stat(2, 2)))
        self.assertFalse(Q.verdict("2a", stat(1, 3)))
        self.assertFalse(Q.verdict("2a", stat(1, 2)))


if __name__ == "__main__":
    unittest.main()
