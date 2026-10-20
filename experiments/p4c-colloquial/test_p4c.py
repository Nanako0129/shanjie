import importlib.util, io, json, os, sys, tempfile, unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import p4c as P  # noqa: E402
import evalstats  # noqa: E402


def row(i, ok0, ok1, switch):
    """Two candidates with an n-gram margin of 1 (< tau 2), so the fusion always looks: ll [0, 10] picks candidate 1 at every grid alpha and
    delta (0.3 * 10 = 3 > 1 + 0.25), ll [0, 0] never. ok = [ok0, ok1]; c is the index the CLI would have chosen at the anchor."""
    return {"i": i, "n": 2, "ng": [0.0, -1.0], "ok": [ok0, ok1], "ed": [1 - ok0, 1 - ok1], "glen": 5, "c": int(switch),
            "ll": [0.0, 10.0 if switch else 0.0]}


def scores(pairs, switch):
    return [row(i, ok0, ok1, sw) for i, ((ok0, ok1), sw) in enumerate(zip(pairs, switch), 1)]


def write(path, rows):
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def write_rs(path, oks):
    with open(path, "w") as f:
        for i, o in enumerate(oks, 1):
            f.write(f"{i}\t{o}\t{1 - o}\t5\n")


class Fix(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.d = t.name
        for name in ("N_SELECT", "N_DISCORD"):   # the fixtures are tiny: the real guards are tested explicitly
            self.addCleanup(setattr, P, name, getattr(P, name))

    def p(self, n):
        return os.path.join(self.d, n)

    def make(self, rows_by_mu, base_ok, rs_override=None):
        write_rs(self.p("base"), base_ok)
        argv = ["--base", self.p("base")]
        for mu, rows in rows_by_mu.items():
            write(self.p(f"s{mu}"), rows)
            ok = [r["ok"][r["c"]] for r in rows]
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

    def stop(self, argv):
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit) as c:
            P.main(argv)
        return c.exception.code, out.getvalue()


class Select(Fix):
    def four(self, rows):
        return {mu: rows for mu in P.MUS}

    def test_chosen_json_and_decomposition(self):
        rows = scores([(0, 1)] * 4, [1, 1, 0, 0])
        P.N_SELECT = 4
        rc, out = self.run_main(self.make(self.four(rows), [0] * 4) + ["--select", "--out", self.p("o.json")])
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip().splitlines()[-1], "0.1,0.3,0.25")   # all cells tie: smaller mu, alpha 0.3, delta 0.25
        j = json.load(open(self.p("o.json")))
        self.assertEqual(len(j["cells"]), 24)
        self.assertEqual(len([l for l in out.splitlines() if l.startswith("mu ")]), 24)
        self.assertEqual((j["cells"][0]["fixes_sfirst_wrong"], j["cells"][0]["fixes_sfirst_right"]), (2, 0))

    def test_tie_break_mu(self):
        good = scores([(0, 1)] * 4, [1, 1, 1, 0])
        weak = scores([(0, 1)] * 4, [1, 0, 0, 0])
        P.N_SELECT = 4
        _, out = self.run_main(self.make({0.1: weak, 0.2: weak, 0.3: good, 0.5: good}, [0] * 4) + ["--select"])
        self.assertEqual(out.strip().splitlines()[-1], "0.3,0.3,0.25")

    def test_higher_net_nonqualifying_loses(self):
        # net +50 but 61 breaks (> 30) at ratio 0.55 (> 0.5): not qualifying; the lower-net qualifying cell wins despite the larger mu.
        huge = {"fixes": 111, "breaks": 61, "net": 50, "p": 0.001}
        small = P.stats([0], [1])
        self.assertFalse(P.qualifies(huge))
        self.assertTrue(P.qualifies(small))
        self.assertEqual(P.choose([(0.1, 0.3, 0.25, huge), (0.5, 0.3, 0.25, small)])[0], 0.5)

    def test_anchor_stop_rowstats(self):
        rows = scores([(0, 1)] * 3, [1, 0, 0])
        P.N_SELECT = 3
        code, out = self.stop(self.make(self.four(rows), [0] * 3, rs_override={0.2: [0, 0, 1]}) + ["--select"])
        self.assertEqual(code, 1)
        self.assertIn("STOP:", out)
        self.assertIn("[1, 3]", out)

    def test_anchor_stop_chosen_index(self):
        rows = scores([(0, 1)] * 3, [1, 0, 0])
        rows[1]["c"] = 1   # the CLI chose candidate 1 where the offline pick is 0
        P.N_SELECT = 3
        code, out = self.stop(self.make(self.four(rows), [0] * 3) + ["--select"])
        self.assertEqual(code, 1)
        self.assertIn("(pick)", out)
        self.assertIn("[2]", out)

    def test_glen_mismatch_stop(self):
        rows = scores([(0, 1)] * 2, [1, 0])
        rows[0]["glen"] = 6
        P.N_SELECT = 2
        code, out = self.stop(self.make(self.four(rows), [0] * 2) + ["--select"])
        self.assertEqual(code, 1)
        self.assertIn("(glen)", out)

    def test_no_cell_qualifies_exit_3(self):
        rows = scores([(1, 0)] * 40, [1] * 40)
        P.N_SELECT = 40
        rc, out = self.run_main(self.make(self.four(rows), [1] * 40) + ["--select", "--out", self.p("o.json")])
        self.assertEqual(rc, 3)
        self.assertIn("NO CELL QUALIFIES", out)
        j = json.load(open(self.p("o.json")))
        self.assertTrue(j["no_cell_qualifies"])
        self.assertIsNone(j["chosen"])

    def test_n_guards(self):
        argv = self.make({mu: scores([(0, 1)], [1]) for mu in P.MUS}, [0])
        with self.assertRaises(SystemExit) as c:
            self.run_main(argv + ["--select"])
        self.assertIn("5761", str(c.exception))
        with self.assertRaises(SystemExit) as c:
            self.run_main(argv + ["--cell", "0.1,0.3,0.25", "--clause", "1"])
        self.assertIn("4958", str(c.exception))

    def test_argument_errors(self):
        argv = self.make({mu: scores([(0, 1)], [1]) for mu in P.MUS}, [0])
        with self.assertRaises(SystemExit) as c:
            self.run_main(argv + ["--select", "--cell", "0.1,0.3,0.25"])
        self.assertIn("cannot be combined", str(c.exception))
        with self.assertRaises(SystemExit) as c:
            self.run_main(argv + ["--cell", "0.1,0.3,0.25", "--mu-scores", f"0.1={self.p('s0.1')}"])
        self.assertIn("twice", str(c.exception))

    def test_cap_30_and_ratio(self):
        def q(f, b):
            return P.qualifies(P.stats([1] * b + [0] * f, [0] * b + [1] * f))
        self.assertTrue(q(25, 30))     # breaks exactly at the cap (p 0.5)
        self.assertFalse(q(25, 31))    # one over the cap, ratio 1.24
        self.assertTrue(q(80, 40))     # over the cap, ratio exactly 0.5
        self.assertFalse(q(79, 40))    # ratio 0.506
        self.assertFalse(q(0, 40))     # fixes 0: infinite ratio and p significant


class Clauses(unittest.TestCase):
    def S(self, f, b, p=0.001):
        return {"fixes": f, "breaks": b, "net": f - b, "p": p if f != b else 1.0}

    def test_clause1(self):
        v = lambda f, b, p: P.clause_verdict("1", self.S(f, b, p))
        self.assertTrue(v(20, 26, 0.5))       # breaks exactly 26
        self.assertFalse(v(20, 27, 0.5))      # 27, ratio 1.35
        self.assertTrue(v(60, 30, 0.001))     # ratio exactly 0.5, net > 0
        self.assertFalse(v(59, 30, 0.001))    # ratio just above 0.5
        self.assertFalse(v(0, 20, 0.001))     # significantly worse: first part fails even under 26 breaks

    def test_clause2a(self):
        self.assertTrue(P.clause_verdict("2a", self.S(0, 0)))
        self.assertTrue(P.clause_verdict("2a", self.S(2, 2)))
        self.assertFalse(P.clause_verdict("2a", self.S(3, 3)))
        self.assertFalse(P.clause_verdict("2a", self.S(1, 3)))

    def test_clause3(self):
        self.assertTrue(P.clause_verdict("3", self.S(4, 2)))      # ratio exactly 0.5
        self.assertFalse(P.clause_verdict("3", self.S(4, 3)))
        self.assertTrue(P.clause_verdict("3", self.S(2, 0)))
        self.assertFalse(P.clause_verdict("3", self.S(0, 0)))     # net 0
        self.assertIsNone(P.clause_verdict("report", self.S(1, 1)))

    def test_old_rules(self):
        o = P.old_rules(self.S(30, 26))
        self.assertTrue(o["old_clause1"])
        self.assertFalse(o["old_clause3"])
        self.assertTrue(P.old_rules(self.S(5, 2))["old_clause3"])
        self.assertFalse(P.old_rules(self.S(5, 3))["old_clause3"])
        self.assertFalse(P.old_rules(self.S(0, 20))["old_clause1"])   # significant and net < 0

    def test_error_ratio(self):
        self.assertEqual(P.stats([1, 1, 0, 0, 0], [0, 1, 1, 1, 0])["error_ratio"], 1 / 2)
        self.assertEqual(P.stats([1], [0])["error_ratio"], float("inf"))
        self.assertEqual(P.stats([1], [1])["error_ratio"], 0.0)


class CellMode(Fix):
    def test_cell_out_rowstats_and_2b(self):
        rows = scores([(0, 1), (1, 0), (1, 1), (0, 0)], [1, 1, 0, 0])
        argv = self.make({0.3: rows}, [0, 1, 1, 0]) + ["--cell", "0.3,0.3,0.25", "--clause", "2b", "--out-rowstats", self.p("c.rs")]
        rc, out = self.run_main(argv)
        self.assertIn("fixes 1 breaks 1 net 0", out)
        self.assertIn("breaks_sfirst_right 1", out)
        self.assertIn("clause 2b FAIL lower90_rows", out)   # n 4: any bootstrap lower bound is far below -1 pp
        self.assertIn("old_clause1 1", out)
        got = evalstats.read_rowstats(self.p("c.rs"))
        self.assertEqual([g[0] for g in got], [1, 0, 1, 0])
        self.assertEqual(got[0], (1, 0, 5))
        self.assertEqual(got[1], (0, 1, 5))
        self.assertIn("p4c", evalstats.compare(self.p("base"), self.p("c.rs"), "p4c", level=0.90))

    def test_2b_pass(self):
        n = 200
        rows = scores([(1, 1)] * n, [0] * n)
        _, out = self.run_main(self.make({0.3: rows}, [1] * n) + ["--cell", "0.3,0.3,0.25", "--clause", "2b"])
        self.assertIn("clause 2b PASS lower90_rows 0 lower90_pp 0.000", out)

    def test_cell_anchor_stop(self):
        rows = scores([(0, 1)] * 2, [1, 0])
        rows[0]["c"] = 0
        code, out = self.stop(self.make({0.3: rows}, [0, 0]) + ["--cell", "0.3,0.7,0"])
        self.assertEqual(code, 1)
        self.assertIn("(pick)", out)

    def test_off_grid_refused(self):
        argv = self.make({0.3: scores([(0, 1)], [1])}, [0]) + ["--cell", "0.4,0.3,0.25"]
        with self.assertRaises(SystemExit) as c:
            self.run_main(argv)
        self.assertIn("off-grid", str(c.exception))

    def test_cell_from_json(self):
        rows = scores([(0, 1)] * 2, [1, 0])
        argv = self.make({mu: rows for mu in P.MUS}, [0, 0])
        P.N_SELECT = 2
        self.run_main(argv + ["--select", "--out", self.p("o.json")])
        _, out = self.run_main(argv + ["--cell-from", self.p("o.json")])
        self.assertIn("fixes 1 breaks 0", out)


@unittest.skipUnless(importlib.util.find_spec("numpy"), "needs numpy (fusegrid imports it)")
class MatchesFusegrid(unittest.TestCase):
    def test_final_pick_equals_fusegrid(self):
        sys.path.insert(0, os.path.join(P.ROOT, "experiments/s7a-tinylm"))
        import fusegrid
        recs = [{"n": 3, "ng": [0.0, -0.5, -3.0], "ll": [0.0, 2.0, 9.0]}, {"n": 2, "ng": [0.0, -2.5], "ll": [0.0, 9.0]},
                {"n": 1, "ng": [0.0], "ll": [0.0]}, {"n": 3, "ng": [0.0, -0.1, -0.2], "ll": [0.0, 0.5, 0.4]}]
        for r in recs:
            for a in P.ALPHAS:
                for d in P.DELTAS:
                    self.assertEqual(P.final_pick(r, a, d), fusegrid.pick(r, a, 2.0, "ll", d))


if __name__ == "__main__":
    unittest.main()
