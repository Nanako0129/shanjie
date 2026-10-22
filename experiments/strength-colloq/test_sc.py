"""Tests of sc.py on synthetic rows only (no private data, no CLI output). Each registered rule has a case that fails when the rule is broken.

  python3 experiments/strength-colloq/test_sc.py        python3 -m unittest experiments/strength-colloq/test_sc.py
"""
import contextlib, io, json, math, os, random, sys, tempfile, unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sc as S  # noqa: E402
import p4c  # noqa: E402

INF = math.inf
ALL_CELLS = [S.STD] + S.CONS + S.AGGR


def scores(ng, ll, ok, c=0):
    d = {"i": 1, "n": len(ng), "ng": ng, "ok": ok, "ed": [0 if o else 1 for o in ok], "glen": 4, "c": c}
    if ll is not None:
        d["ll"] = ll
    return d


def row(ng, ll, ok, col, ch, c=0):
    return {"sc": scores(ng, ll, ok, c), "col": col, "ch": ch}


def jdump(obj, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f)


def jload(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def exits(test, fn, *args, **kw):
    with test.assertRaises(SystemExit) as cm, contextlib.redirect_stdout(io.StringIO()):
        fn(*args, **kw)
    test.assertEqual(cm.exception.code, 1)


def reference_pick(ng, ll, col, ch, cell):
    """The README formula written out, nothing shared with sc.py: s_i = ng_i + a ll_i + g_i mu (col_i - col_0) + nu (ch_i - ch_0), mu 0.5, T 1.0, nu 1.0."""
    a, tau, d = cell
    if ng[0] - ng[1] >= tau:
        return 0
    s = [ng[i] + a * ll[i] + 1 / (1 + math.exp(-(ll[i] - ll[0]) / 1.0)) * 0.5 * (col[i] - col[0]) + 1.0 * (ch[i] - ch[0]) for i in range(len(ng))]
    best = max(range(len(ng)), key=lambda i: (s[i], -i))
    return best if s[best] - s[0] >= d else 0


class Fusion(unittest.TestCase):
    def test_every_cell_is_the_readme_formula(self):
        rng = random.Random(7)
        picked = set()
        for cell in ALL_CELLS:
            for _ in range(400):
                k = rng.randint(2, 8)
                ng = sorted((-rng.uniform(5, 30) for _ in range(k)), reverse=True)
                ng = [x + rng.choice((0, 0, 1.5)) * 0 for x in ng]
                ll = [-rng.uniform(5, 30) for _ in range(k)]
                col = [rng.uniform(-8, 0) for _ in range(k)]
                ch = [rng.choice((0.0, -1.0, -2.5, -4.0, -6.0)) for _ in range(k)]
                r = row(ng, ll, [1] + [0] * (k - 1), col, ch)
                j = S.cell_pick(r, cell, S.STD)
                self.assertEqual(j, reference_pick(ng, ll, col, ch, cell), cell)
                picked.add((cell, j > 0))
        self.assertTrue(all((cell, True) in picked and (cell, False) in picked for cell in ALL_CELLS), "every cell both moves and keeps the first")

    # two candidates, equal ll (so the gate is exactly 0.5): the gain of candidate 1 over the first is -0.3 + 0.5 * mu * dcol + nu * dch
    def pair(self, dcol, dch, cell):
        return S.cell_pick(row([-10.0, -10.3], [-5.0, -5.0], [1, 0], [0.0, dcol], [0.0, dch]), cell, S.STD)

    def test_mu_over_alpha_uses_the_cells_alpha(self):
        for alpha in (0.3, 0.5, 1.0):
            cell = (alpha, 2.0, 0.25)
            self.assertEqual(self.pair(2.4, 0.0, cell), 1, f"alpha {alpha}: gain 0.3 >= 0.25 whatever alpha")
            self.assertEqual(self.pair(2.0, 0.0, cell), 0, f"alpha {alpha}: gain 0.2 < 0.25 whatever alpha")

    def test_nu_over_alpha_uses_the_cells_alpha(self):
        for alpha in (0.3, 0.5, 0.7, 1.0):
            cell = (alpha, 2.0, 0.25)
            self.assertEqual(self.pair(0.0, 0.7, cell), 1, f"alpha {alpha}: gain 0.4")
            self.assertEqual(self.pair(0.0, 0.5, cell), 0, f"alpha {alpha}: gain 0.2")

    def test_the_terms_are_relative_to_the_first_candidate(self):
        r = row([-10.0, -10.3], [-5.0, -5.0], [1, 0], [-100.0, -97.6], [-50.0, -49.3])      # same gaps as dcol 2.4, dch 0.7
        self.assertEqual(S.cell_pick(r, (0.5, 2.0, 0.25), S.STD), 1)

    def test_tau_inf_always_reranks_and_tau_2_stops_at_a_margin_of_2(self):
        r = row([-10.0, -13.0], [-20.0, -1.0], [1, 0], [0.0, 0.0], [0.0, 0.0])             # n-gram margin 3, the small model says candidate 1
        self.assertEqual(S.cell_pick(r, (0.7, 2.0, 0.0), S.STD), 0)
        self.assertEqual(S.cell_pick(r, (0.7, INF, 0.0), S.STD), 1)
        edge = row([-10.0, -12.0], [-20.0, -1.0], [1, 0], [0.0, 0.0], [0.0, 0.0])           # margin exactly 2: stops (>= tau)
        self.assertEqual(S.cell_pick(edge, (0.7, 2.0, 0.0), S.STD), 0)

    def test_delta_is_the_cells_own(self):
        r = row([-10.0, -10.3], [-5.0, -3.0], [1, 0], [0.0, 0.0], [0.0, 0.0])                # gain 0.5 * 2 - 0.3 = 0.7 at alpha 0.5
        self.assertEqual(S.cell_pick(r, (0.5, 2.0, 0.5), S.STD), 1)
        self.assertEqual(S.cell_pick(r, (0.5, 2.0, 1.0), S.STD), 0)

    def test_non_finite_col_takes_the_plain_pick_with_the_given_triple(self):
        r = row([-10.0, -10.3], [-5.0, -3.8], [1, 0], [0.0, INF], [0.0, 0.0])
        self.assertEqual(S.cell_pick(r, (0.5, 2.0, 0.25), (0.3, 2.0, 0.25)), 0)             # plain: 0.3 * 1.2 - 0.3 = 0.06 < 0.25
        self.assertEqual(S.cell_pick(r, (0.5, 2.0, 0.25), (1.0, 2.0, 0.25)), 1)             # plain at alpha 1.0: 1.2 - 0.3 = 0.9
        r["col"] = [float("nan"), 0.0]
        self.assertEqual(S.cell_pick(r, (0.5, 2.0, 0.25), (0.3, 2.0, 0.25)), 0)

    def test_no_ll_or_one_candidate_keeps_the_first(self):
        self.assertEqual(S.cell_pick(row([-10.0, -10.3], None, [1, 0], [0.0, 9.0], [0.0, 9.0]), S.STD, S.STD), 0)
        self.assertEqual(S.cell_pick(row([-10.0], [-5.0], [1], [0.0], [0.0]), S.STD, S.STD), 0)

    def test_longer_ll_is_cut_to_the_candidates(self):                                      # a scores file with more than 8 ll
        r = row([-10.0, -10.3], [-5.0, -3.0, -1.0, None], [1, 0], [0.0, 0.0], [0.0, 0.0])
        self.assertEqual(S.cell_pick(r, (0.5, 2.0, 0.5), S.STD), 1)
        self.assertEqual(S.plain_pick(r["sc"], (0.5, 2.0, 0.5)), 1)

    def test_the_grid_is_the_registered_one(self):
        self.assertEqual(S.STD, (0.5, 2.0, 0.25))
        self.assertEqual(S.CONS, [(0.3, 2.0, 1.0), (0.3, 2.0, 1.5), (0.5, 2.0, 1.0), (0.5, 2.0, 1.5), (0.5, 2.0, 2.0), (1.0, 2.0, 3.0)])
        self.assertEqual(sorted(S.AGGR), sorted((a, t, d) for a in (0.5, 0.7) for t in (2.0, INF) for d in (0.0, 0.1)))
        self.assertEqual((len(S.CONS), len(S.AGGR)), (6, 8))
        self.assertEqual((S.MU, S.GATE, S.NU, S.CAP), (0.5, 1.0, 1.0, 4))
        self.assertEqual(S.SHIP, {"conservative": (1.0, 2.0, 3.0), "aggressive": (0.3, 2.0, 0.25)})
        self.assertEqual([S.label(c) for c in (S.STD, S.AGGR[1], S.AGGR[2])], ["0.5,2,0.25", "0.5,2,0.1", "0.5,inf,0.0"])
        self.assertEqual(S.cell_of("0.7,inf,0.1"), (0.7, INF, 0.1))
        exits(self, S.cell_of, "0.6,2,0.25")


def table(level, cells):
    """{label: stats} of the level's grid; cells not given are far from qualifying (0 fixes, 9 breaks)."""
    grid = S.CONS if level == "conservative" else S.AGGR
    return {S.label(c): {"fixes": cells.get(c, (0, 9))[0], "breaks": cells.get(c, (0, 9))[1]} for c in grid}


STD_STATS = {"fixes": 100, "breaks": 20}


class Selection(unittest.TestCase):
    def cons(self, cells, std=STD_STATS):
        return S.choose("conservative", table("conservative", cells), std)

    def aggr(self, cells, std=STD_STATS):
        return S.choose("aggressive", table("aggressive", cells), std)

    A, B, C = S.CONS[0], S.CONS[2], S.CONS[3]                    # (0.3,2,1.0) (0.5,2,1.0) (0.5,2,1.5)

    def test_conservative_breaks_at_most_half_the_standards(self):
        self.assertEqual(self.cons({self.A: (50, 10)}), self.A)
        self.assertIsNone(self.cons({self.A: (50, 11)}))
        self.assertEqual(self.cons({self.A: (50, 3)}, {"fixes": 100, "breaks": 7}), self.A)       # 2 * 3 <= 7
        self.assertIsNone(self.cons({self.A: (50, 4)}, {"fixes": 100, "breaks": 7}))              # 2 * 4 > 7: not 4 <= 3.5 rounded up

    def test_conservative_needs_more_fixes_than_breaks(self):
        self.assertIsNone(self.cons({self.A: (5, 5)}))
        self.assertEqual(self.cons({self.A: (6, 5)}), self.A)

    def test_conservative_maximises_fixes_minus_4_breaks(self):
        # A: 41 - 0 = 41; B: 60 - 20 = 40, then 62 - 20 = 42. Weights 3 and 5 would flip one of the two lines.
        self.assertEqual(self.cons({self.A: (41, 0), self.B: (60, 5)}), self.A)
        self.assertEqual(self.cons({self.A: (41, 0), self.B: (62, 5)}), self.B)

    def test_conservative_ties_prefer_larger_delta_then_smaller_alpha(self):
        self.assertEqual(self.cons({self.B: (30, 2), self.C: (30, 2)}), self.C)                   # delta 1.5 over 1.0
        self.assertEqual(self.cons({self.A: (30, 2), self.B: (30, 2)}), self.A)                   # alpha 0.3 over 0.5, same delta
        self.assertEqual(self.cons({self.A: (30, 2), self.C: (30, 2)}), self.C)                   # delta before alpha
        self.assertEqual(self.cons({S.CONS[4]: (30, 2), self.C: (30, 2)}), S.CONS[4])             # delta 2.0 over 1.5

    def test_conservative_ignores_the_standard_cell_as_a_candidate(self):
        self.assertIsNone(self.cons({}, {"fixes": 0, "breaks": 0}))

    X, Y, Z = S.AGGR[0], S.AGGR[1], S.AGGR[4]                    # (0.5,2,0.0) (0.5,2,0.1) (0.7,2,0.0)

    def test_aggressive_needs_more_fixes_and_no_less_net_than_the_standard(self):
        self.assertEqual(self.aggr({self.X: (101, 21)}), self.X)                                  # net 80 == 80
        self.assertIsNone(self.aggr({self.X: (101, 22)}))                                         # net 79
        self.assertIsNone(self.aggr({self.X: (100, 20)}))                                         # fixes equal
        self.assertIsNone(self.aggr({self.X: (100, 10)}))                                         # more net, same fixes
        self.assertEqual(self.aggr({self.X: (150, 70)}), self.X)

    def test_aggressive_maximises_fixes_minus_2_breaks(self):
        # standard 100 / 20 (net 80); all four cells below qualify. Weight 1 would pick X first, weight 3 the other cell of the second pair.
        self.assertEqual(self.aggr({self.X: (120, 30), self.Y: (108, 22)}), self.Y)               # 60 < 64
        self.assertEqual(self.aggr({self.X: (110, 22), self.Y: (130, 31)}), self.Y)               # 66 < 68
        self.assertEqual(self.aggr({self.X: (110, 22), self.Y: (130, 33)}), self.X)               # 66 > 64

    def test_aggressive_ties_prefer_smaller_alpha_then_tau_2_then_larger_delta(self):
        c = lambda a, t, d: (a, t, d)
        eq = (110, 22)
        self.assertEqual(self.aggr({c(0.5, 2.0, 0.0): eq, c(0.7, 2.0, 0.0): eq}), c(0.5, 2.0, 0.0))
        self.assertEqual(self.aggr({c(0.5, 2.0, 0.1): eq, c(0.5, INF, 0.1): eq}), c(0.5, 2.0, 0.1))
        self.assertEqual(self.aggr({c(0.5, 2.0, 0.0): eq, c(0.5, 2.0, 0.1): eq}), c(0.5, 2.0, 0.1))
        self.assertEqual(self.aggr({c(0.5, INF, 0.1): eq, c(0.7, 2.0, 0.0): eq}), c(0.5, INF, 0.1))      # alpha before tau
        self.assertEqual(self.aggr({c(0.5, INF, 0.0): eq, c(0.5, 2.0, 0.0): eq}), c(0.5, 2.0, 0.0))
        self.assertEqual(self.aggr({c(0.5, 2.0, 0.0): eq, c(0.5, INF, 0.1): eq}), c(0.5, 2.0, 0.0))      # tau before delta

    def test_select_writes_the_standard_cell_when_no_cell_qualifies(self):
        cells = {"name": "chatclause", "anchor1": "ok", "anchor2": {lv: "ok" for lv in S.LEVELS}, "std": {S.label(S.STD): STD_STATS},
                 "cells": {lv: table(lv, {}) for lv in S.LEVELS}}
        cells["cells"]["aggressive"][S.label(S.AGGR[0])] = {"fixes": 130, "breaks": 20}
        with tempfile.TemporaryDirectory() as d:
            p, out = os.path.join(d, "cells.json"), os.path.join(d, "sel.json")
            jdump(cells, p)
            self.assertEqual(S.main(["select", p, "--out", out]), 0)
            sel = jload(out)
        self.assertEqual(sel["chosen"], {"conservative": S.label(S.STD), "aggressive": S.label(S.AGGR[0])})
        self.assertEqual(sel["selection_fallback"], {"conservative": True, "aggressive": False})

    def test_select_refuses_what_is_not_the_anchored_chatclause_cells(self):
        base = {"name": "chatclause", "anchor1": "ok", "anchor2": {lv: "ok" for lv in S.LEVELS}, "std": {S.label(S.STD): STD_STATS},
                "cells": {lv: table(lv, {}) for lv in S.LEVELS}}
        for bad in ({"name": "discordtune"}, {"anchor1": "no"}, {"anchor2": {"conservative": "ok"}}, {"anchor2": {"conservative": "ok", "aggressive": "no"}}):
            with tempfile.TemporaryDirectory() as d:
                p = os.path.join(d, "cells.json")
                jdump({**base, **bad}, p)
                exits(self, S.main, ["select", p, "--out", os.path.join(d, "sel.json")])


def pair_stats(fb):
    return {"fixes": fb[0], "breaks": fb[1], "net": fb[0] - fb[1]}


def gate_sets(fb, **over):
    return {n: pair_stats(over.get(n, fb)) for n in S.GATE_SETS}


class Gates(unittest.TestCase):
    STD = gate_sets((100, 20))

    def test_conservative_no_more_breaks_than_the_standard_on_each_set(self):
        self.assertTrue(all(S.gates("conservative", gate_sets((50, 20)), self.STD).values()))
        for n in S.GATE_SETS:
            g = S.gates("conservative", gate_sets((50, 20), **{n: (50, 21)}), self.STD)
            self.assertEqual([k for k, v in g.items() if not v], [f"breaks_le_std:{n}"])

    def test_conservative_discordtune_net_must_be_positive(self):
        chosen = gate_sets((50, 20), discordtune=(20, 20))
        self.assertEqual([k for k, v in S.gates("conservative", chosen, self.STD).items() if not v], ["discordtune_net_gt_0"])
        self.assertTrue(S.gates("conservative", gate_sets((50, 20), discordtune=(21, 20)), self.STD)["discordtune_net_gt_0"])

    def test_aggressive_net_not_below_the_standards(self):
        std = gate_sets((100, 20))
        self.assertTrue(S.gates("aggressive", gate_sets((110, 30)), std)["discordtune_net_ge_std"])        # 80 == 80
        self.assertFalse(S.gates("aggressive", gate_sets((110, 31)), std)["discordtune_net_ge_std"])

    def test_aggressive_more_fixes_than_the_standard(self):
        std = gate_sets((100, 20))
        self.assertFalse(S.gates("aggressive", gate_sets((100, 10)), std)["discordtune_fixes_gt_std"])
        self.assertTrue(S.gates("aggressive", gate_sets((101, 10)), std)["discordtune_fixes_gt_std"])

    def test_aggressive_breaks_over_fixes_at_most_0_6(self):
        std = gate_sets((5, 0))
        self.assertTrue(S.gates("aggressive", gate_sets((10, 6)), std)["discordtune_ratio_le_0.6"])
        self.assertFalse(S.gates("aggressive", gate_sets((10, 7)), std)["discordtune_ratio_le_0.6"])
        self.assertFalse(S.gates("aggressive", gate_sets((50, 31)), std)["discordtune_ratio_le_0.6"])      # 0.62; the old 0.5 would fail 30 too
        self.assertTrue(S.gates("aggressive", gate_sets((50, 30)), std)["discordtune_ratio_le_0.6"])

    def test_aggressive_gates_read_discordtune_only(self):
        std = gate_sets((100, 20))
        g = S.gates("aggressive", gate_sets((110, 30), typing76=(0, 9), **{"user-reported": (0, 9)}), std)
        self.assertTrue(all(g.values()) and len(g) == 3)


class Decision(unittest.TestCase):
    OK = [True, True, True, True]

    def test_chosen_ships_when_gates_and_four_clauses_pass(self):
        self.assertEqual(S.decide("a", "std", True, self.OK, self.OK), ("chosen", "a"))

    def test_a_gate_failure_takes_the_standard_cell(self):
        self.assertEqual(S.decide("a", "std", False, self.OK, self.OK), ("standard", "std"))

    def test_each_failed_clause_of_the_chosen_cell_takes_the_standard_cell(self):
        for k in range(4):
            c = list(self.OK)
            c[k] = False
            self.assertEqual(S.decide("a", "std", True, c, self.OK), ("standard", "std"))

    def test_the_standard_cell_must_pass_the_four_clauses_too(self):
        for k in range(4):
            c = list(self.OK)
            c[k] = False
            self.assertEqual(S.decide("a", "std", False, self.OK, c), ("keep-current", None))
            self.assertEqual(S.decide("std", "std", True, c, c), ("keep-current", None))

    def test_a_chosen_cell_that_is_the_standard_one_ships_it_when_it_passes(self):
        self.assertEqual(S.decide("std", "std", True, self.OK, self.OK), ("standard", "std"))


def stats_of(f, b, n=200, same=0):
    """p4c.stats of f fixes and b breaks (and `same` rows right in both) against the base."""
    base = [False] * f + [True] * b + [True] * same + [False] * (n - f - b - same)
    new = [True] * f + [False] * b + [True] * same + [False] * (n - f - b - same)
    return p4c.stats(base, new)


class Clauses(unittest.TestCase):
    def test_clause_1_discordtune(self):
        v = lambda f, b, n=4958: S.verdict("1", stats_of(f, b, n))
        self.assertTrue(v(0, 0))
        self.assertTrue(v(30, 26))                       # net >= 0, breaks <= 26
        self.assertFalse(v(10, 27))                      # net < 0, p < 0.05 (exact McNemar), breaks > 26, ratio > 0.6
        self.assertTrue(v(60, 27))                       # breaks 27 > 26 but ratio 0.45 <= 0.6
        self.assertTrue(v(45, 27))                       # ratio exactly 0.6
        self.assertFalse(v(44, 27))                      # 0.614 (and the old 0.5 limit would fail the line above)
        self.assertTrue(v(20, 22))                       # net < 0 but p >= 0.05 and breaks <= 26
        self.assertFalse(v(5, 26))                       # net < 0 and p < 0.05 even though breaks <= 26
        self.assertTrue(v(26, 26))                       # net 0

    def test_clause_2_typing(self):
        v = lambda f, b: S.verdict("2", stats_of(f, b, 76))
        self.assertTrue(v(0, 0))
        self.assertTrue(v(2, 2))
        self.assertFalse(v(0, 1))                        # net < 0
        self.assertFalse(v(5, 3))                        # breaks > 2
        self.assertTrue(v(3, 2))

    def test_clause_4_user_reported(self):
        v = lambda f, b: S.verdict("4", stats_of(f, b, 157))
        self.assertFalse(v(0, 0))                        # net 0: the registered inference, 0 fixes 0 breaks does not pass
        self.assertTrue(v(2, 0))
        self.assertTrue(v(5, 3))                         # ratio exactly 0.6
        self.assertFalse(v(5, 4))
        self.assertFalse(v(4, 4))

    def test_clause_3_bootstrap_lower_bound_is_judged_against_the_level_shipping_value(self):
        n = 200
        # every row: the cell picks candidate 1 (ll favours it at delta 0 / alpha 0.5), whose ok is [o, 1 - o][1]
        rows = [row([-10.0, -10.3], [-5.0, -3.0], [1, 0], [0.0, 0.0], [0.0, 0.0]) for _ in range(n)]
        cell = (0.5, 2.0, 0.0)
        for r in rows:
            r["sc"]["ed"] = [0, 3]
        ng_ok = [True] * n
        same = [(1, 0, 4)] * n
        arm = S.arm_of("cvtune-clean", rows, [True] * n, same, ng_ok, (0.5, 2.0, 100.0), S.STD)      # delta 100: keeps the first, identical to ship
        self.assertTrue(arm["verdict"])
        self.assertEqual(arm["bootstrap90_lower_rows"], 0)
        for r in rows[:30]:                                                                          # 30 rows where the cell picks the wrong candidate
            r["sc"]["ok"] = [1, 0]
        for r in rows[30:]:
            r["sc"]["ok"] = [0, 0]
        ship_ok = [True] * 30 + [False] * (n - 30)
        ship_rows = [(int(o), 0 if o else 3, 4) for o in ship_ok]
        arm = S.arm_of("cvtune-clean", rows, ship_ok, ship_rows, ng_ok, cell, S.STD)
        self.assertEqual(arm["vs_ship"]["breaks"], 30)
        self.assertFalse(arm["verdict"])
        self.assertLess(arm["bootstrap90_lower_pp"], -1.0)

    def test_arm_numbers_are_against_the_shipping_value_and_the_ngram_first(self):
        # ngram first right on rows 0..3; ship right on 0..1; the cell keeps the first (delta 100) -> vs ship: 2 fixes; vs ngram: none
        rows = [row([-10.0, -10.3], [-5.0, -3.0], [int(i < 4), 0], [0.0, 0.0], [0.0, 0.0]) for i in range(10)]
        ship_ok = [i < 2 for i in range(10)]
        arm = S.arm_of("typing76", rows, ship_ok, [], [bool(r["sc"]["ok"][0]) for r in rows], (0.5, 2.0, 100.0), S.STD)
        self.assertEqual((arm["vs_ship"]["fixes"], arm["vs_ship"]["breaks"]), (2, 0))
        self.assertEqual(arm["vs_ngram"], {"fixes": 0, "breaks": 0, "net": 0})
        self.assertEqual(arm["ok"], "1111000000")
        self.assertIs(arm["verdict"], True)

    def test_dev302_is_report_only_with_row_numbers(self):
        rows = [row([-10.0, -10.3], [-5.0, -3.0], [int(i % 2 == 0), int(i % 2 == 1)], [0.0, 0.0], [0.0, 0.0]) for i in range(6)]
        ship_ok = [True, True, False, False, True, False]                     # the cell (delta 0) picks candidate 1: right on odd rows
        arm = S.arm_of("dev302", rows, ship_ok, [], [True] * 6, (0.5, 2.0, 0.0), S.STD)
        self.assertNotIn("verdict", arm)
        self.assertEqual((arm["fix_rows"], arm["break_rows"]), ([4, 6], [1, 5]))


class Anchors(unittest.TestCase):
    def rows(self):
        # row 1: the standard cell moves to candidate 1 (ll favours it); row 2: keeps the first
        r1 = row([-10.0, -10.3], [-5.0, -3.0], [0, 1], [0.0, 0.0], [0.0, 0.0], c=1)
        r2 = row([-10.0, -10.3], [-5.0, -5.0], [1, 0], [0.0, 0.0], [0.0, 0.0], c=0)
        return [r1, r2]

    def test_anchor_1_passes_when_pick_and_ok_agree(self):
        self.assertEqual(S.anchor1(self.rows(), [True, True], S.STD), [1, 0])

    def test_anchor_1_stops_on_a_pick_that_differs(self):
        rows = self.rows()
        rows[1]["sc"]["c"] = 1
        exits(self, S.anchor1, rows, [True, True], S.STD)

    def test_anchor_1_stops_on_an_ok_that_differs(self):
        exits(self, S.anchor1, self.rows(), [True, False], S.STD)

    def test_anchor_1_stops_when_the_cell_would_pick_differently_from_the_cli(self):
        rows = self.rows()
        rows[0]["ch"] = [0.0, -9.0]                       # a reading term the CLI did not apply
        exits(self, S.anchor1, rows, [True, True], S.STD)

    def level(self, params):
        rows = self.rows()
        recs = [dict(r["sc"], i=i, c=S.plain_pick(r["sc"], params)) for i, r in enumerate(rows, 1)]
        ship_ok = [bool(r["sc"]["ok"][rec["c"]]) for r, rec in zip(rows, recs)]
        return rows, recs, ship_ok

    def test_anchor_2_passes_for_the_plain_pick_with_the_header_triple(self):
        for params in ((1.0, 2.0, 3.0), (0.3, 2.0, 0.25), (1.0, 2.0, 0.0)):
            rows, recs, ship_ok = self.level(params)
            S.anchor2("conservative", rows, recs, ship_ok, params)

    def test_anchor_2_uses_the_levels_triple_not_the_cells(self):
        rows, recs, ship_ok = self.level((0.3, 2.0, 0.0))        # picks candidate 1 on row 1 (0.3 * 2 = 0.6 - 0.3 >= 0)
        self.assertEqual(recs[0]["c"], 1)
        exits(self, S.anchor2, "aggressive", rows, recs, ship_ok, (0.3, 2.0, 100.0))

    def test_anchor_2_stops_on_a_pick_ok_or_count_that_differs(self):
        params = (0.3, 2.0, 0.0)
        rows, recs, ship_ok = self.level(params)
        recs[0]["c"] = 0
        exits(self, S.anchor2, "aggressive", rows, recs, ship_ok, params)
        rows, recs, ship_ok = self.level(params)
        exits(self, S.anchor2, "aggressive", rows, recs, [not ship_ok[0], ship_ok[1]], params)
        exits(self, S.anchor2, "aggressive", rows, recs[:1], ship_ok, params)
        exits(self, S.anchor2, "aggressive", rows, recs, ship_ok[:1], params)

    def test_anchor_2_stops_when_the_runs_are_not_the_same_candidates(self):
        params = (1.0, 2.0, 3.0)
        for key, val in (("ng", [-10.0, -10.4]), ("ok", [1, 1]), ("ll", [-5.0, -3.1]), ("glen", 5)):
            rows, recs, ship_ok = self.level(params)
            recs[0][key] = val
            exits(self, S.anchor2, "conservative", rows, recs, ship_ok, params)
        rows, recs, ship_ok = self.level(params)
        del recs[0]["ll"]
        exits(self, S.anchor2, "conservative", rows, recs, ship_ok, params)


def write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


class Checks(unittest.TestCase):
    def test_row_hashes_are_the_registered_ones(self):
        for n in ("chatclause", "discordtune", "cvtune-clean", "typing76", "dev302"):
            self.assertEqual(S.ROW_SHA256[n], S.Q.ROW_SHA256[n])
        self.assertEqual(set(S.ROW_SHA256), set(S.ROW_COUNTS))
        self.assertEqual(S.ROW_COUNTS, {"chatclause": 5761, "discordtune": 4958, "cvtune-clean": 3286, "typing76": 76, "user-reported": 157, "dev302": 302})

    def test_public_rows_files_match_their_hash_and_count(self):
        for n, p in (("typing76", "eval/bench/suite-v1/typing76.txt"), ("dev302", "eval/bench/suite-v1/dev302.txt"), ("user-reported", "eval/dev/user-reported.txt")):
            path = os.path.join(S.ROOT, p)
            S.check_rows_sha(n, path)
            with open(path, encoding="utf-8") as f:
                self.assertEqual(sum(1 for l in f if l.strip()), S.ROW_COUNTS[n])

    def test_rows_hash_mismatch_stops(self):
        with tempfile.TemporaryDirectory() as d:
            exits(self, S.check_rows_sha, "typing76", write(os.path.join(d, "r.txt"), "x|y|z\n"))

    def test_row_count_mismatch_stops(self):
        S.check_count("x", 5, 5)
        exits(self, S.check_count, "x", 4, 5)

    def test_model_hash_mismatch_stops(self):
        sjtl, t2 = (os.path.join(S.ROOT, "data/lm", f) for f in ("tinylm.sjtl", "colloquial.sjlm"))
        if not (os.path.exists(sjtl) and os.path.exists(t2)):
            self.skipTest("data/lm models not present")
        S.check_models(sjtl, t2)
        with tempfile.TemporaryDirectory() as d:
            other = write(os.path.join(d, "x"), "not a model")
            exits(self, S.check_models, other, t2)
            exits(self, S.check_models, sjtl, other)

    def test_header_triples_are_the_readmes(self):
        sjtl = os.path.join(S.ROOT, "data/lm/tinylm.sjtl")
        if not os.path.exists(sjtl):
            self.skipTest("data/lm models not present")
        h = S.check_headers(sjtl)
        self.assertEqual((h["standard"], h["conservative"], h["aggressive"]), ((0.3, 2.0, 0.25), (1.0, 2.0, 3.0), (0.3, 2.0, 0.25)))
        with mock.patch.dict(S.SHIP, {"conservative": (1.0, 2.0, 2.0)}):
            exits(self, S.check_headers, sjtl)
        with tempfile.TemporaryDirectory() as d:
            exits(self, S.header_triples, write(os.path.join(d, "x"), "\0" * 400))

    TL = "e7c179d8"
    CO = S.P.T2_SHA256[:8]

    def tag(self, level, rc=None, rl=None, tl=TL, extra=""):
        rl = {"standard": "", "conservative": "+rl:conservative", "aggressive": "+rl:aggressive"}[level] if rl is None else rl
        rc = (level == "standard") if rc is None else rc
        return f"lm-chat+ctx+kn:cbefbced:θ1:β1+tl:{tl}{rl}+acg+colloq:{self.CO}{'+rc' if rc else ''}{extra}"

    def test_tags(self):
        for lv in ("standard", "conservative", "aggressive"):
            S.check_tag(self.tag(lv), lv, self.TL)
        bad = [("standard", self.tag("standard", rc=False)), ("conservative", self.tag("conservative", rc=True)),
               ("conservative", self.tag("standard")), ("standard", self.tag("conservative")), ("aggressive", self.tag("conservative")),
               ("standard", self.tag("standard", tl="00000000")), ("standard", self.tag("standard").replace("+acg", "")),
               ("standard", self.tag("standard").replace(self.CO, "00000000")), ("standard", self.tag("standard").replace("lm-chat", "lm-formal")),
               ("standard", self.tag("standard", extra="-nodemote")), ("standard", self.tag("standard").replace("+ctx", ""))]
        for lv, t in bad:
            exits(self, S.check_tag, t, lv, self.TL)

    def test_summary(self):
        with tempfile.TemporaryDirectory() as d:
            p = write(os.path.join(d, "s"), "noise\n## typing76  lm-chat+ctx+tl:e7c179d8  {'n': 76, 'top1': 72, 'oracle@64': 76, 'top1_sha256': 'x'}\n")
            self.assertEqual(S.read_summary(p), ("lm-chat+ctx+tl:e7c179d8", 76, 72))
            exits(self, S.read_summary, write(os.path.join(d, "t"), "no summary\n"))

    def level_args(self, d, n, top1, tag, recs=None):
        sjtl = write(os.path.join(d, "m.sjtl"), "model")
        sha8 = S.sha_file(sjtl)[:8]
        tag = tag.replace(self.TL, sha8)
        a = mock.Mock(sjtl=sjtl)
        a.cons_summary = write(os.path.join(d, "sum"), f"## x  {tag}  {{'n': {n}, 'top1': {top1}, 'oracle@64': 1, 'top1_sha256': 'x'}}\n")
        a.cons_rowstats = write(os.path.join(d, "rs"), "".join(f"{i}\t{int(i <= 2)}\t0\t4\n" for i in range(1, n + 1)))
        recs = [scores([-1.0, -2.0], None, [1, 0]) for _ in range(n)] if recs is None else recs
        a.cons_scores = write(os.path.join(d, "sc"), "".join(json.dumps(r) + "\n" for r in recs))
        return a

    def test_load_level_checks_counts_top1_and_tag(self):
        with tempfile.TemporaryDirectory() as d:
            base, recs = S.load_level(self.level_args(d, 3, 2, self.tag("conservative")), "conservative", 3)
            self.assertEqual((len(base), len(recs)), (3, 3))
            exits(self, S.load_level, self.level_args(d, 3, 2, self.tag("conservative")), "conservative", 4)       # registered count
            exits(self, S.load_level, self.level_args(d, 3, 1, self.tag("conservative")), "conservative", 3)       # summary top1
            exits(self, S.load_level, self.level_args(d, 3, 2, self.tag("standard")), "conservative", 3)           # tag of another level
            exits(self, S.load_level, self.level_args(d, 3, 2, self.tag("conservative"), recs=[scores([-1.0, -2.0], None, [1, 0])] * 2), "conservative", 3)


def run_json(name, lv_data, sel_sha, n=None):
    """A `run --sel` output with the given {level: (chosen label, {arm: (vs_ngram (f, b), vs_ship (f, b), verdict)})}."""
    def arm(cell, fb_ng, fb_ship, verdict):
        a = {"cell": cell, "vs_ngram": pair_stats(fb_ng), "vs_ship": {**pair_stats(fb_ship), "p": 0.5, "error_ratio": 0.0}}
        if verdict is not None:
            a["verdict"] = verdict
        return a
    return {"name": name, "n": S.ROW_COUNTS[name] if n is None else n, "anchor1": "ok", "anchor2": {lv: "ok" for lv in S.LEVELS}, "selection_sha256": sel_sha,
            "levels": {lv: {"chosen": chosen, "arms": {k: arm(*v) for k, v in arms.items()}} for lv, (chosen, arms) in lv_data.items()}}


class Final(unittest.TestCase):
    STD, CH_C, CH_A = S.label(S.STD), S.label(S.CONS[2]), S.label(S.AGGR[4])

    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)
        self.sel = os.path.join(self.d.name, "sel.json")
        jdump({"chosen": {"conservative": self.CH_C, "aggressive": self.CH_A}, "selection_fallback": {"conservative": False, "aggressive": False}}, self.sel)
        self.sha = S.sha_file(self.sel)

    def good(self, name):
        role = S.ROLE[name]
        v = None if name == "dev302" else True
        arms = lambda ch: {"std": (self.STD, (100, 20), (10, 1), v), "chosen": (ch, (60, 10), (8, 1), v)}
        return run_json(name, {"conservative": (self.CH_C, arms(self.CH_C)), "aggressive": (self.CH_A, {"std": (self.STD, (100, 20), (10, 1), v),
                                                                                                           "chosen": (self.CH_A, (110, 25), (12, 1), v)})}, self.sha)

    def write_runs(self, edit=None):
        runs = {n: self.good(n) for n in S.JUDGED}
        if edit:
            edit(runs)
        paths = []
        for n, r in runs.items():
            p = os.path.join(self.d.name, f"{n}.json")
            jdump(r, p)
            paths.append(p)
        return paths

    def final(self, edit=None):
        out = os.path.join(self.d.name, "final.json")
        self.assertEqual(S.main(["final", self.sel] + self.write_runs(edit) + ["--out", out]), 0)
        return jload(out)["levels"]

    def test_everything_passes_ships_the_chosen_cells(self):
        r = self.final()
        self.assertEqual((r["conservative"]["decision"], r["conservative"]["ship_cell"]), ("chosen", self.CH_C))
        self.assertEqual((r["aggressive"]["decision"], r["aggressive"]["ship_cell"]), ("chosen", self.CH_A))
        self.assertTrue(all(r["conservative"]["gates"].values()) and len(r["conservative"]["gates"]) == 5)

    def test_one_gate_failing_takes_the_standard_cell_of_that_level_only(self):
        def edit(runs):                                  # conservative: more breaks than the standard on typing76
            runs["typing76"]["levels"]["conservative"]["arms"]["chosen"]["vs_ngram"] = pair_stats((60, 21))
        r = self.final(edit)
        self.assertEqual((r["conservative"]["decision"], r["conservative"]["ship_cell"]), ("standard", self.STD))
        self.assertEqual(r["aggressive"]["decision"], "chosen")

    def test_a_failed_clause_of_the_chosen_cell_takes_the_standard_cell(self):
        def edit(runs):
            runs["cvtune-clean"]["levels"]["aggressive"]["arms"]["chosen"]["verdict"] = False
        r = self.final(edit)
        self.assertEqual((r["aggressive"]["decision"], r["aggressive"]["ship_cell"]), ("standard", self.STD))
        self.assertEqual(r["aggressive"]["clauses_chosen"]["3"], False)

    def test_the_standard_cell_failing_too_ships_nothing(self):
        def edit(runs):
            for k in ("std", "chosen"):
                runs["user-reported"]["levels"]["conservative"]["arms"][k]["verdict"] = False
        r = self.final(edit)
        self.assertEqual((r["conservative"]["decision"], r["conservative"]["ship_cell"]), ("keep-current", None))
        self.assertEqual(r["aggressive"]["decision"], "chosen")

    def test_dev302_never_decides(self):
        def edit(runs):
            runs["dev302"]["levels"]["conservative"]["arms"]["chosen"]["vs_ship"]["breaks"] = 99
        self.assertEqual(self.final(edit)["conservative"]["decision"], "chosen")

    def test_a_standard_selection_has_no_gates(self):
        sel = jload(self.sel)
        sel["chosen"]["conservative"] = self.STD
        jdump(sel, self.sel)
        self.sha = S.sha_file(self.sel)
        r = self.final(lambda runs: [runs[n]["levels"]["conservative"].update(chosen=self.STD) for n in runs])
        self.assertIsNone(r["conservative"]["gates"])
        self.assertEqual((r["conservative"]["decision"], r["conservative"]["ship_cell"]), ("standard", self.STD))

    def stops(self, edit):
        exits(self, S.main, ["final", self.sel] + self.write_runs(edit))

    def test_final_refuses_unanchored_foreign_missing_or_duplicate_runs(self):
        self.stops(lambda runs: runs["typing76"].update(anchor1="no"))
        self.stops(lambda runs: runs["typing76"]["anchor2"].update(aggressive="no"))
        self.stops(lambda runs: runs["typing76"].update(selection_sha256="0" * 64))
        self.stops(lambda runs: runs["typing76"].update(n=75))
        self.stops(lambda runs: runs["typing76"]["levels"]["aggressive"].update(chosen=self.STD))
        self.stops(lambda runs: runs["typing76"].update(name="dev302"))
        self.stops(lambda runs: runs.pop("typing76"))
        exits(self, S.main, ["final", self.sel] + self.write_runs()[:4])
        paths = self.write_runs()
        exits(self, S.main, ["final", self.sel] + paths + paths[:1])


class Cli(unittest.TestCase):
    def test_chatclause_takes_cells_never_sel(self):
        a = ["run", "--name", "chatclause", "--rows", "x", "--sjtl", "x", "--t2", "x", "--dump", "x", "--dump-words", "x", "--rowstats", "x",
             "--tinylm-scores", "x", "--summary", "x", "--out", "x"]
        for p in S.ARGS.values():
            for k in ("rowstats", "scores", "summary"):
                a += [f"--{p}-{k}", "x"]
        exits(self, S.main, a + ["--sel", "x"])

    def test_run_refuses_a_wrong_rows_file_before_anything_is_read(self):
        a = ["run", "--name", "typing76", "--rows", os.path.join(S.ROOT, "eval/bench/suite-v1/dev302.txt"), "--sjtl", "x", "--t2", "x", "--dump", "x",
             "--dump-words", "x", "--rowstats", "x", "--tinylm-scores", "x", "--summary", "x", "--out", os.path.join(tempfile.gettempdir(), "sc-test-out.json")]
        for p in S.ARGS.values():
            for k in ("rowstats", "scores", "summary"):
                a += [f"--{p}-{k}", "x"]
        exits(self, S.main, a + ["--cells"])


if __name__ == "__main__":
    unittest.main(buffer=True)
