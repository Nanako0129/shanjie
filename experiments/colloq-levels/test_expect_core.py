"""expect_core.py: the offline arithmetic gives the same three picks as the core's unit test `each_level_picks_with_its_own_cell`
(standard 0, conservative 2, aggressive 1 on one list); the real header triples (0.3/2/0.25, 1/2/3, 0.3/2/0.25) are the fallback, and `compare` flags a level run whose pick is not the level's cell."""
import math, os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import expect_core as E  # noqa: E402

ROW = {"sc": {"ng": [0.0, -0.4, -4.0], "ll": [0.0, 1.2, 8.0], "ok": [0, 0, 1], "ed": [1, 1, 0], "glen": 3, "n": 3, "c": 0}, "col": [0.0] * 3, "ch": [0.0] * 3}
HDR = {lv: E.HEADER[lv] for lv in E.HEADER}


class T(unittest.TestCase):
    def test_each_level_picks_its_own_cell(self):
        self.assertEqual([E.cell_pick(ROW, E.CELL[lv], HDR[lv]) for lv in ("standard", "conservative", "aggressive")], [0, 2, 1])

    def test_terms_weigh_the_same_at_every_alpha(self):
        r = {"sc": {"ng": [0.0, -0.5], "ll": [-5.0, -5.0]}, "col": [0.0, 10.0], "ch": [0.0, 0.0]}
        self.assertEqual(E.cell_pick(r, E.CELL["conservative"], HDR["conservative"]), 0)       # 2 < delta 3 (a doubled term: 4.5)
        r["col"] = [0.0, 16.0]
        self.assertEqual(E.cell_pick(r, E.CELL["conservative"], HDR["conservative"]), 1)       # 3.5

    def test_non_finite_col_takes_the_levels_header_triple(self):
        r = {"sc": {"ng": [-10.0, -11.0], "ll": [-5.0, -1.0]}, "col": [0.0, -math.inf], "ch": [0.0, 0.0]}
        self.assertEqual([E.cell_pick(r, E.CELL[lv], E.HEADER[lv]) for lv in ("standard", "conservative", "aggressive")], [0, 1, 0])

    def test_compare_flags_a_wrong_pick(self):
        rec = {"i": 1, "n": 3, "ng": ROW["sc"]["ng"], "ok": ROW["sc"]["ok"], "ed": ROW["sc"]["ed"], "glen": 3, "ll": ROW["sc"]["ll"], "c": 2}
        self.assertEqual(E.compare("conservative", [ROW], [rec], [(1,)], HDR), [])
        self.assertEqual(E.compare("conservative", [ROW], [dict(rec, c=0)], [(0,)], HDR), [1])

    def test_row_check_stops_on_a_gold_length_mismatch(self):
        cs = [("a", -1.0, ["a"]), ("b", -2.0, ["b"])]
        sc = {"ng": [-1.0, -2.0], "glen": 3}
        E.check_row(1, cs, sc, (1, 0, 3))                                   # consistent: no exit
        with self.assertRaises(SystemExit):
            E.check_row(1, cs, sc, (1, 0, 4))                               # gold length differs between scores and rowstats
        with self.assertRaises(SystemExit):
            E.check_row(1, cs, dict(sc, ng=[-1.0, -2.5]), (1, 0, 3))        # ng is not the dump's
        with self.assertRaises(SystemExit):
            E.check_row(1, [("a", -1.0, None), cs[1]], sc, (1, 0, 3))       # no word split

    @unittest.skipUnless(os.environ.get("SC_PY"), "set SC_PY to experiments/strength-colloq/sc.py (exp/strength-colloq, not on main)")
    def test_cell_pick_equals_sc_py_on_synthetic_rows(self):
        import importlib.util, random
        sys.dont_write_bytecode = True
        spec = importlib.util.spec_from_file_location("sc_ref", os.environ["SC_PY"])
        ref = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ref)
        rnd = random.Random(7)
        n = 0
        for _ in range(300):
            k = rnd.randint(2, 8)
            ng = sorted((-rnd.uniform(0, 30) for _ in range(k)), reverse=True)
            r = {"sc": {"ng": ng, "ll": [-rnd.uniform(0, 40) for _ in range(k)]}, "col": [-rnd.uniform(0, 30) for _ in range(k)], "ch": [-rnd.choice([0, 0, 1, 2.5, 4, 8]) for _ in range(k)]}
            if rnd.random() < 0.15:
                r["col"][rnd.randrange(k)] = rnd.choice([math.inf, -math.inf, math.nan])
            for lv, cell in E.CELL.items():
                self.assertEqual(E.cell_pick(r, cell, E.HEADER[lv]), ref.cell_pick(r, cell, E.HEADER[lv]))
                n += 1
        self.assertEqual(n, 900)


if __name__ == "__main__":
    unittest.main()
