"""S7a unit tests, numpy only: fusion gating and tie rules, frozen selection (tuning sets only), rowstats, the table driver,
dump loading and score records."""
import contextlib
import copy
import io
import json
import math
import os
import re
import tempfile
import unittest

import numpy as np

import tinylm
from tinylm import Vocab

import evalstats  # noqa: E402  (tools/, on sys.path via tinylm)
import fusegrid as fg
import score


def rec(i, ng, ll, ok, ed=None, key=2, ll0=None, key16=2, glen=6):
    n = len(ng)
    return {"i": i, "n": n, "ng": ng, "ok": ok, "ed": ed or [0 if o else 2 for o in ok], "glen": glen, "key": key, "key16": key16,
            **({"ll": ll, "ll0": ll0 if ll0 is not None else ll, "ll16": ll} if n >= 2 else {})}


class TestPick(unittest.TestCase):
    def test_tau_gate_is_strict_less_than(self):
        r = rec(1, [-1.0, -1.5], [-3.0, -1.0], [0, 1])      # margin exactly 0.5; fusion would prefer candidate 1 (alpha > 0.25)
        self.assertEqual(fg.pick(r, 1.0, 0.5), 0)           # margin < tau is false at the boundary: first
        self.assertEqual(fg.pick(r, 1.0, 1.0), 1)           # inside the gate: reranked
        self.assertEqual(fg.pick(r, 1.0, math.inf), 1)      # no threshold
        self.assertEqual(fg.pick(r, 0.1, 1.0), 0)           # small alpha: the n-gram still wins

    def test_alpha_threshold(self):
        r = rec(1, [-1.0, -1.5], [-3.0, -1.0], [0, 1])      # s0 = -1 - 3a, s1 = -1.5 - a: candidate 1 wins iff a > 0.25
        self.assertEqual([fg.pick(r, a, math.inf) for a in (0.2, 0.3)], [0, 1])

    def test_ties_go_to_the_better_ngram_rank(self):
        r = rec(1, [-1.0, -1.5], [-1.0, -0.5], [1, 0])      # alpha 1: s0 = -2.0 = s1
        self.assertEqual(fg.pick(r, 1.0, math.inf), 0)
        r3 = rec(1, [-1.0, -1.5, -1.5], [-9.0, -1.0, -1.0], [0, 1, 0])    # candidates 1 and 2 tie: rank 2 (index 1)
        self.assertEqual(fg.pick(r3, 1.0, math.inf), 1)

    def test_single_candidate_rows_keep_the_first(self):
        self.assertEqual(fg.pick(rec(1, [-1.0], [], [1]), 1.0, math.inf), 0)

    def test_fusion_actually_reranks(self):
        # guards against a no-op fusion: over a grid some row must move off the first candidate and gain
        r = rec(1, [-1.0, -2.5], [-4.0, 0.0], [0, 1])
        picks = {fg.pick(r, a, t) for a in fg.ALPHAS for t in fg.TAUS}
        self.assertEqual(picks, {0, 1})
        self.assertEqual(fg.correct([r], 2, math.inf), 1)
        self.assertEqual(fg.correct([r], 0.1, 0.5), 0)


class TestDelta(unittest.TestCase):
    """S7e section 2.2: the delta gate."""

    def rows(self, n=300, seed=7):
        rng = np.random.default_rng(seed)
        out = []
        for i in range(1, n + 1):
            k = int(rng.integers(2, 6))
            ng = sorted((float(-1 - rng.uniform(0, 3)) for _ in range(k)), reverse=True)
            ll = [float(-3 + rng.normal(0, 1.5)) for _ in range(k)]
            ok = [int(j == int(rng.integers(0, k))) for j in range(k)]
            out.append(rec(i, ng, ll, ok))
        return out

    @staticmethod
    def old_pick(r, alpha, tau):
        """pick as it was before S7e (origin/main 59adc18), so delta 0 is compared with the old formula, not with itself."""
        if r["n"] < 2 or r["ng"][0] - r["ng"][1] >= tau:
            return 0
        s = [g + alpha * l for g, l in zip(r["ng"], r["ll"])]
        return max(range(len(s)), key=lambda i: (s[i], -i))

    def test_delta_zero_is_the_old_pick(self):
        rows = self.rows()
        for a in fg.ALPHAS:
            for t in fg.TAUS:
                old = [self.old_pick(r, a, t) for r in rows]
                self.assertEqual([fg.pick(r, a, t) for r in rows], old)
                self.assertEqual([fg.pick(r, a, t, "ll", 0) for r in rows], old)
        self.assertTrue(any(fg.pick(r, 2, math.inf) for r in rows))       # the rows do get reranked at delta 0

    def test_large_delta_keeps_the_first(self):
        rows = self.rows()
        self.assertEqual({fg.pick(r, 2, math.inf, "ll", 1e9) for r in rows}, {0})

    def test_delta_boundary_is_inclusive(self):
        r = rec(1, [-1.0, -1.5], [-3.0, -1.0], [0, 1])      # alpha 1: s0 = -4.0, s1 = -2.5, so s1 - s0 = 1.5
        self.assertEqual(fg.pick(r, 1.0, math.inf, "ll", 1.5), 1)
        self.assertEqual(fg.pick(r, 1.0, math.inf, "ll", 1.75), 0)

    def test_fixed_broken_counts_against_the_first(self):
        fix = rec(1, [-1.0, -1.5], [-3.0, -1.0], [0, 1])
        brk = rec(2, [-1.0, -1.5], [-3.0, -1.0], [1, 0])
        same = rec(3, [-1.0, -1.5], [-3.0, -1.0], [0, 0])
        self.assertEqual(fg.fixed_broken([fix, brk, same], 1.0, math.inf, 0), (1, 1))
        self.assertEqual(fg.fixed_broken([fix, brk, same], 1.0, math.inf, 9), (0, 0))


class TestSelectWeighted(unittest.TestCase):
    """S7e section 2.2: select by fixed - W x broken; ties fewer broken, larger delta, smaller tau, smaller alpha."""

    def sets(self):
        # 4 rows the fusion fixes with a big fused gap (2.5 at alpha 1) and 1 row it breaks with a small gap (0.5 at alpha 1):
        # every row has n-gram margin 0.5, so tau 0.5 shuts the gate and tau >= 1 opens it
        fixes = [rec(i, [-1.0, -1.5], [-4.0, -1.0], [0, 1]) for i in range(1, 5)]
        breaks = [rec(5, [-1.0, -1.5], [-2.0, -1.0], [1, 0])]
        return {"cvtune": fixes + breaks, "wikitune": [], "dev302": [rec(1, [-1.0, -1.5], [-4.0, -1.0], [1, 0])]}

    def test_weight_decides_between_more_fixes_and_no_breaks(self):
        s = self.sets()
        a1, t1, d1, f1, b1 = fg.select_weighted(s, 1)
        self.assertEqual((f1 - b1, b1), (4, 0))          # weight 1: a delta that drops the break wins on ties by fewer breaks
        a10, t10, d10, f10, b10 = fg.select_weighted(s, 10)
        self.assertEqual((f10, b10), (4, 0))
        # with the break impossible to drop (same gap as the fixes), weight 1 keeps the fusion and weight 10 turns it off
        s2 = {"cvtune": [rec(i, [-1.0, -1.5], [-4.0, -1.0], [0, 1]) for i in range(1, 3)]
                        + [rec(3, [-1.0, -1.5], [-4.0, -1.0], [1, 0])], "wikitune": []}
        self.assertEqual(fg.select_weighted(s2, 1)[3:], (2, 1))
        self.assertEqual(fg.select_weighted(s2, 10)[3:], (0, 0))

    def test_ties_fewer_broken_then_larger_delta_then_smaller_tau_then_smaller_alpha(self):
        s = self.sets()
        a, t, d, f, b = fg.select_weighted(s, 10)
        # every (alpha, tau, delta) that fixes 4 and breaks 0 ties; the largest such delta wins, then the smallest tau, alpha
        cands = [(al, ta, de) for al in fg.ALPHAS for ta in fg.TAUS for de in fg.DELTAS
                 if fg.fixed_broken(s["cvtune"], al, ta, de) == (4, 0)]
        dmax = max(c[2] for c in cands)
        tmin = min(c[1] for c in cands if c[2] == dmax)
        amin = min(c[0] for c in cands if c[2] == dmax and c[1] == tmin)
        self.assertEqual((a, t, d), (amin, tmin, dmax))

    def test_selection_reads_only_the_tuning_sets(self):
        s = self.sets()
        chosen = fg.select_weighted(s, 10)
        s["dev302"] = [rec(i, [-1.0, -1.5], [-4.0, -1.0], [1, 0]) for i in range(1, 400)]
        s["discordtune"] = [rec(i, [-1.0, -1.5], [-4.0, -1.0], [1, 0]) for i in range(1, 400)]
        self.assertEqual(fg.select_weighted(s, 10), chosen)
        s["wikitune"] = [rec(i, [-1.0, -1.5], [-4.0, -1.0], [1, 0]) for i in range(1, 40)]    # the tuning sets do matter
        self.assertNotEqual(fg.select_weighted(s, 10), chosen)


class TestE2NamesAndCap(unittest.TestCase):
    """E2: `names` picks the pooled sets, `cap` bars cells whose broken / rows is above it; neither given = S7e behaviour."""

    def sets(self):
        fix = lambda i: rec(i, [-1.0, -1.5], [-4.0, -1.0], [0, 1])
        brk = lambda i: rec(i, [-1.0, -1.5], [-4.0, -1.0], [1, 0])
        # cvtune + wikitune: 2 fixes and 1 break with the same gap, so no delta separates them; "judge": 3 breaks only
        return {"cvtune": [fix(1), fix(2), brk(3)], "wikitune": [], "judge": [brk(1), brk(2), brk(3)]}

    def test_names_selects_which_sets_are_read(self):
        s = self.sets()
        self.assertEqual(fg.select_weighted(s, 1)[3:], (2, 1))
        self.assertEqual(fg.select_weighted(s, 1, names=("cvtune", "wikitune")), fg.select_weighted(s, 1))
        self.assertEqual(fg.select_weighted(s, 1, names=("judge",))[3:], (0, 0))      # only the break rows: fusion off
        self.assertEqual(fg.fixed_broken(s, 1.0, math.inf, 0, names=("cvtune",)), (2, 1))
        self.assertEqual(fg.fixed_broken(s, 1.0, math.inf, 0, names=("judge",)), (0, 3))
        self.assertEqual(fg.fixed_broken(s, 1.0, math.inf, 0, names=("cvtune", "judge")), (2, 4))
        with self.assertRaises(KeyError):                                              # a name that is absent is an error, not skipped
            fg.select_weighted({"cvtune": s["cvtune"]}, 1)                             # default TUNING needs wikitune

    def test_cap_bars_cells_over_the_break_rate(self):
        s = self.sets()
        self.assertEqual(fg.select_weighted(s, 1)[3:], (2, 1))                         # no cap: the fusion that breaks 1 of 3 wins
        a, t, d, f, b = fg.select_weighted(s, 1, cap=0.2)                              # 1 / 3 > 0.2 for every cell that fixes
        self.assertEqual((f, b), (0, 0))
        self.assertEqual(fg.select_weighted(s, 1, cap=1 / 3)[3:], (2, 1))              # the boundary is allowed (<=)

    def test_cap_with_no_admissible_cell_is_an_error(self):
        s = {"cvtune": [rec(i, [-1.0, -1.5], [-4.0, -1.0], [1, 0]) for i in range(1, 4)], "wikitune": []}
        # a negative cap admits no cell (broken >= 0 is always above it)
        with self.assertRaises(ValueError):
            fg.select_weighted(s, 1, cap=-1)


class TestSelect(unittest.TestCase):
    def sets(self):
        # margin 1.5: tau in {2, 3, 5, inf} lets fusion in; candidate 1 beats 0 iff 4a > 1.5 (a >= 0.5)
        win = [rec(i, [-1.0, -2.5], [-4.0, 0.0], [0, 1]) for i in range(1, 6)]
        return {"cvtune": copy.deepcopy(win), "wikitune": copy.deepcopy(win), "dev302": [rec(1, [-1.0, -2.5], [0.0, -4.0], [1, 0])]}

    def test_tie_rule_smaller_tau_then_smaller_alpha(self):
        a, t, c = fg.select(self.sets())
        self.assertEqual((a, t, c), (0.5, 2, 10))

    def test_all_equal_picks_the_first_grid_cell(self):
        flat = {"cvtune": [rec(1, [-1.0], [], [1])], "wikitune": [rec(1, [-1.0], [], [0])]}
        self.assertEqual(fg.select(flat), (0.1, 0.5, 1))

    def test_selection_reads_only_the_tuning_sets(self):
        s = self.sets()
        chosen = fg.select(s)
        s["dev302"] = [rec(i, [-1.0, -2.5], [-4.0, 0.0], [1, 0]) for i in range(1, 400)]         # would prefer never reranking
        s["discordtune"] = [rec(i, [-1.0, -1.1], [-0.1, -2.0], [0, 1]) for i in range(1, 400)]
        self.assertEqual(fg.select(s), chosen)
        only = {k: s[k] for k in fg.TUNING}
        self.assertEqual(fg.select(only), chosen)                  # the other sets are not even needed
        s["cvtune"] = [rec(i, [-1.0, -2.5], [-4.0, 0.0], [1, 0]) for i in range(1, 50)]         # but the tuning sets do matter
        self.assertNotEqual(fg.select(s), chosen)

    def test_kept_rows_only_drive_the_selection(self):
        # S7d: rows flagged 1 (gold in the training text) are left out of the selection, whatever their scores
        s = self.sets()
        chosen = fg.select(s)
        keep = {"cvtune": [0] * 5 + [1] * 60, "wikitune": [0] * 5}
        s["cvtune"] += [rec(i, [-1.0, -2.5], [-4.0, 0.0], [1, 0]) for i in range(6, 66)]           # would prefer never reranking
        self.assertNotEqual(fg.select(s), chosen)                                                   # they matter when kept ...
        self.assertEqual(fg.select(fg.kept(s, keep)), chosen)                                       # ... and not when flagged
        self.assertEqual(fg.select(fg.kept(s, {t: [0] * len(s[t]) for t in fg.TUNING})), fg.select(s))   # all 0: same as no flags
        self.assertIs(fg.kept(s, None), s)

    def test_kept_rows_header_counts_only_the_kept_rows(self):
        s = self.sets()
        s["cvtune"] += [rec(i, [-1.0, -2.5], [-4.0, 0.0], [1, 0]) for i in range(6, 66)]     # 60 flagged rows that prefer never reranking
        keep = {"cvtune": [0] * 5 + [1] * 60, "wikitune": [0] * 5}
        self.assertNotEqual(fg.select(s)[:2], (0.5, 2))                                      # on all rows the selection differs ...
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as td, contextlib.redirect_stdout(out):
            os.makedirs(os.path.join(td, "rowstats"))
            _cells, gain, _stop = fg.run_setting("S", "chat", s, td, keep)
        # ... run_setting selects on the 10 kept rows: all wrong at the n-gram first, all right after fusion -> 0 -> 10 of 10, +100 pt
        self.assertIn("frozen on cvtune+wikitune (kept rows): alpha 0.5, tau 2; pooled top1 0 -> 10 of 10 (+100.00 pt)", out.getvalue())
        self.assertAlmostEqual(gain, 100.0)
        # the tables still use every row: cvtune has 65 rows in its table line
        self.assertRegex(out.getvalue(), r"\| S chat cvtune \| 65 \|")

    def test_tune_keep_refuses_bad_flags_and_empty_selections(self):
        s = self.sets()
        with tempfile.TemporaryDirectory() as td, contextlib.redirect_stdout(io.StringIO()):
            os.makedirs(os.path.join(td, "rowstats"))
            with self.assertRaises(SystemExit):
                fg.run_setting("S", "chat", s, td, {"cvtune": [1] * 5, "wikitune": [1] * 5})

    def test_pooled_over_both_sets(self):
        good = [rec(i, [-1.0, -2.5], [-4.0, 0.0], [0, 1]) for i in range(1, 4)]
        bad = [rec(i, [-1.0, -2.5], [-4.0, 0.0], [1, 0]) for i in range(1, 3)]
        a, t, c = fg.select({"cvtune": good, "wikitune": bad})
        self.assertEqual(c, 3 + 0)                     # 3 fixed vs 2 broken at best: base 2 correct -> 3 with fusion
        self.assertEqual((fg.correct(good + bad, 0.1, 0.5)), 2)


class TestRowstats(unittest.TestCase):
    def test_same_text_as_evalstats_format_rowstats(self):
        gold = ["我們去吃飯", "今天天氣", "他很好"]
        cands = [["我們去吃飯", "我們去喫飯"], ["今天天器", "今天天氣"], ["他很好", "她很好"]]
        rows = []
        for i, (g, cs) in enumerate(zip(gold, cands), 1):
            ok = [int(c == g) for c in cs]
            rows.append({"i": i, "n": 2, "ng": [-1, -2], "ok": ok, "ed": [0 if o else evalstats.levenshtein(c, g) for c, o in zip(cs, ok)], "glen": len(g)})
        picks = [0, 0, 1]
        theirs = evalstats.format_rowstats([(r["ok"][p], cs[p], g) for r, p, cs, g in zip(rows, picks, cands, gold)])
        self.assertEqual(fg.rowstats(rows, picks), theirs)
        self.assertRegex(fg.rowstats(rows, picks), r"\A[0-9\t\n]+\Z")


class TestDump(unittest.TestCase):
    def test_load_dump_top8(self):
        lines = [f"1\t{r}\tc{r}\t{-r / 2!r}\n" for r in range(1, 12)] + ["2\t1\tx\t-1.0\n", "2\t2\ty\t-2.0\n"]
        c = score.load_dump(lines, 2)
        self.assertEqual([s for s, _ in c[0]], [f"c{r}" for r in range(1, 9)])
        self.assertEqual(c[0][2], ("c3", -1.5))
        self.assertEqual(len(c[1]), 2)

    def test_load_dump_stops_on_missing_rows_and_rank_gaps(self):
        with self.assertRaises(ValueError):
            score.load_dump(["1\t1\ta\t-1.0\n"], 2)                      # row 2 missing
        with self.assertRaises(ValueError):
            score.load_dump(["1\t1\ta\t-1.0\n", "1\t3\tb\t-2.0\n"], 1)   # rank 2 missing
        with self.assertRaises(ValueError):
            score.load_dump(["3\t1\ta\t-1.0\n"], 2)                      # row outside the set

    def test_top1_from_dump_uses_the_lenient_comparison(self):
        rows = [("她很好", [], ""), ("今天", [], "")]
        cands = [[("他很好", -1.0)], [("明天", -1.0)]]
        self.assertEqual(score.top1_from_dump(rows, cands), 1)           # 她/他 are equal under lenient()


class ToyLM:
    """P(next | previous) uniform except 'good' bigrams, so LL is predictable."""
    def __init__(self):
        self.vocab = Vocab(list("甲乙丙"))

    def logp10(self, ids):
        V = self.vocab.size
        p = np.full((V, V), 0.1)
        p[3, 4] = 0.7                       # P(乙|甲)
        p[2, 3] = 0.7                       # P(甲|BOS)
        p /= p.sum(1, keepdims=True)
        return np.log10(p[ids])


class TestRecords(unittest.TestCase):
    def test_records(self):
        rows = [("甲乙", [], "你好，"), ("甲乙", [], "你好"), ("乙", [], "")]
        cands = [[("甲乙", -1.0), ("甲丙", -1.2)], [("甲丙", -1.0), ("甲乙", -1.1)], [("乙", -1.0)]]
        recs = score.build_records(ToyLM(), rows, cands)
        self.assertEqual([r["i"] for r in recs], [1, 2, 3])
        self.assertEqual(recs[0]["ok"], [1, 0]); self.assertEqual(recs[1]["ok"], [0, 1])
        self.assertEqual(recs[1]["ed"], [1, 0])
        self.assertEqual((recs[0]["key"], recs[1]["key"]), (0, 2))           # '，' ends the first prefix: no key
        self.assertNotIn("ll", recs[2])
        self.assertEqual(recs[0]["ll"], recs[0]["ll0"])                      # no key -> same sequence
        self.assertNotEqual(recs[1]["ll"], recs[1]["ll0"])                   # key '你好' -> UNK context changes the first character's score
        self.assertEqual(recs[0]["ll"][0], tinylm.ll(ToyLM(), "", "甲乙"))
        self.assertLess(recs[0]["ll"][1], recs[0]["ll"][0])
        json.dumps(recs)                                                     # numbers only, serialisable

    def test_gold_perplexity_uses_the_same_ll(self):
        rows = [("甲乙", [], ""), ("甲", [], "乙")]
        p = score.gold_ppl(ToyLM(), rows)
        want = tinylm.ll(ToyLM(), "", "甲乙") + tinylm.ll(ToyLM(), "乙", "甲")
        self.assertAlmostEqual(p["sum_ll10"], want, places=9)
        self.assertAlmostEqual(p["ppl_per_char"], 10 ** (-want / 3), places=9)


class TestScoreMain(unittest.TestCase):
    def test_cli_writes_numbers_only_and_checks_the_baseline(self):
        chars = list("你好嗎我他們去吃飯")
        with tempfile.TemporaryDirectory() as td:
            npz = os.path.join(td, "m.npz")
            tinylm.save_params(npz, tinylm.random_params(chars, 1, 16, 2, seed=5), chars, 1, 2)
            with open(os.path.join(td, "rows.txt"), "w", encoding="utf-8") as f:
                f.write("你好|我們去吃飯|ㄨㄛˇ\n|他好嗎|ㄊㄚ\n")
            with open(os.path.join(td, "d.dump"), "w", encoding="utf-8") as f:
                f.write("1\t1\t我們去吃飯\t-1.5\n1\t2\t我們去吃飽\t-2.5\n2\t1\t他好嗎\t-1.0\n2\t2\t他好嘛\t-1.5\n2\t3\t你好嗎\t-2.0\n")
            args = ["--npz", npz, "--dump", os.path.join(td, "d.dump"), "--rows", os.path.join(td, "rows.txt"), "--name", "toy",
                    "--out", os.path.join(td, "o.jsonl"), "--ppl-out", os.path.join(td, "p.json")]
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit):
                    score.main(args + ["--expect-top1", "1"])                      # the dump says 2: stop
                score.main(args + ["--expect-top1", "2"])
            recs = [json.loads(l) for l in open(os.path.join(td, "o.jsonl"), encoding="utf-8")]
            self.assertEqual([r["n"] for r in recs], [2, 3])
            self.assertEqual([r["key"] for r in recs], [2, 0])
            self.assertEqual(len(recs[1]["ll"]), 3)
            self.assertRegex(open(os.path.join(td, "o.jsonl"), encoding="utf-8").read(), r'\A[\x00-\x7f]*\Z')     # no Chinese text in a score file
            self.assertGreater(json.load(open(os.path.join(td, "p.json")))["ppl_per_char"], 1.0)


class TestDriver(unittest.TestCase):
    def make_scores(self, td, wired=True, skip=()):
        rng = np.random.default_rng(0)

        def rows(n, seed, ctx):
            rng = np.random.default_rng(seed)
            out = []
            for i in range(1, n + 1):
                gold = int(rng.integers(0, 3))
                ll = [-3.0 + (2.5 if j == gold else 0) + float(rng.normal(0, .3)) for j in range(3)]
                ll0 = [x + (0.05 if ctx else 0) * (j + 1) for j, x in enumerate(ll)] if ctx and wired else list(ll)
                ng = [-1.0, -1.0 - float(rng.uniform(0, 1.2)), -3.0]
                ok = [int(j == gold) for j in range(3)]
                out.append(rec(i, ng, ll, ok, ll0=ll0, key=2 if ctx else 0))
            return out
        for st in ("chat", "formal"):
            for s, seed, ctx in (("cvtune", 1, True), ("wikitune", 2, False), ("dev302", 5, False), ("typing76", 6, False),
                                 ("user-reported", 3, True), ("discordtune", 4, True)):
                if s == "discordtune" and st == "formal":
                    continue
                if (st, s) in skip:
                    continue
                with open(os.path.join(td, f"S.{st}.{s}.jsonl"), "w") as f:
                    f.write("".join(json.dumps(r) + "\n" for r in rows(300 if s == "discordtune" else 120, seed, ctx)))

    def run_driver(self, td, wired=True, skip=()):
        self.make_scores(td, wired, skip)
        out = io.StringIO()
        code = 0
        with contextlib.redirect_stdout(out):
            try:
                fg.main(["--scores", td, "--out", os.path.join(td, "out"), "--models", "S", "--settings", "chat", "formal",
                         "--sets", "cvtune", "wikitune", "dev302", "typing76", "user-reported", "discordtune"])
            except SystemExit as e:
                code = e.code
        return out.getvalue(), code

    def test_end_to_end(self):
        with tempfile.TemporaryDirectory() as td:
            text, code = self.run_driver(td)
            self.assertEqual(code, 0, text)
            self.assertNotIn("SKIP", text)
            self.assertIn("| 集合 | n | top1 基準 | top1 新 |", text)
            self.assertEqual(len(re.findall(r"^\| S chat [\w-]+ \|", text, re.M)), 6)                 # one table row per set present
            self.assertEqual(len(re.findall(r"^\| S chat [\w-]+ \(16-char context\) \|", text, re.M)), 6)
            self.assertIn("frozen on cvtune+wikitune", text)
            self.assertIn("section 4, S", text)
            self.assertIn("(16-char context)", text)
            for f in os.listdir(os.path.join(td, "out", "rowstats")):
                self.assertRegex(open(os.path.join(td, "out", "rowstats", f)).read(), r"\A[0-9\t\n]+\Z")

    def test_missing_guard_set_fails_closed(self):
        for gone in (("chat", "typing76"), ("formal", "dev302"), ("chat", "user-reported"), ("chat", "discordtune")):
            with tempfile.TemporaryDirectory() as td:
                text, code = self.run_driver(td, skip=(gone,))
                self.assertEqual(code, 3, (gone, text))
                self.assertIn("FAIL: every guard set", text)
                self.assertIn(str(gone), text)
        with tempfile.TemporaryDirectory() as td:        # a missing context set is named, not silently skipped
            text, code = self.run_driver(td, skip=(("chat", "user-reported"),))
            self.assertIn("context not checked, no scores: S chat user-reported", text)

    def test_unwired_context_stops(self):
        with tempfile.TemporaryDirectory() as td:
            text, code = self.run_driver(td, wired=False)
            self.assertEqual(code, 3, text)
            self.assertIn("context not wired: S chat user-reported", text)

    def test_weighted_end_to_end(self):
        with tempfile.TemporaryDirectory() as td:
            self.make_scores(td)
            for st in ("chat", "formal"):                  # fw: copy of dev302's scores under the new set name
                with open(os.path.join(td, f"S.{st}.dev302.jsonl")) as src, open(os.path.join(td, f"S.{st}.fw.jsonl"), "w") as dst:
                    dst.write(src.read())
            out = io.StringIO()
            code = 0
            with contextlib.redirect_stdout(out):
                try:
                    fg.main(["--scores", td, "--out", os.path.join(td, "out"), "--models", "S", "--settings", "chat", "formal",
                             "--sets", "cvtune", "wikitune", "dev302", "typing76", "user-reported", "fw", "discordtune",
                             "--break-weight", "10"])
                except SystemExit as e:
                    code = e.code
            text = out.getvalue()
            self.assertEqual(code, 0, text)                # wired context and every required cell: no STOP
            self.assertIn("by fixed - 10 x broken", text)
            self.assertEqual(len(re.findall(r"^\| S chat [\w-]+ \(vs shipped\) \|", text, re.M)), 7)
            self.assertNotIn("16-char context", text)
            self.assertIn("### S7e section 3, S", text)
            self.assertNotIn("missing: [(", text)
            for f in os.listdir(os.path.join(td, "out", "rowstats")):
                self.assertRegex(open(os.path.join(td, "out", "rowstats", f)).read(), r"\A[0-9\t\n]+\Z")

    def test_weighted_decision_rules(self):
        new = {"n": 4958, "fixed": 80, "broken": 5, "p": 1e-7}
        old = {"n": 4958, "fixed": 229, "broken": 163, "p": 1e-3}
        quiet = {"n": 10, "fixed": 1, "broken": 0, "p": 1.0}
        cells = {c: quiet for c in fg.REQUIRED_S7E}
        shipped = {c: quiet for c in fg.REQUIRED_S7E}
        cells[("chat", "discordtune")], shipped[("chat", "discordtune")] = new, old
        self.assertTrue(fg.decision_weighted(cells, shipped, 10)[-1][1])          # 80 - 50 = 30 > 0 and > 229 - 1630
        for edit in ({"broken": 8}, {"broken": 163}, {"fixed": 4, "broken": 5}):  # net <= 0; not fewer breaks; delta top1 < 0
            c = dict(cells); c[("chat", "discordtune")] = {**new, **edit}
            self.assertFalse(fg.decision_weighted(c, shipped, 10)[-1][1], edit)
        self.assertFalse(fg.decision_weighted(cells, shipped, 20)[-1][1])         # the weight is used: 80 - 100 < 0
        s = dict(shipped); s[("chat", "discordtune")] = {**old, "fixed": 200, "broken": 10}
        self.assertFalse(fg.decision_weighted(cells, s, 10)[-1][1])               # 30 > 0 but not above the shipped 200 - 100
        c = dict(cells); c[("formal", "fw")] = {"n": 186, "fixed": 1, "broken": 9, "p": 0.02}
        self.assertFalse(fg.decision_weighted(c, shipped, 10)[-1][1])            # a guard cell, fw included, blocks it
        c = dict(cells); c[("formal", "discordtune")] = {"n": 4958, "fixed": 1, "broken": 90, "p": 1e-9}
        self.assertTrue(fg.decision_weighted(c, shipped, 10)[-1][1])             # discordtune formal is not a guard cell
        for gone in fg.REQUIRED_S7E:
            c = {k: v for k, v in cells.items() if k != gone}
            self.assertFalse(fg.decision_weighted(c, shipped, 10)[-1][1], gone)

    def test_weighted_refuses_bad_weights_and_empty_tuning(self):
        with tempfile.TemporaryDirectory() as td:
            self.make_scores(td)
            args = ["--scores", td, "--out", os.path.join(td, "out"), "--models", "S", "--sets", "cvtune", "wikitune", "dev302"]
            for w in ("nan", "0", "-10"):
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as e:
                    fg.main(args + ["--break-weight", w])
                self.assertEqual(e.exception.code, 2, w)                          # argparse error, before anything runs
            flags = os.path.join(td, "flags")                 # the guard's real trigger: --tune-keep with every tuning row flagged
            os.makedirs(flags)
            for t in ("cvtune", "wikitune"):
                with open(os.path.join(flags, f"{t}.flags"), "w") as f:
                    f.write("1\n" * 120)                      # make_scores writes 120 rows per tuning set
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as e:
                fg.main(args + ["--break-weight", "10", "--tune-keep", flags])
            self.assertIn("no tuning rows left", str(e.exception.code))

    def test_decision_rules(self):
        base = {"n": 4958, "fixed": 120, "broken": 50, "p": 1e-7, "ci_lo": 30, "dcer": -0.2}
        quiet = {"n": 10, "fixed": 1, "broken": 0, "p": 1.0, "dcer": 0}
        cells = {c: quiet for c in fg.REQUIRED_CELLS}
        cells[("chat", "discordtune")] = base
        self.assertTrue(fg.decision(cells)[-1][1])
        for edit in ({"fixed": 98}, {"p": 0.06}, {"ci_lo": 0}, {"dcer": 0.1}):
            c = dict(cells); c[("chat", "discordtune")] = {**base, **edit}
            self.assertFalse(fg.decision(c)[-1][1], edit)
        c = dict(cells); c[("formal", "dev302")] = {"n": 302, "fixed": 2, "broken": 30, "p": 0.001, "dcer": 0.5}
        self.assertFalse(fg.decision(c)[-1][1])                                # a net-negative significant cell blocks it
        for gone in fg.REQUIRED_CELLS:                                         # a guard cell that was never loaded blocks it too
            c = {k: v for k, v in cells.items() if k != gone}
            self.assertFalse(fg.decision(c)[-1][1], gone)


if __name__ == "__main__":
    unittest.main()
