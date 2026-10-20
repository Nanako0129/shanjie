import math, os, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import p4d as P  # noqa: E402
import triage  # noqa: E402


def sc(ng, ll, ok, c=0):
    return {"ng": ng, "ll": ll, "ok": ok, "ed": [1 - o for o in ok], "glen": 4, "c": c}


class FinalIndex(unittest.TestCase):
    def test_mu_zero_is_the_shipping_pick(self):
        r = sc([0.0, -0.4, -1.0], [0.0, 1.5, 3.0], [0, 1, 0])
        for a in P.ALPHAS:
            for g in P.GATES:
                self.assertEqual(P.final_index(r, [5.0, -3.0, 9.0], 0.0, a, g), triage.pick(r["ng"], r["ll"], a, P.TAU, P.DELTA))

    def test_the_term_acts_through_the_margin(self):
        # col relative to the anchor: candidate 1 gains (mu / alpha) * 2 in ll, i.e. mu * 2 = 1.0 in the fused score
        r = sc([0.0, -0.5], [0.0, 0.0], [0, 1])
        self.assertEqual(P.final_index(r, [0.0, 2.0], 0.5, 0.5, P.INF), 1)        # -0.5 + 1.0 = 0.5 >= 0 + 0.25
        self.assertEqual(P.final_index(r, [7.0, 9.0], 0.5, 0.5, P.INF), 1)        # only the difference to the anchor counts
        self.assertEqual(P.final_index(r, [0.0, 0.0], 0.5, 0.5, P.INF), 0)        # no term, no switch
        self.assertEqual(P.final_index(r, [0.0, 2.0], 0.15, 0.5, P.INF), 0)       # -0.5 + 0.3 < 0.25: delta keeps the anchor

    def test_the_gate_damps_the_term_when_the_small_model_disagrees(self):
        r = sc([0.0, -0.5], [0.0, -2.0], [0, 1])
        # ungated: -0.5 + 0.5 * (-2 + 1 * 4) = 0.5 >= 0.25 switches; gate 0.5: g = 1 / (1 + e^4), the term nearly vanishes
        self.assertEqual(P.final_index(r, [0.0, 4.0], 0.5, 0.5, P.INF), 1)
        self.assertEqual(P.final_index(r, [0.0, 4.0], 0.5, 0.5, 0.5), 0)
        g = 1 / (1 + math.exp(4))
        self.assertLess(-0.5 + 0.5 * (-2 + g * 4), 0.25)

    def test_the_term_is_relative_to_the_anchor_under_the_gate(self):
        # gate 0.5, ll [0, -0.2]: g_0 = 0.5, g_1 = sigmoid(-0.4) = 0.401. Relative: candidate 1 gains 0.401 * 4 in ll, fused
        # -0.5 + 0.5 * 1.404 = 0.202 < 0.25, no switch. The absolute form (col_i, not col_i - col_0) would give the anchor
        # 0.5 * (-4) = -2 in ll, fused -1 against -0.6 for candidate 1, and switch.
        r = sc([0.0, -0.5], [0.0, -0.2], [0, 1])
        self.assertEqual(P.final_index(r, [-4.0, 0.0], 0.5, 0.5, 0.5), 0)

    def test_the_gate_measures_agreement_against_the_anchor(self):
        # real ll are far below 0: ll [-21, -20] gives g_1 = sigmoid(2) = 0.881, ll_eff_1 = -20 + 3.52, fused -8.74 against
        # -10.5: switch. A gate on ll_1 alone (sigmoid(-40), about 0) would drop the term: fused -10.5 against -10.5, no switch.
        r = sc([0.0, -0.5], [-21.0, -20.0], [0, 1])
        self.assertEqual(P.final_index(r, [0.0, 4.0], 0.5, 0.5, 0.5), 1)

    def test_sigmoid_does_not_overflow(self):
        self.assertEqual(P.sigmoid(-1e6), 0.0)
        self.assertEqual(P.sigmoid(1e6), 1.0)
        self.assertAlmostEqual(P.sigmoid(0.0), 0.5)

    def test_cols_are_lc_minus_half_k_for_the_first_8(self):
        pre = [{"lc": {"T2": [-1.0 * i for i in range(10)]}, "k": [i % 2 for i in range(10)]}]
        self.assertEqual(P.cols_from(pre), [[-i - 0.5 * (i % 2) for i in range(8)]])

    def test_tau_and_rows_without_ll(self):
        self.assertEqual(P.final_index(sc([0.0, -2.0], [0.0, 9.0], [0, 1]), [0.0, 9.0], 0.5, 0.5, P.INF), 0)   # n-gram gap >= tau
        self.assertEqual(P.final_index({"ng": [0.0, -0.1], "ok": [0, 1]}, [0.0, 9.0], 0.5, 0.5, P.INF), 0)


class Selection(unittest.TestCase):
    def s(self, f, b):
        return {"fixes": f, "breaks": b, "net": f - b}

    def test_largest_u2_not_largest_net(self):
        cells = [(0.5, 0.5, P.INF, self.s(180, 80)), (0.25, 0.5, 0.5, self.s(120, 40))]   # u2 20 vs 40
        self.assertEqual(P.choose(cells)[:3], (0.25, 0.5, 0.5))

    def test_negative_u2_never_qualifies(self):
        self.assertIsNone(P.choose([(0.15, 0.3, P.INF, self.s(10, 6))]))
        self.assertIsNotNone(P.choose([(0.15, 0.3, P.INF, self.s(10, 5))]))   # u2 0 qualifies (ratio 0.5)

    def test_equal_u2_takes_the_larger_net(self):
        cells = [(0.15, 0.3, P.INF, self.s(20, 5)), (0.5, 0.5, 0.5, self.s(30, 10))]   # u2 10 both, net 15 vs 20
        self.assertEqual(P.choose(cells)[:3], (0.5, 0.5, 0.5))

    def test_gate_order_inf_then_1_then_half(self):
        same = self.s(30, 10)
        self.assertEqual(P.choose([(0.15, 0.3, 0.5, same), (0.15, 0.3, 1.0, same)])[:3], (0.15, 0.3, 1.0))

    def test_ties(self):
        same = self.s(30, 10)
        cells = [(0.35, 0.3, P.INF, same), (0.15, 0.5, 0.5, same), (0.15, 0.3, 1.0, same), (0.15, 0.5, P.INF, same)]
        self.assertEqual(P.choose(cells)[:3], (0.15, 0.5, P.INF))                # smaller mu, then gate inf
        self.assertEqual(P.choose([(0.15, 0.5, 1.0, same), (0.15, 0.3, 1.0, same)])[:3], (0.15, 0.3, 1.0))   # alpha 0.3 last tie

    def test_grid_and_cells(self):
        self.assertEqual(len(P.GRID), 24)
        self.assertNotIn(P.SHIP, P.GRID)
        self.assertEqual(P.parse_cell("0.25,0.5,inf"), (0.25, 0.5, P.INF))
        self.assertEqual(P.parse_cell("0.5,0.3,0.5"), (0.5, 0.3, 0.5))
        with self.assertRaises(SystemExit):
            P.parse_cell("0.2,0.5,inf")
        self.assertEqual(P.label((0.5, 0.3, P.INF)), "0.5,0.3,inf")


class Reach(unittest.TestCase):
    def test_counts_swap_fixes_and_the_ones_beyond_tau(self):
        rows = [sc([0.0, -0.5], [0.0, 0.0], [0, 1]), sc([0.0, -3.0], [0.0, 0.0], [0, 1]), sc([0.0, -0.5], [0.0, 0.0], [1, 0])]
        cols = [[0.0, 2.0], [0.0, 8.0], [0.0, 2.0]]
        # row 1: swap fixes, gap 0.5; row 2: swap fixes, gap 3 >= tau; row 3: the shipping final is already right
        self.assertEqual(P.reach(rows, cols, [False, False, True], 0.5), {"swap_fixes": 2, "swap_fixes_gap_ge_tau": 1})
        # the n-gram first right and no swap (the term does not move it): not P4's fix
        self.assertEqual(P.reach([sc([0.0, -0.5], [0.0, 0.0], [1, 0])], [[0.0, 0.0]], [False], 0.5)["swap_fixes"], 0)


class Anchor(unittest.TestCase):
    def test_setting_anchor(self):
        rows = [sc([0.0, -0.1], [0.0, 2.0], [0, 1], c=1)]
        old = dict(P.SETTING_ANCHORS)
        self.addCleanup(lambda: (P.SETTING_ANCHORS.clear(), P.SETTING_ANCHORS.update(old)))
        P.SETTING_ANCHORS["x"] = (1, 0)                                         # shipping right, n-gram first wrong: 1 fix
        P.check_anchor(rows, [[0.0, 0.0]], [True], "x")
        P.SETTING_ANCHORS["x"] = (0, 0)
        with self.assertRaises(SystemExit):
            P.check_anchor(rows, [[0.0, 0.0]], [True], "x")

    def test_anchor_stops_on_a_pick_mismatch(self):
        rows = [sc([0.0, -0.1], [0.0, 2.0], [0, 1], c=1)]                       # -0.1 + 0.3 * 2 = 0.5 >= 0.25: shipping picks 1
        P.check_anchor(rows, [[0.0, 0.0]], [True])
        rows[0]["c"] = 0
        with self.assertRaises(SystemExit):
            P.check_anchor(rows, [[0.0, 0.0]], [True])
        rows[0]["c"] = 1
        with self.assertRaises(SystemExit):
            P.check_anchor(rows, [[0.0, 0.0]], [False])                        # ok differs from the rowstats


if __name__ == "__main__":
    unittest.main()
