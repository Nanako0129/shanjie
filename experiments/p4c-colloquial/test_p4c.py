import importlib.util, io, json, os, sys, tempfile, unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import p4c as P  # noqa: E402
import evalstats  # noqa: E402


def row(i, ok_pick, ok0, ll=None):
    """Margin 0 so the fusion always looks: candidate 0 has ng 0, candidate 1 ng -1; ll decides. ok = [ok0, ok_pick]."""
    r = {"i": i, "n": 2, "ng": [0.0, -1.0], "ok": [ok0, ok_pick], "ed": [0 if ok0 else 1, 0 if ok_pick else 1], "glen": 5, "c": 0}
    if ll is not None:
        r["ll"] = ll
    return r


def write(path, rows):
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def write_rs(path, oks):
    with open(path, "w") as f:
        for i, o in enumerate(oks, 1):
            f.write(f"{i}\t{o}\t{1 - o}\t5\n")


def scores(pairs, switch):
    """pairs: (ok0, ok1) per row. ll [0, 10] switches to candidate 1 at every alpha in the grid (0.3*10 = 3 > 1); [0, 0] never."""
    return [row(i, ok1, ok0, [0.0, 10.0 if sw else 0.0]) for i, ((ok0, ok1), sw) in enumerate(zip(pairs, switch), 1)]


class Fix(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = lambda n: os.path.join(self.d, n)

    def make(self, rows_by_mu, base_ok, rs_override=None):
        write_rs(self.p("base"), base_ok)
        argv = ["--base", self.p("base")]
        for mu, rows in rows_by_mu.items():
            write(self.p(f"s{mu}"), rows)
            ok = [r["ok"][P.final_pick(r, *P.ANCHOR)] for r in rows]
            if rs_override and mu in rs_override:
                ok = rs_override[mu]
            write_rs(self.p(f"r{mu}"), ok)
            argv += ["--mu-scores", f"{mu}={self.p(f's{mu}')}", "--mu-rowstats", f"{mu}={self.p(f'r{mu}')}"]
        return argv

    def run_main(self, argv):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = P.main(argv)
        return rc, out.getvalue()


class Select(Fix):
    def four(self, rows):
        return {mu: rows for mu in P.MUS}

    def test_chosen_and_json(self):
        # base wrong everywhere, candidate 1 right: switching rows are fixes
        rows = scores([(0, 1)] * 4, [1, 1, 0, 0])
        argv = self.make(self.four(rows), [0] * 4) + ["--select", "--out", self.p("o.json")]
        rc, out = self.run_main(argv)
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip().splitlines()[-1], "0.1,0.3,0.25")   # all cells tie: smaller mu, alpha 0.3, delta 0.25
        self.assertEqual(len(json.load(open(self.p("o.json")))["cells"]), 24)
        self.assertEqual(len([l for l in out.splitlines() if l.startswith("mu ")]), 24)

    def test_tie_break_mu(self):
        good = scores([(0, 1)] * 4, [1, 1, 1, 0])
        weak = scores([(0, 1)] * 4, [1, 0, 0, 0])
        rows = {0.1: weak, 0.2: weak, 0.3: good, 0.5: good}
        _, out = self.run_main(self.make(rows, [0] * 4) + ["--select"])
        self.assertEqual(out.strip().splitlines()[-1], "0.3,0.3,0.25")

    def test_anchor_stop(self):
        rows = scores([(0, 1)] * 3, [1, 0, 0])
        argv = self.make(self.four(rows), [0] * 3, rs_override={0.2: [0, 0, 1]}) + ["--select"]
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit) as c:
            P.main(argv)
        self.assertEqual(c.exception.code, 1)
        self.assertIn("STOP:", out.getvalue())
        self.assertIn("[1, 3]", out.getvalue())

    def test_no_cell_qualifies(self):
        # 40 rows ok at base, all broken: breaks 40 > 30, fixes 0, p tiny, net < 0
        rows = scores([(1, 0)] * 40, [1] * 40)
        _, out = self.run_main(self.make(self.four(rows), [1] * 40) + ["--select", "--out", self.p("o.json")])
        self.assertIn("NO CELL QUALIFIES", out)
        j = json.load(open(self.p("o.json")))
        self.assertTrue(j["no_cell_qualifies"])
        self.assertIsNone(j["chosen"])

    def test_cap_30_and_ratio(self):
        def q(f, b):
            return P.qualifies(P.stats([1] * b + [0] * f, [0] * b + [1] * f))
        self.assertTrue(q(25, 30))     # breaks exactly at the cap (p 0.5)
        self.assertFalse(q(25, 31))    # one over the cap, ratio 1.24
        self.assertTrue(q(80, 40))     # over the cap, ratio exactly 0.5
        self.assertFalse(q(79, 40))    # ratio 0.506
        self.assertFalse(q(0, 40))     # fixes 0: infinite ratio and p significant


class Clauses(unittest.TestCase):
    def S(self, f, b):
        return {"fixes": f, "breaks": b, "net": f - b, "p": 0.001 if f - b else 1.0}

    def test_clause1(self):
        def v(f, b, p):
            return P.clause_verdict("1", {"fixes": f, "breaks": b, "net": f - b, "p": p})
        self.assertTrue(v(20, 26, 0.5))       # breaks exactly 26
        self.assertFalse(v(20, 27, 0.5))      # 27, ratio 1.35
        self.assertTrue(v(60, 30, 0.001))     # ratio exactly 0.5, net > 0
        self.assertFalse(v(59, 30, 0.001))    # ratio just above 0.5
        self.assertFalse(v(0, 20, 0.001))     # significantly worse: first part fails even under 26 breaks

    def test_clause2a(self):
        self.assertTrue(P.clause_verdict("2a", self.S(0, 0)))
        self.assertTrue(P.clause_verdict("2a", self.S(2, 2)))
        self.assertFalse(P.clause_verdict("2a", self.S(1, 3)))
        self.assertFalse(P.clause_verdict("2a", self.S(5, 3)))

    def test_clause3(self):
        self.assertTrue(P.clause_verdict("3", self.S(4, 2)))      # ratio exactly 0.5
        self.assertFalse(P.clause_verdict("3", self.S(4, 3)))
        self.assertTrue(P.clause_verdict("3", self.S(2, 0)))      # breaks 0
        self.assertFalse(P.clause_verdict("3", self.S(0, 0)))     # net 0
        self.assertIsNone(P.clause_verdict("report", self.S(1, 1)))


class CellMode(Fix):
    def test_cell_and_out_rowstats(self):
        rows = scores([(0, 1), (1, 0), (1, 1), (0, 0)], [1, 1, 0, 0])
        argv = self.make({0.3: rows}, [0, 1, 1, 0]) + ["--cell", "0.3,0.3,0.25", "--clause", "1", "--out-rowstats", self.p("c.rs")]
        rc, out = self.run_main(argv)
        self.assertIn("fixes 1 breaks 1 net 0", out)
        self.assertIn("clause 1 PASS", out)
        self.assertIn("old_rule", out)
        got = evalstats.read_rowstats(self.p("c.rs"))
        self.assertEqual([g[0] for g in got], [1, 0, 1, 0])
        self.assertEqual(got[0], (1, 0, 5))
        self.assertEqual(got[1], (0, 1, 5))
        self.assertIn("p4c", evalstats.compare(self.p("base"), self.p("c.rs"), "p4c", level=0.90))

    def test_off_grid_refused(self):
        rows = scores([(0, 1)], [1])
        argv = self.make({0.3: rows}, [0]) + ["--cell", "0.4,0.3,0.25"]
        with self.assertRaises(SystemExit) as c:
            self.run_main(argv)
        self.assertIn("off-grid", str(c.exception))

    def test_cell_from_json(self):
        rows = scores([(0, 1)] * 2, [1, 0])
        argv = self.make({mu: rows for mu in P.MUS}, [0, 0])
        self.run_main(argv + ["--select", "--out", self.p("o.json")])
        _, out = self.run_main(argv + ["--cell-from", self.p("o.json")])
        self.assertIn("fixes 1 breaks 0", out)


@unittest.skipUnless(os.path.exists(os.path.join(P.ROOT, "experiments/s7a-tinylm/fusegrid.py")) and importlib.util.find_spec("numpy"),
                     "needs numpy")
class CopyMatches(unittest.TestCase):
    def test_pick_equals_fusegrid(self):
        sys.path.insert(0, os.path.join(P.ROOT, "experiments/s7a-tinylm"))
        import fusegrid
        recs = [{"n": 3, "ng": [0.0, -0.5, -3.0], "ll": [0.0, 2.0, 9.0]}, {"n": 2, "ng": [0.0, -2.5], "ll": [0.0, 9.0]},
                {"n": 1, "ng": [0.0], "ll": [0.0]}, {"n": 3, "ng": [0.0, -0.1, -0.2], "ll": [0.0, 0.5, 0.4]}]
        for r in recs:
            for a in P.ALPHAS:
                for d in P.DELTAS:
                    self.assertEqual(P.pick(r, a, 2.0, "ll", d), fusegrid.pick(r, a, 2.0, "ll", d))


if __name__ == "__main__":
    unittest.main()
