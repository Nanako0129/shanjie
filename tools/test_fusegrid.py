"""tools/lib/fusegrid.py `pick` and `fuse_pick` skip the candidates a `--tinylm-scores` record lists in `masked`, as core/src/tinylm.rs
`pick_masked` does. The cases are that function's unit test `a_masked_candidate_cannot_win_but_still_counts_for_the_gate`, number for number.
Run: python3 -m unittest tools/test_fusegrid.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
import fusegrid


def rec(ng, ll, masked=None, col=None):
    r = {"n": len(ng), "ng": ng, "ll": ll}
    if masked is not None:
        r["masked"] = masked
    if col is not None:
        r["col"] = col
    return r


class Masked(unittest.TestCase):
    NG, LL = [0.0, -0.5, -3.0], [-5.0, 0.0, -1.0]

    def test_pick_skips_masked(self):
        ng, ll = self.NG, self.LL
        self.assertEqual(fusegrid.pick(rec(ng, ll), 1, 2), 1, "premise: without `masked`, candidate 1 is chosen")
        self.assertEqual(fusegrid.pick(rec(ng, ll, []), 1, 2), 1, "an empty mask is no mask")
        self.assertEqual(fusegrid.pick(rec(ng, ll, [1]), 1, 2), 2, "the better of 0 and 2")
        self.assertEqual(fusegrid.pick(rec(ng, [-1.0, 0.0, -9.0], [1]), 1, 2), 0)
        self.assertEqual(fusegrid.pick(rec(ng, ll, [1]), 1, 2, delta=1.5), 0, "gain 1 below delta 1.5")
        self.assertEqual(fusegrid.pick(rec(ng, ll, [1, 2]), 1, 2), 0, "every other candidate masked keeps the first")

    def test_the_gate_uses_the_masked_second_place(self):
        # ng[0] - ng[1] = 3 >= tau keeps the first although candidate 1 is masked and candidate 2 would win
        self.assertEqual(fusegrid.pick(rec([0.0, -3.0, -3.5], [-9.0, 0.0, 0.0], [1]), 1, 2), 0)

    def test_other_column(self):
        r = rec(self.NG, [0.0, 0.0, 0.0], [1])
        r["ll16"] = self.LL
        self.assertEqual(fusegrid.pick(r, 1, 2, col="ll16"), 2)

    def test_fixed_broken_follows_the_mask(self):
        r = rec(self.NG, self.LL, [1])
        r["ok"] = [0, 1, 0]
        self.assertEqual(fusegrid.fixed_broken([r], 1, 2, 0), (0, 0), "the right candidate is masked: not fixed")
        r["masked"] = []
        self.assertEqual(fusegrid.fixed_broken([r], 1, 2, 0), (1, 0))

    def test_fuse_pick_skips_masked(self):
        # col all equal: the colloquial term is 0 and fuse_pick is the plain masked pick
        ng, ll, col = self.NG, self.LL, [0.0, 0.0, 0.0]
        self.assertEqual(fusegrid.fuse_pick(rec(ng, ll, col=col), 1, 2, 0), 1)
        self.assertEqual(fusegrid.fuse_pick(rec(ng, ll, [1], col), 1, 2, 0), 2)
        self.assertEqual(fusegrid.fuse_pick(rec(ng, ll, [1, 2], col), 1, 2, 0), 0)
        self.assertEqual(fusegrid.fuse_pick(rec(ng, ll, [1], col), 1, 2, 1.5), 0)


if __name__ == "__main__":
    unittest.main()
