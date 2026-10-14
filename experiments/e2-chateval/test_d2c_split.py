"""d2c_split.py (rerank-strength contract section 11.5): synthetic score records, one per class, no data.

Mutations checked by hand (each makes a test fail): j without alpha * ll; (i) as ll[g] > ll[0]; D2b without the delta test. The
contract's "g = the last ok" mutation has no target: no class reads g, because D2b, rescued and (i) all test `ok` at an index
(any right candidate counts, as in the triage contract)."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import d2c_split as D  # noqa: E402
import fusegrid as fg  # noqa: E402

A, T, DL = 1.0, 2.0, 0.5   # alpha, tau, delta for the synthetic rows


def rec(ng, ll, ok):
    r = {"i": 1, "n": len(ng), "ng": ng, "ok": ok}
    if ll is not None:
        r["ll"] = ll
    return r


ROWS = {
    "first_right": rec([-1.0, -1.5], [-3.0, -1.0], [1, 0]),                      # outside: n-gram first is right
    "not_in_top": rec([-1.0, -1.5], [-3.0, -1.0], [0, 0]),                       # outside: no right answer in the top n
    "D2_none": rec([-1.0, -1.5], None, [0, 1]),                                   # no ll
    "D2a": rec([-1.0, -3.5], [-9.0, -1.0], [0, 1]),                               # margin 2.5 >= tau: never reranked
    "D2b": rec([-1.0, -1.5], [-2.0, -1.2], [0, 1]),                               # s = [-3.0, -2.7]: j = 1 right, gap 0.3 < delta
    "rescued": rec([-1.0, -1.5], [-4.0, -1.0], [0, 1]),                           # j = 1 right, gap 2.5 >= delta: pick is right
    "D2c_i": rec([-1.0, -1.2, -1.9], [-3.0, -2.0, -1.9], [0, 0, 1]),              # s = [-4.0, -3.2, -3.8]: j = 1 wrong; model alone picks 2 (right)
    "D2c_ii": rec([-1.0, -1.2, -1.9], [-6.0, -1.0, -2.0], [0, 0, 1]),             # ll[j] > ll[g] > ll[0]: model alone picks j
    "last_ok": rec([-1.0, -1.2, -1.4], [-5.0, -1.2, -1.0], [0, 1, 1]),            # g is the first ok (1); lm_alone picks 2 (also ok)
}


class Classify(unittest.TestCase):
    def test_each_row_lands_in_its_class(self):
        want = {"first_right": None, "not_in_top": None, "D2_none": "D2_none", "D2a": "D2a", "D2b": "D2b",
                "rescued": "rescued", "D2c_i": "D2c_i", "D2c_ii": "D2c_ii"}
        for name, k in want.items():
            self.assertEqual(D.classify(ROWS[name], A, T, DL), k, name)

    def test_rescued_is_exactly_where_pick_is_right(self):
        for name, r in ROWS.items():
            k = D.classify(r, A, T, DL)
            if k is None or k == "D2_none":
                continue
            self.assertEqual(k == "rescued", bool(r["ok"][fg.pick(r, A, T, delta=DL)]), name)

    def test_d2b_needs_the_delta_gap(self):
        self.assertEqual(D.classify(ROWS["rescued"], A, T, 3.0), "D2b")   # the same row under a larger delta is not rescued

    def test_alpha_matters_for_j(self):
        # alpha 0 ranks by n-gram only, so j = 0 (wrong): the row the fusion rescues at alpha 1 becomes D2c, and since the
        # model alone still prefers the right candidate it is (i)
        self.assertEqual(D.classify(ROWS["rescued"], 0.0, T, DL), "D2c_i")
        self.assertEqual(D.classify(ROWS["D2c_ii"], 0.0, T, DL), "D2c_ii")

    def test_first_ok_is_the_answer(self):
        self.assertEqual(D.classify(ROWS["last_ok"], A, T, DL), "rescued")

    def test_count_and_share(self):
        c = D.count(list(ROWS.values()), (A, T, DL))
        self.assertEqual(c["in_range"], 7)
        self.assertEqual((c["D2c_i"], c["D2c_ii"]), (1, 1))
        self.assertTrue(D.line("x", c).endswith("| 0.500 |"))


if __name__ == "__main__":
    unittest.main()
