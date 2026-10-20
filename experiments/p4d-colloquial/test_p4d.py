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


class Anchor(unittest.TestCase):
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
