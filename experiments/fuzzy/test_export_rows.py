import unittest

import export_rows as X
import fuzzy as F


class ExportRows(unittest.TestCase):
    def test_rows_are_the_grid_inputs(self):
        # The exported input of row i must be what grid.py decoded for row i (same seed string as fuzzy.exact_of).
        ctx = F.Ctx("chat")
        rows = F.load_set("dev299")[:20]
        text, flags = X.export(rows, "fuzzy", 0.1, 1, ctx.tol("fuzzy").valid)
        got = [l.split("|")[2].split() for l in text.splitlines()]
        want = [e["inp"] for e in F.exact_of(ctx, rows, "fuzzy", 0.1, 1)]
        self.assertEqual(got, want)
        self.assertEqual([l.split("|")[1] for l in text.splitlines()], [g for g, _ in rows])
        self.assertTrue(any(l.split("\t")[1] == "1" for l in flags.splitlines()))  # p = 0.1 over 20 rows changes some

    def test_score_counts_like_rescue(self):
        flags = [(1, 0), (1, 0), (1, 0), (1, 1), (0, 0)]
        ok = lambda *b: [(x, 0, 1) for x in b]
        # row 1: clean right, exact wrong, tolerance right -> rescued; row 2: clean wrong -> excluded; row 3: exact right
        got = X.score(flags, ok(1, 0, 1, 1, 1), ok(0, 0, 1, 0, 1), ok(1, 1, 0, 1, 1))
        self.assertEqual(got, {"valid": (3, 1, 1, 1), "invalid": (1, 0, 1, 1)})


if __name__ == "__main__":
    unittest.main()
