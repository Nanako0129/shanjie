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

    def test_rows_file_keeps_context_and_is_seeded(self):
        import os, tempfile
        src = "".join(f"前文{i}|{g}|{' '.join(y)}\n" for i, (g, y) in enumerate(F.load_set("dev299")[:20]))
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "rows.txt")
            open(f, "w", encoding="utf-8").write(src)
            ctxs, rows = X.read_rows3(f)
            valid = F.Ctx().tol("fuzzy").valid
            a, flags = X.export(rows, "fuzzy", 0.1, 1, valid, ctxs)
            self.assertEqual(X.export(rows, "fuzzy", 0.1, 1, valid, ctxs), (a, flags))
        self.assertEqual([l.split("|")[0] for l in a.splitlines()], [f"前文{i}" for i in range(20)])
        self.assertEqual(len(flags.splitlines()), len(src.splitlines()))
        # the reading is injected exactly as with the contextless export at the same seed
        plain = X.export(rows, "fuzzy", 0.1, 1, valid)[0]
        self.assertEqual([l.split("|", 1)[1] for l in a.splitlines()], [l.split("|", 1)[1] for l in plain.splitlines()])

    def test_score_counts_like_rescue(self):
        flags = [(1, 0), (1, 0), (1, 0), (1, 1), (0, 0)]
        ok = lambda *b: [(x, 0, 1) for x in b]
        # row 1: clean right, exact wrong, tolerance right -> rescued; row 2: clean wrong -> excluded; row 3: exact right;
        # row 4 (invalid): EXACT says right (the CLI decoded it through adjacent readings) but the exact decoder has no path -> wrong
        got = X.score(flags, ok(1, 0, 1, 1, 1), ok(0, 0, 1, 1, 1), ok(1, 1, 0, 1, 1))
        self.assertEqual(got, {"valid": (3, 1, 1, 1), "invalid": (1, 0, 1, 1)})


if __name__ == "__main__":
    unittest.main()
