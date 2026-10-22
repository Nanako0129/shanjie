import json, os, shutil, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import r1d as D  # noqa: E402

R = D.R
SJTL = os.path.join(R.ROOT, "data", "lm", "tinylm.sjtl")
CLI = os.path.join(R.ROOT, "target", "release", "shanjie-eval")
PARAMS = (0.5, 2.0, 0.25)


def row(ng, ll, ok, col=None, c=0):
    """A synthetic loaded row: `ng` / `ok` of all candidates, `ll` for all of them (None entries allowed), `col` for chat."""
    k = min(len(ng), 8)
    sc = {"ng": ng[:k], "ok": ok[:k], "ed": [1 - o for o in ok[:k]], "glen": 4, "c": c}
    if ll is not None:
        sc["ll"] = ll
    r = {"sc": sc, "ok64": [bool(o) for o in ok], "ed64": [1 - o for o in ok], "ng64": ng}
    if col is not None:
        r["col"] = col
    return r


class Pick(unittest.TestCase):
    def test_rerank_off_is_the_first_of_b(self):
        r = row([0.0, -0.5, -1.0], None, [0, 1, 0], col=[0.0, 4.0, 0.0])
        self.assertEqual(D.r1d_pick((), False, r, 0.0)[0], 0)                 # mu 0: the dump's first
        self.assertEqual(D.r1d_pick((), False, r, 0.25)[0], 1)                # b = -0.5 + 0.25 * 4 = 0.5 > 0
        j, order, how = D.r1d_pick((), False, r, 1.0)
        self.assertEqual((j, order, how), (1, [1, 0, 2], "off"))

    def test_ties_keep_the_dump_rank_and_mu_zero_ignores_col(self):
        r = row([0.0, -1.0, -1.0, -1.0], None, [0] * 4, col=[0.0, 8.0, 2.0, 2.0])
        self.assertEqual(D.r1d_pick((), False, r, 0.0)[1], [0, 1, 2, 3])
        self.assertEqual(D.r1d_pick((), False, r, 0.25)[1], [1, 0, 2, 3])      # b = 0.0, 1.0, -0.5, -0.5 ... ties 2, 3 keep their order
        # col alone must not matter in formal (no col at all) and mu None
        self.assertEqual(D.r1d_pick((), False, row([0.0, -1.0], None, [0, 0]), None)[1], [0, 1])

    def test_rerank_on_fuses_the_first_eight_of_b_with_the_given_triple(self):
        r = row([0.0, -0.5, -1.0], [0.0, 3.0, 0.0], [0, 1, 0])
        self.assertEqual(D.r1d_pick(PARAMS, True, r, 0.0), (1, [0, 1, 2], "ok"))     # -0.5 + 1.5 - 0 >= 0.25
        self.assertEqual(D.r1d_pick((0.1, 2.0, 0.25), True, r, 0.0)[0], 0)           # alpha 0.1: -0.5 + 0.3 < 0.25
        self.assertEqual(D.r1d_pick((0.5, 0.4, 0.25), True, r, 0.0)[0], 0)           # tau: a margin of 0.5 >= 0.4 keeps the first

    def test_colloquial_term_reorders_the_64_before_the_fusion(self):
        # candidate 2 has the best col; with mu 1 it is the first of b, so it is the fusion's anchor and the others must beat it
        r = row([0.0, -0.5, -1.2], [0.0, 0.0, 0.0], [0, 0, 1], col=[0.0, 0.0, 3.0])
        j, order, how = D.r1d_pick(PARAMS, True, r, 1.0)
        self.assertEqual((order, j, how), ([2, 0, 1], 2, "ok"))
        self.assertEqual(D.r1d_pick(PARAMS, True, r, 0.0)[0], 0)

    def test_a_candidate_beyond_the_eighth_enters_when_b_puts_it_in_the_top_eight(self):
        n = 12
        ng = [-0.1 * i for i in range(n)]
        col = [0.0] * 10 + [9.0, 9.0]
        ll = [0.0] * n
        r = row(ng, ll, [0] * 11 + [1], col=col)
        j, order, how = D.r1d_pick(PARAMS, True, r, 1.0)
        self.assertEqual((order[:2], how), ([10, 11], "ok"))
        r["sc"]["ll"][10] = None                                              # a top-8 candidate without ll: the first of b decides
        self.assertEqual(D.r1d_pick(PARAMS, True, r, 1.0), (10, order, "nollcand"))
        r["sc"]["ll"][10] = 0.0
        r["sc"]["ll"][9] = None                                               # a null outside the top eight does not matter
        self.assertEqual(D.r1d_pick(PARAMS, True, r, 1.0)[2], "ok")
        del r["sc"]["ll"]
        self.assertEqual(D.r1d_pick(PARAMS, True, r, 1.0)[0::2], (10, "none"))

    def test_run_mu_counts_fixes_breaks_and_top1(self):
        rows = [row([0.0, -0.5], None, [0, 1], col=[0.0, 4.0]), row([0.0, -0.5], None, [1, 0], col=[0.0, 4.0])]
        s, picks = D.run_mu((), False, rows, [False, True], 0.25)
        self.assertEqual((picks, s["fixes"], s["breaks"], s["top1_before"], s["top1_after"], s["u2"]), ([1, 1], 1, 1, 1, 1, -1))
        self.assertEqual((s["b_first_fixes"], s["b_first_breaks"]), (1, 1))
        s, _ = D.run_mu(PARAMS, True, [row([0.0, -0.5], [0.0, 0.0], [0, 1])], [False], 0.0)       # rerank on: no b-first fields
        self.assertNotIn("b_first_fixes", s)

    def test_top8_counts_compare_sets_and_count_every_non_none_row(self):
        n = 10
        ng = [-0.1 * i for i in range(n)]
        # col swaps candidates 0 and 1 (both inside the top eight): the SET of the top eight is unchanged
        r = row(ng, None, [0] * n, col=[0.0, 1.0] + [0.0] * 8)
        s, _ = D.run_mu((), False, [r], [False], 1.0)
        self.assertEqual(s["top8_changed_rows"], 0)
        # candidate 9 enters the top eight; a "nollcand" row counts as needing the 9th, a "none" row does not
        r = row(ng, None, [0] * n, col=[0.0] * 9 + [9.0])
        s, _ = D.run_mu((), False, [r], [False], 1.0)
        self.assertEqual((s["top8_changed_rows"], s["top8_needs_beyond_8_rows"]), (1, 1))
        r = row(ng, [0.0] * n, [0] * n, col=[0.0] * 9 + [9.0])
        r["sc"]["ll"][9] = None
        s, _ = D.run_mu(PARAMS, True, [r], [False], 1.0)
        self.assertEqual((s["rows_top8_candidate_without_ll"], s["top8_needs_beyond_8_rows"]), (1, 1))
        del r["sc"]["ll"]
        s, _ = D.run_mu(PARAMS, True, [r], [False], 1.0)
        self.assertEqual((s["rows_without_ll"], s["top8_needs_beyond_8_rows"]), (1, 0))


class Anchors(unittest.TestCase):
    def test_anchor1_stops_on_a_pick_ok_or_order_mismatch(self):
        r = row([0.0, -0.5], [0.0, 3.0], [0, 1], c=1)
        D.anchor1(PARAMS, [r], [True])
        for rows, ok in (([row([0.0, -0.5], [0.0, 3.0], [0, 1], c=0)], [True]), ([r], [False]),
                         ([row([-0.1, 0.0], [0.0, 0.0], [0, 1], c=0)], [False])):     # the last: a dump not in n-gram order
            with self.assertRaises(SystemExit) as cm:
                D.anchor1(PARAMS, rows, ok)
            self.assertEqual(cm.exception.code, 1)

    def test_anchor2_is_dump_rank_zero(self):
        D.anchor2([row([0.0, -0.5], None, [0, 1])])
        with self.assertRaises(SystemExit):
            D.anchor2([row([-0.1, 0.0], None, [0, 1])])


class Tags(unittest.TestCase):
    KN = "+kn:cbefbced:θ1:β1"
    TL = "+tl:" + (R.sha_file(SJTL)[:8] if os.path.exists(SJTL) else "e7c179d8")
    SHA = TL[4:]

    def ok(self, tag, setting, rd):
        D.check_tag(tag, setting, self.SHA, rd)

    def bad(self, tag, setting, rd):
        with self.assertRaises(SystemExit):
            D.check_tag(tag, setting, self.SHA, rd)

    def test_baseline_tags(self):
        self.ok(f"lm-chat+ctx{self.KN}{self.TL}+acg+colloq:{R.COLLOQ8}+rc", "chat-std", False)
        self.ok(f"lm-chat+ctx{self.KN}{self.TL}+rl:conservative+acg+colloq:{R.COLLOQ8}+rc", "chat-cons", False)
        self.ok(f"lm-formal+ctx{self.TL}+acg+tri:{R.TRI8}", "formal-std", False)
        self.ok(f"lm-chat+ctx{self.KN}+acg", "chat-off", False)
        self.ok(f"lm-formal+ctx+acg+tri:{R.TRI8}", "formal-off", False)
        self.bad(f"lm-chat+ctx{self.KN}{self.TL}+acg+colloq:{R.COLLOQ8}", "chat-std", False)          # no +rc
        self.bad(f"lm-chat+ctx{self.KN}{self.TL}+acg", "chat-std", False)                              # no colloquial term in the shipping baseline
        self.bad(f"lm-chat+ctx{self.KN}{self.TL}+acg+colloq:{R.COLLOQ8}+rc", "chat-off", False)         # a rerank-off baseline has no small model
        self.bad(f"lm-formal+ctx{self.TL}+acg", "formal-std", False)                                    # no +tri
        self.bad(f"lm-chat+ctx{self.KN}{self.TL}+acg+colloq:{R.COLLOQ8}+rc+rd", "chat-std", False)      # a baseline must not be an rd run
        self.bad(f"lm-chat+ctx{self.TL}+acg+colloq:{R.COLLOQ8}+rc", "chat-std", False)                  # a --no-kn run: no +kn:
        self.bad("lm-chat+ctx+acg", "chat-off", False)

    def test_rd_run_tags(self):
        self.ok(f"lm-chat+ctx{self.KN}{self.TL}+acg+rd", "chat-std", True)
        self.ok(f"lm-chat+ctx{self.KN}{self.TL}+rl:aggressive+acg+rd", "chat-aggr", True)
        self.ok(f"lm-formal+ctx{self.TL}+acg+tri:{R.TRI8}+rd", "formal-std", True)
        self.bad(f"lm-chat+ctx{self.KN}{self.TL}+acg", "chat-std", True)                                 # no +rd
        self.bad(f"lm-chat+ctx{self.KN}{self.TL}+acg+colloq:{R.COLLOQ8}+rc+rd", "chat-std", True)        # the CLI's own colloquial term must be off
        self.bad(f"lm-chat+ctx{self.KN}{self.TL}+acg+rd+x", "chat-std", True)                            # +rd is the last tag
        self.bad(f"lm-formal+ctx{self.TL}+acg+rd", "formal-std", True)
        self.bad(f"lm-chat+ctx{self.TL}+acg+rd", "chat-std", True)                                       # no +kn:
        self.bad(f"lm-chat+ctx{self.KN}{self.TL}+rl:aggressive+acg+rd", "chat-std", True)                # the level of another run


class Choose(unittest.TestCase):
    def test_largest_u2_ties_the_smaller_mu(self):
        cell = lambda *u: {D.mu_label(m): {"u2": x} for m, x in zip(D.MUS, u)}
        self.assertEqual(D.choose(cell(0, 3, 5, 1)), 0.5)
        self.assertEqual(D.choose(cell(2, 5, 5, 5)), 0.25)
        self.assertEqual(D.choose(cell(-1, -3, -5, -1)), 0.0)         # negative everywhere: still the largest, ties the smaller
        self.assertEqual(D.choose(cell(-4, -3, -5, -1)), 1.0)

    def test_select_writes_the_chosen_mu(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        cells = {"setting": "chat-off", "name": "chatclause", "n": R.ROW_COUNTS["chatclause"], "anchor2": "ok",
                 "cells": {D.mu_label(m): {"u2": u} for m, u in zip(D.MUS, (1, 4, 4, 2))}}
        p = os.path.join(d, "c.json")
        with open(p, "w") as f:
            json.dump(cells, f)
        self.assertEqual(D.main(["select", p, "--out", os.path.join(d, "sel.json")]), 0)
        with open(os.path.join(d, "sel.json")) as f:
            self.assertEqual(json.load(f)["chosen"], "0.25")
        cells["setting"] = "chat-std"
        with open(p, "w") as f:
            json.dump(cells, f)
        with self.assertRaises(SystemExit):
            D.main(["select", p, "--out", os.path.join(d, "sel2.json")])


SEL = "sel.json"


def synth(setting, name, mu, verdict=True, base="1", cell="1", n=None):
    n = n or R.ROW_COUNTS[name]
    role = D.ROLES[setting][name]
    base = "0" if role == "3" else base          # clause 3 needs a net gain: every row a fix
    d = {"setting": setting, "name": name, "role": role, "n": n, "anchor1": "ok", "anchor2": "ok", "scores_sha256": f"{D.RUN_OF.get(setting, setting)}{name}", "selection": SEL if mu else None,
         "mu": mu, "base_ok": base * n, "cell_ok": cell * n}
    for k in ("fixes", "breaks", "net", "error_ratio", "b_first_fixes", "b_first_breaks", "top8_changed_rows", "top8_needs_beyond_8_rows",
              "rows_without_ll", "rows_top8_candidate_without_ll", "top1_before", "top1_after", "gold_in_64"):
        d[k] = 0
    if role in ("1", "2a", "2b"):
        d["verdict"] = verdict
    return d


class Final(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        self.sel = self.write(SEL, {"chosen": "0.5", "cells": {"0": 1, "0.25": 2, "0.5": 3, "1": 0}})

    def write(self, name, obj):
        p = os.path.join(self.d, name)
        with open(p, "w") as f:
            json.dump(obj, f)
        return p

    def inputs(self, mu="0.5", mutate=None):
        runs = []
        for s, n in D.JUDGED:
            d = synth(s, n, mu if D.SETTINGS[s][0] == "chat" else None)
            if mutate:
                mutate(d)
            runs.append(self.write(f"{s}.{n}.json", d))
        a3 = [self.write(f"a3-{s}.json", {"setting": s, "name": "cvtune-clean", "pass": True, "n64_sha256": f"{s}cvtune-clean"}) for s in ("chat-std", "formal-std")]
        return runs, a3

    def test_judged_table_is_eight_settings(self):
        self.assertEqual(len(D.SETTINGS), 8)
        self.assertEqual({s for s, _ in D.JUDGED}, set(D.SETTINGS))
        self.assertIn(("chat-off", "discordtune"), D.JUDGED)
        self.assertIn(("formal-off", "dev302"), D.JUDGED)
        self.assertNotIn(("chat-off", "chatclause"), D.JUDGED)
        self.assertEqual(D.ROLES["formal-aggr"], {"wikitune-clean": "2b", "typing76": "2a"})

    def test_all_pass_and_report(self):
        runs, a3 = self.inputs()
        out = os.path.join(self.d, "final.json")
        self.assertEqual(D.main(["final", self.sel, "--anchor3", *a3, *runs, "--out", out]), 0)
        with open(out) as f:
            res = json.load(f)
        self.assertTrue(res["all_pass"])
        self.assertEqual(sorted(res["clauses"]["3"]), ["user-reported chat-off + formal-off", "user-reported chat-std + formal-std"])
        self.assertEqual(sorted(res["clauses"]["1"]), ["chat-aggr/discordtune", "chat-cons/discordtune", "chat-off/discordtune", "chat-std/discordtune"])
        self.assertEqual(sorted(res["clauses"]["2b"]), ["chat-off/cvtune-clean", "chat-std/cvtune-clean", "formal-aggr/wikitune-clean", "formal-cons/wikitune-clean",
                                                        "formal-off/cvtune-clean", "formal-std/cvtune-clean"])
        self.assertIn("chat/discordtune", res["level_order"])

    def test_clause_three_pools_chat_and_formal_per_pair(self):
        # chat-std: 154 rows, base 0 -> cell 1 on all (154 fixes); formal-std: base 1 -> cell 0 on all (154 breaks): pooled net 0 -> fails clause 3
        def mutate(d):
            if d["name"] == "user-reported":
                d["base_ok"], d["cell_ok"] = (("0", "1") if d["setting"] == "chat-std" else ("1", "0")) if d["setting"].endswith("std") else ("0", "0")
                d["base_ok"], d["cell_ok"] = d["base_ok"] * d["n"], d["cell_ok"] * d["n"]
        runs, a3 = self.inputs(mutate=mutate)
        out = os.path.join(self.d, "final.json")
        self.assertEqual(D.main(["final", self.sel, "--anchor3", *a3, *runs, "--out", out]), 0)
        with open(out) as f:
            res = json.load(f)
        self.assertEqual(res["clauses"]["3"], {"user-reported chat-std + formal-std": False, "user-reported chat-off + formal-off": False})
        self.assertFalse(res["all_pass"])
        self.assertEqual((res["clause3_stats"]["user-reported chat-std + formal-std"]["fixes"], res["clause3_stats"]["user-reported chat-std + formal-std"]["breaks"]), (154, 154))

    def test_final_stops_on_a_wrong_mu_missing_run_or_anchor(self):
        runs, a3 = self.inputs()
        with self.assertRaises(SystemExit):
            D.main(["final", self.write("sel25.json", {"chosen": "0.25", "cells": {}}), "--anchor3", *a3, *runs])      # runs were made at mu 0.5
        with self.assertRaises(SystemExit):
            D.main(["final", self.sel, "--anchor3", *a3, *runs[1:]])                # a judged run missing
        with self.assertRaises(SystemExit):
            D.main(["final", self.write("sel3.json", {"chosen": "0.3", "cells": {}}), "--anchor3", *a3, *runs])      # off-grid mu
        bad_a3 = self.write("bad.json", {"setting": "chat-std", "name": "cvtune-clean", "pass": True, "n64_sha256": "other"})
        with self.assertRaises(SystemExit):
            D.main(["final", self.sel, "--anchor3", bad_a3, a3[1], *runs])
        runs2, a32 = self.inputs(mutate=lambda d: d.update(anchor1="n/a"))
        with self.assertRaises(SystemExit):
            D.main(["final", self.sel, "--anchor3", *a32, *runs2])

    def stops(self, *args):
        with self.assertRaises(SystemExit) as cm:
            D.main(["final", *args])
        self.assertEqual(cm.exception.code, 1)

    def test_final_takes_only_the_selection_json_and_runs_made_from_it(self):
        runs, a3 = self.inputs()
        self.stops("0.5", "--anchor3", *a3, *runs)                                       # a bare MU is refused
        self.stops(self.write("nochosen.json", {"cells": {}}), "--anchor3", *a3, *runs)  # not select's output
        self.stops(self.write("nocells.json", {"chosen": "0.5"}), "--anchor3", *a3, *runs)
        other = self.write("other.json", {"chosen": "0.5", "cells": {}})                 # chat runs say selection sel.json, not other.json
        self.stops(other, "--anchor3", *a3, *runs)
        runs2, a32 = self.inputs(mutate=lambda d: d.update(selection="fixed") if d["mu"] else None)    # --mu 0.5 by hand
        self.stops(self.sel, "--anchor3", *a32, *runs2)

    def test_an_off_run_must_share_the_scores_file_of_its_std_run(self):
        runs, a3 = self.inputs(mutate=lambda d: d.update(scores_sha256="other") if d["setting"] == "formal-off" and d["name"] == "dev302" else None)
        self.stops(self.sel, "--anchor3", *a3, *runs)

    def test_report_keeps_b_first_for_off_settings_only(self):
        runs, a3 = self.inputs()
        out = os.path.join(self.d, "final.json")
        D.main(["final", self.sel, "--anchor3", *a3, *runs, "--out", out])
        with open(out) as f:
            rep = json.load(f)["report_only"]
        self.assertIn("b_first_fixes", rep["chat-off/discordtune"])
        self.assertNotIn("b_first_fixes", rep["chat-std/discordtune"])

    def test_level_order_covers_the_formal_main_set(self):
        runs, a3 = self.inputs()
        extra = self.write("fstd.json", synth("formal-std", "wikitune-clean", None))   # report-only select-role run
        out = os.path.join(self.d, "final.json")
        D.main(["final", self.sel, "--anchor3", *a3, *runs, extra, "--out", out])
        with open(out) as f:
            self.assertIn("formal/wikitune-clean", json.load(f)["level_order"])


def cli_has_reading_decode():
    """An older binary stops on `--reading-decode` itself ("unknown argument"); a current one gets as far as the missing --profile."""
    try:
        r = subprocess.run([CLI, "--lm", "/nonexistent", "--reading-decode"], capture_output=True, text=True, cwd=R.ROOT)
    except OSError:
        return False
    return "unknown argument" not in r.stderr


@unittest.skipUnless(os.path.exists(CLI) and os.path.exists(SJTL) and os.path.exists(os.path.join(R.ROOT, "data", "lm", "colloquial.sjlm")),
                     "target/release/shanjie-eval and data/lm (build the CLI first)")
@unittest.skipUnless(cli_has_reading_decode(), "target/release/shanjie-eval is older than --reading-decode (rebuild it)")
class EndToEndOnTyping76(unittest.TestCase):
    """The CLI's real rd run and shipping baseline on typing76 (public): the anchors hold for the six rerank-on settings' std runs and the -off
    settings read the same files; mu 0 and a formal run reproduce the CLI's own pick."""

    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp()
        cls.addClassCleanup(shutil.rmtree, cls.d, True)
        cls.rows = os.path.join(R.ROOT, "eval", "bench", "suite-v1", "typing76.txt")

    def cli(self, prof, tag, extra):
        a = [CLI, "--lm", "data/lm/bigram.sjlm", "--profile", prof, "--context", "--packs", "acg", "--rows", self.rows, *extra]
        out = subprocess.run(a, cwd=R.ROOT, capture_output=True, text=True, check=True).stdout
        p = os.path.join(self.d, tag)
        with open(p, "w") as f:
            f.write(out)
        return p

    def test_anchors_hold_on_a_real_run(self):
        f = lambda n: os.path.join(self.d, n)
        for prof in ("chat", "formal"):
            t = prof
            summary = self.cli(prof, f"{t}.sum", ["--tinylm", SJTL, "--reading-decode", "--dump", f(f"{t}.d"), "--dump-words", f(f"{t}.w"), "--rowstats", f(f"{t}.rs"),
                                                  "--tinylm-scores", f(f"{t}.sc"), "--tinylm-scores-topk", "64"])
            base = self.cli(prof, f"{t}.bsum", ["--tinylm", SJTL, "--rowstats", f(f"{t}.brs")] + (["--colloquial-core", "data/lm/colloquial.sjlm"] if prof == "chat" else []))
            off = self.cli(prof, f"{t}.bsum-off", ["--rowstats", f(f"{t}.brs-off")])
            for setting, bs, brs in ((f"{prof}-std", base, f(f"{t}.brs")), (f"{prof}-off", off, f(f"{t}.brs-off"))):
                out = f(f"{setting}.json")
                args = ["run", "--setting", setting, "--name", "typing76", "--rows", self.rows, "--dump", f(f"{t}.d"), "--dump-words", f(f"{t}.w"), "--rowstats", f(f"{t}.rs"),
                        "--tinylm-scores", f(f"{t}.sc"), "--summary", summary, "--base-rowstats", brs, "--base-summary", bs, "--sjtl", SJTL, "--out", out]
                if prof == "chat":
                    args += ["--t2", os.path.join(R.ROOT, "data", "lm", "colloquial.sjlm"), "--mu", "0.25"]
                self.assertEqual(D.main(args), 0)
                with open(out) as fh:
                    res = json.load(fh)
                self.assertEqual((res["anchor1"], res["anchor2"], res["n"], len(res["pick_idx"])), ("ok", "ok", 76, 76))
                self.assertEqual(res["top1_before"], sum(map(int, res["base_ok"])))
        # a baseline of another setting is refused (the tag check)
        with self.assertRaises(SystemExit):
            D.main(["run", "--setting", "formal-std", "--name", "typing76", "--rows", self.rows, "--dump", f("formal.d"), "--dump-words", f("formal.w"), "--rowstats", f("formal.rs"),
                    "--tinylm-scores", f("formal.sc"), "--summary", f("formal.sum"), "--base-rowstats", f("formal.brs-off"), "--base-summary", f("formal.bsum-off"),
                    "--sjtl", SJTL, "--out", f("x.json")])


if __name__ == "__main__":
    unittest.main()
