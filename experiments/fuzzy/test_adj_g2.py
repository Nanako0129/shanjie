import random
import unittest

import adj_g2 as A
import fuzzy as F


class AdjG2(unittest.TestCase):
    def test_sides_split_the_research_table(self):
        left, right = A.one_side("left"), A.one_side("right")
        self.assertEqual(left["ㄗ"], ["ㄔ"])   # keys t y u: ㄔ ㄗ ㄧ
        self.assertEqual(right["ㄗ"], ["ㄧ"])
        for c, nb in A.FULL.items():
            self.assertEqual(sorted(left[c] + right[c]), sorted(nb), c)

    def test_one_sided_tolerance_keeps_two_sided_injection(self):
        saved = F.NEIGHBOURS, F.inject, F.KINDS
        A.apply("left")
        try:
            self.assertEqual(F.NEIGHBOURS["ㄗ"], ["ㄔ"])
            # p = 1: every key slips; with ㄗ alone the right-hand slip (ㄧ) must still be drawn by the injection
            seen = {F.inject(["ㄗ"], "adjacent", 1, random.Random(i), set())[0][0] for i in range(50)}
            self.assertEqual(seen, {"ㄔ", "ㄧ"})
        finally:
            F.NEIGHBOURS, F.inject, F.KINDS = saved


if __name__ == "__main__":
    unittest.main()
