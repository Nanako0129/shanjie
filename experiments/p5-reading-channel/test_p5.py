import hashlib, json, math, os, sys, tempfile, unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import p5 as Q  # noqa: E402
import p4c  # noqa: E402

P = Q.P


def sc(ng, ll, ok, c=0):
    return {"ng": ng, "ll": ll, "ok": ok, "ed": [1 - o for o in ok], "glen": 4, "c": c}


def stat(f, b):
    return {"fixes": f, "breaks": b, "net": f - b, "p": 0.001, "error_ratio": b / f if f else 0.0}


def exits(test, code, fn, *args, **kw):
    with test.assertRaises(SystemExit) as cm:
        fn(*args, **kw)
    test.assertEqual(cm.exception.code, code)


class Estimators(unittest.TestCase):
    # word X: readings a, b tied at the top, c rare. word Z: p is the top reading, q and r sit at the -6.8 floor.
    LEX = {(("a",), "X"): -1.0, (("b",), "X"): -1.0, (("c",), "X"): -3.0,
           (("p",), "Z"): -1.0, (("q",), "Z"): -6.8, (("r",), "Z"): -6.8,
           (("a", "b"), "XX"): -0.5, (("c", "c"), "XX"): -0.5}
    # q is a reading only the (frequent) multi-character word ZW uses; r only a rare word.
    MCB = {(("q", "m"), "ZW"): -1.0, (("r", "m"), "ZV"): -6.0, (("a", "b"), "XX"): -0.5}

    def test_lex_tied_and_top_readings_are_zero_and_rare_is_negative(self):
        lp = Q.lex_logp(self.LEX)
        self.assertEqual(lp[(("a",), "X")], 0.0)
        self.assertEqual(lp[(("b",), "X")], 0.0)
        self.assertAlmostEqual(lp[(("c",), "X")], -2.0)
        self.assertEqual(lp[(("p",), "Z")], 0.0)

    def test_lex_has_single_character_words_only(self):
        self.assertEqual({w for _, w in Q.lex_logp(self.LEX)}, {"X", "Z"})
        self.assertEqual({w for _, w in Q.mc_logp(self.LEX, self.MCB)}, {"X", "Z"})

    def test_lex_one_reading_is_zero_whatever_its_score(self):
        self.assertEqual(Q.lex_logp({(("a",), "w"): -6.8})[(("a",), "w")], 0.0)

    def test_mc_tied_and_top_readings_are_zero(self):
        lp = Q.mc_logp(self.LEX, self.MCB)
        self.assertEqual(lp[(("a",), "X")], 0.0)          # XX is read a-b: both readings get the same mass, so they stay tied at the top
        self.assertEqual(lp[(("b",), "X")], 0.0)
        self.assertLess(lp[(("c",), "X")], -1.0)          # c has no multi-character support: stays negative

    def test_mc_multi_character_mass_lifts_a_floor_reading(self):
        lex, mc = Q.lex_logp(self.LEX), Q.mc_logp(self.LEX, self.MCB)
        self.assertLess(lex[(("q",), "Z")], -5.0)         # the floor value looks very rare ...
        self.assertAlmostEqual(mc[(("q",), "Z")], 0.0, places=4)     # ... but ZW, a frequent word, reads Z as q: it is the top reading
        self.assertAlmostEqual(mc[(("p",), "Z")], 0.0, places=4)     # p (0.1 of mass, as much as q) is within 1e-4 of the top
        self.assertLess(mc[(("r",), "Z")], -3.0)          # only the rare word ZV (1e-6) supports r: stays negative
        self.assertGreater(mc[(("r",), "Z")], lex[(("r",), "Z")])    # the rare word still lifts it off the bare floor

    def test_mc_counts_every_position_of_the_word(self):
        lex = {(("a",), "Z"): -9.0, (("b",), "Z"): -9.0}
        lp = Q.mc_logp(lex, {(("a", "b"), "ZZ"): -1.0})          # Z at both positions, read a then b: each reading gets 10^-1 once
        self.assertEqual(lp[(("a",), "Z")], lp[(("b",), "Z")])
        lp = Q.mc_logp(lex, {(("a", "a"), "ZZ"): -1.0})          # Z read a at both positions: a gets the mass twice, b only its own
        self.assertEqual(lp[(("a",), "Z")], 0.0)
        self.assertAlmostEqual(lp[(("b",), "Z")], math.log10(1e-9 / 0.2), places=6)

    def test_real_lexicon_floor_readings(self):
        import d23 as D
        lex = D.load_lex_scores()[0]
        lp, mc = Q.lex_logp(lex), Q.mc_logp(lex, Q.load_mcbpmf())
        k = (("ㄎㄨㄞˋ",), "會")
        self.assertEqual(lp[k], lex[k] - max(s for (r, w), s in lex.items() if w == "會" and len(r) == 1))
        self.assertLess(lp[k], -3.0)
        self.assertGreater(mc[k], lp[k])                  # mc lifts it (會計)
        self.assertEqual(max(v for (r, w), v in mc.items() if w == "會"), 0.0)
        self.assertEqual(max(v for (r, w), v in lp.items() if w == "會"), 0.0)


class Channel(unittest.TestCase):
    LP = {(("z",), "c"): -3.0, (("q",), "d"): 0.0}

    def test_multi_character_words_are_zero_not_missing(self):
        t = Q.word_terms(["x", "y", "z", "q"], ["ab", "c", "d"], self.LP)
        self.assertEqual(t, [0.0, -3.0, 0.0])
        self.assertEqual(Q.word_terms(["z", "x", "y", "q"], ["c", "ab", "d"], self.LP), [-3.0, 0.0, 0.0])    # the cut follows the words
        self.assertEqual(Q.word_terms(["x", "y"], ["ab"], {}), [0.0])                                          # no lexicon needed

    def test_missing_single_character_pair_is_none_and_counts_zero(self):
        self.assertEqual(Q.word_terms(["q", "z"], ["c", "d"], self.LP), [None, None])        # each looked up under its own syllable
        t = Q.word_terms(["x", "y", "unseen"], ["ab", "c"], self.LP)
        self.assertEqual(t, [0.0, None])
        self.assertEqual(Q.ch_of(t, 4), 0.0)

    def test_words_that_do_not_cover_the_syllables_are_uncut(self):
        self.assertIsNone(Q.word_terms(["x", "y"], ["ab", "c"], self.LP))
        self.assertIsNone(Q.word_terms(["x", "y"], None, self.LP))
        self.assertEqual(Q.ch_of(None, 4), 0.0)

    def test_the_clip_at_minus_c(self):
        self.assertEqual(Q.ch_of([-6.0], 2), -2.0)
        self.assertEqual(Q.ch_of([-6.0, -0.5, -3.0], 1), -2.5)        # -1, -0.5, -1
        self.assertEqual(Q.ch_of([-1.5], 2), -1.5)                    # above the clip: untouched
        self.assertEqual(Q.ch_of([-6.0], 4), -4.0)

    def test_row_terms_use_the_candidates_words_not_its_characters(self):
        rows = [{"syls": ["s1", "s2"], "cands": [("XY", 0, ["XY"]), ("XY", 0, ["X", "Y"])]}]
        lp = {(("s1",), "X"): -1.0, (("s2",), "Y"): -2.0}
        self.assertEqual(Q.row_terms(rows, lp), [[[0.0], [-1.0, -2.0]]])     # splitting the text would give [-1, -2] for the first too

    def test_counts_over_the_candidates_that_enter_the_pick(self):
        rows = [{"cands": [("abc", 0, ["ab", "c"]), ("xy", 0, ["x", "y"]), ("zz", 0, None), ("ww", 0, ["w", "w"])]},
                {"cands": [("q", 0, ["q"])]},
                {"cands": [("m", 0, ["m"]), ("n", 0, ["n"])]}]
        scores = [{"ng": [0] * 4, "ll": [0] * 3},                         # only 3 enter: min(8, len(ng), len(ll))
                  {"ng": [0]},                                             # no ll: skipped
                  {"ng": [0, 0], "ll": [0, 0]}]
        terms = [[[0.0, -2.5], [None, -5.0], None, [-9.0, -9.0]], [[-9.0]], [[-0.5], [0.0]]]
        n = Q.term_counts(rows, scores, terms)
        # row 0: abc = ab (0.0, multi: not a slot) + c (-2.5); xy = x missing + y -5; zz uncut. row 2: m -0.5, n 0.0
        self.assertEqual((n["candidates"], n["uncut_candidates"], n["single_char_slots"], n["missing_pairs"]), (5, 1, 5, 1))
        self.assertEqual(n["below_minus_c"], {"1": 2, "2": 2, "4": 1})   # -2.5 and -5 below -1 and -2; only -5 below -4


class Fusion(unittest.TestCase):
    def test_anchor_keeps_its_own_ll_and_others_move_by_the_difference(self):
        r = sc([0.0, -0.5, -0.9], [0.0, 1.0, 2.0], [0, 1, 0])
        ng, eff, alpha = Q.fused(r, [0.0] * 3, [-5.0, -3.0, -6.0], 0.5, False)
        self.assertEqual(alpha, 0.3)
        self.assertEqual(eff[0], 0.0)                                  # ch_0 subtracted: the anchor's own term is 0
        self.assertAlmostEqual(eff[1], 1.0 + (0.5 / 0.3) * 2.0)
        self.assertAlmostEqual(eff[2], 2.0 + (0.5 / 0.3) * -1.0)

    def test_alpha_is_03_off_and_05_on(self):
        r = sc([0.0, -0.5], [0.0, 0.0], [0, 1])
        self.assertEqual(Q.fused(r, [0.0, 0.0], [0.0, 0.0], 0.5, False)[2], 0.3)
        self.assertEqual(Q.fused(r, [0.0, 0.0], [0.0, 0.0], 0.5, True)[2], 0.5)
        # the channel gain is (nu / alpha) in ll, so nu * d in the fused score at either alpha (colloquial col 0: no other term)
        off = Q.fused(r, [0.0, 0.0], [0.0, 2.0], 0.5, False)
        on = Q.fused(r, [0.0, 0.0], [0.0, 2.0], 0.5, True)
        self.assertAlmostEqual(0.3 * off[1][1], 0.5 * 2.0)
        self.assertAlmostEqual(0.5 * on[1][1], 0.5 * 2.0)

    def test_the_channel_acts_through_the_margin(self):
        r = sc([0.0, -0.5], [0.0, 0.0], [0, 1])
        # fused gain nu * d: -0.5 + 0.5 * 2 = 0.5 >= delta 0.25 switches; d 1 gives 0 and does not
        self.assertEqual(Q.final_index(r, [0.0, 0.0], [0.0, 2.0], 0.5, False), 1)
        self.assertEqual(Q.final_index(r, [0.0, 0.0], [0.0, 1.0], 0.5, False), 0)
        self.assertEqual(Q.final_index(r, [0.0, 0.0], [0.0, -2.0], 0.5, False), 0)       # a worse reading never helps
        self.assertEqual(Q.final_index(r, [0.0, 0.0], [0.0, 2.0], 0.1, False), 0)       # nu 0.1: -0.5 + 0.2 = -0.3 < delta 0.25
        self.assertEqual(Q.final_index(sc([0.0, -2.0], [0.0, 0.0], [0, 1]), [0.0, 0.0], [0.0, 9.0], 1.0, False), 0)   # n-gram gap >= tau
        self.assertEqual(Q.final_index({"ng": [0.0, -0.1], "ok": [0, 1]}, [0.0, 0.0], [0.0, 9.0], 1.0, False), 0)    # no ll

    def test_nu_zero_skips_the_channel(self):
        rows = [sc([0.0, -0.4, -1.0], [0.0, 1.5, 3.0], [0, 1, 0]), sc([0.0, -0.1], [-21.0, -20.0], [0, 1]), sc([0.0, -0.5], [0.0, -0.2], [0, 1])]
        cols = [[0.0, 2.0, -1.0], [0.0, 4.0], [-4.0, 0.0]]
        ch = [[7.0, -3.0, 1.0], [-2.0, -9.0], [0.0, 5.0]]                                  # a channel that must not matter at nu 0
        for r, col, c in zip(rows, cols, ch):
            self.assertEqual(Q.final_index(r, col, c, 0.0, False), P.triage.pick(r["ng"], r["ll"], 0.3, P.TAU, P.DELTA))
            self.assertEqual(Q.final_index(r, col, c, 0.0, True), P.final_index(r, col, *Q.ON))

    def test_both_terms_add_before_the_pick(self):
        r = sc([0.0, -0.5], [0.0, 0.0], [0, 1])
        col, ch = [0.0, 1.0], [0.0, 1.0]
        # on: colloquial gate 1.0 at ll equal: g 0.5, gain 0.5 * (0.5 / 0.5) * 1 = 0.5 in ll; channel nu 0.5: 0.5 / 0.5 * 1 = 1.0 in ll
        ng, eff, alpha = Q.fused(r, col, ch, 0.5, True)
        self.assertAlmostEqual(eff[1], 0.5 + 1.0)
        self.assertEqual(Q.final_index(r, col, ch, 0.5, True), 1)                         # -0.5 + 0.5 * 1.5 = 0.25 >= delta


class Selection(unittest.TestCase):
    def test_largest_u2(self):
        self.assertEqual(Q.choose([("lex", 0.5, 2, stat(180, 80)), ("lex", 0.25, 2, stat(120, 40))])[:3], ("lex", 0.25, 2))   # u2 20 vs 40

    def test_tie_larger_net(self):
        a, b = stat(32, 11), stat(30, 10)                                   # u2 10 and 10, net 21 and 20
        self.assertEqual((P.u2(a), P.u2(b)), (10, 10))
        self.assertEqual(Q.choose([("lex", 0.1, 1, b), ("mc", 0.5, 2, a)])[:3], ("mc", 0.5, 2))
        self.assertEqual(Q.choose([("mc", 0.5, 2, a), ("lex", 0.1, 1, b)])[:3], ("mc", 0.5, 2))

    def test_later_ties_smaller_nu_larger_c_lex_first(self):
        same = stat(30, 10)
        self.assertEqual(Q.choose([("lex", 0.5, 2, same), ("lex", 0.25, 2, same)])[:3], ("lex", 0.25, 2))                  # smaller nu
        self.assertEqual(Q.choose([("lex", 0.25, 1, same), ("lex", 0.25, 4, same)])[:3], ("lex", 0.25, 4))                 # larger c
        self.assertEqual(Q.choose([("mc", 0.25, 2, same), ("lex", 0.25, 2, same)])[:3], ("lex", 0.25, 2))                  # lex before mc

    def test_u2_zero_or_less_never_qualifies(self):
        self.assertIsNone(Q.choose([("lex", 0.1, 1, stat(10, 6))]))
        self.assertIsNone(Q.choose([("lex", 0.1, 1, stat(10, 5))]))                                       # u2 == 0
        self.assertIsNotNone(Q.choose([("lex", 0.1, 1, stat(11, 5))]))

    def test_grid_and_cells(self):
        self.assertEqual(len(Q.GRID), 24)
        self.assertEqual(len(set(Q.GRID)), 24)
        self.assertEqual({e for e, _, _ in Q.GRID}, {"lex", "mc"})
        self.assertEqual(Q.parse_cell("mc,0.25,2"), ("mc", 0.25, 2))
        self.assertEqual(Q.parse_cell("lex,1.0,4"), ("lex", 1.0, 4))
        for bad in ("mc,0.2,2", "mc,0.25,3", "x,0.25,2", "0.25,2,on", "mc,0,1"):
            with self.assertRaises(SystemExit):
                Q.parse_cell(bad)
        self.assertEqual(Q.label(("mc", 0.25, 2)), "mc,0.25,2")


class Verdict(unittest.TestCase):
    def test_ratio_limit_is_06_not_05(self):
        s = stat(100, 55)                                                   # ratio 0.55: only the 0.6 rule passes it
        self.assertTrue(Q.verdict("3", s))
        self.assertFalse(p4c.clause_verdict("3", s))                        # p4c is the 0.5 version and stays that way
        self.assertTrue(Q.verdict("3", stat(100, 60)))                      # 0.6 exactly passes
        self.assertFalse(Q.verdict("3", stat(100, 61)))
        self.assertFalse(Q.verdict("3", stat(0, 0)))                        # net > 0 is required

    def test_clause_1(self):
        self.assertTrue(Q.verdict("1", stat(50, 30)))                       # breaks 30 > 26 but ratio 0.6
        self.assertFalse(Q.verdict("1", stat(50, 31)))                      # ratio 0.62
        self.assertFalse(Q.verdict("1", {**stat(10, 26), "p": 0.01}))       # significantly worse
        self.assertTrue(Q.verdict("1", {**stat(30, 26), "p": 0.01}))        # net >= 0 passes whatever p says
        self.assertTrue(Q.verdict("1", {**stat(10, 20), "p": 0.2}))         # not significant, breaks <= 26

    def test_clause_1_cap_26(self):
        self.assertTrue(Q.verdict("1", {**stat(40, 26), "p": 0.2}))         # breaks 26: the cap holds although the ratio 0.65 > 0.6
        self.assertFalse(Q.verdict("1", {**stat(40, 27), "p": 0.2}))        # breaks 27 > 26 and ratio 0.675 > 0.6

    def test_clause_2a(self):
        self.assertTrue(Q.verdict("2a", stat(0, 0)))
        self.assertTrue(Q.verdict("2a", stat(2, 2)))
        self.assertTrue(Q.verdict("2a", stat(3, 2)))                        # breaks 2 is allowed
        self.assertFalse(Q.verdict("2a", stat(4, 3)))                       # net +1 but breaks 3 > 2
        self.assertFalse(Q.verdict("2a", stat(1, 3)))
        self.assertFalse(Q.verdict("2a", stat(1, 2)))                       # net < 0


class Bindings(unittest.TestCase):
    def test_clause_is_bound_to_its_set(self):
        for clause, name in Q.CLAUSE_SET.items():
            Q.check_clause(clause, name)
            for other in Q.NAMES:
                if other != name:
                    exits(self, 1, Q.check_clause, clause, other)
        Q.check_clause("report", "dev302")

    def test_set_is_bound_to_its_row_count(self):
        self.assertEqual(Q.ROW_COUNTS, {"chatclause": 5761, "discordtune": 4958, "cvtune-clean": 3286, "typing76": 76, "user-reported": 154, "dev302": 302})
        for name, n in Q.ROW_COUNTS.items():
            Q.check_count(name, n)
            exits(self, 1, Q.check_count, name, n + 1)
            exits(self, 1, Q.check_count, name, n - 1)

    def test_controls_stop_when_the_p4d_counts_differ_from_the_anchors(self):
        for name, (f, b) in Q.P4D_ANCHORS.items():
            with mock.patch.object(P, "check_anchor"), mock.patch.object(P, "evaluate", return_value=({"fixes": f, "breaks": b}, [0])) as ev:
                self.assertEqual(Q.check_controls(name, [], [], []), ({"fixes": f, "breaks": b}, [0]))
                self.assertEqual(ev.call_args[0][3], Q.ON)                  # the control is the P4d cell, not shipping
            for fb in ((f + 1, b), (f, b + 1)):
                with mock.patch.object(P, "check_anchor"), mock.patch.object(P, "evaluate", return_value=({"fixes": fb[0], "breaks": fb[1]}, [0])):
                    exits(self, 1, Q.check_controls, name, [], [], [])

    def test_the_control_is_computed_with_the_colloquial_term_on(self):
        # unmocked evaluate: colloquial col 4 switches the P4d cell to candidate 1 (right), shipping stays on 0 (wrong): 1/0, not the typing76 anchor 0/0
        r = sc([0.0, -0.5], [0.0, 0.0], [0, 1], c=0)
        exits(self, 1, Q.check_controls, "typing76", [r], [[0.0, 4.0]], [False])
        # the same row with no colloquial difference: the control equals shipping, 0/0, and passes
        s, picks = Q.check_controls("typing76", [r], [[0.0, 0.0]], [False])
        self.assertEqual((s["fixes"], s["breaks"], picks), (0, 0, [0]))

    def test_controls_call_the_anchor_check(self):
        with mock.patch.object(P, "check_anchor", side_effect=SystemExit(1)), mock.patch.object(P, "evaluate"):
            exits(self, 1, Q.check_controls, "dev302", [], [], [])

    def test_rows_file_is_bound_to_its_sha256(self):
        self.assertEqual(set(Q.ROW_SHA256), set(Q.NAMES))
        for name, rel in (("typing76", "eval/bench/suite-v1/typing76.txt"), ("user-reported", "eval/dev/user-reported.txt"),
                          ("dev302", "eval/bench/suite-v1/dev302.txt")):
            Q.check_rows_sha(name, os.path.join(Q.ROOT, rel))                # the registered hashes are those of the public files
            exits(self, 1, Q.check_rows_sha, "chatclause" if name != "chatclause" else "dev302", os.path.join(Q.ROOT, rel))
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "rows.txt")
            with open(p, "wb") as f:
                f.write(b"x|y|z\n")
            exits(self, 1, Q.check_rows_sha, "typing76", p)
            with mock.patch.dict(Q.ROW_SHA256, {"typing76": hashlib.sha256(b"x|y|z\n").hexdigest()}):
                Q.check_rows_sha("typing76", p)

    def write(self, d, obj):
        p = os.path.join(d, "sel.json")
        with open(p, "w") as f:
            json.dump(obj, f)
        return p

    def test_selection_must_be_chatclause_with_a_chosen_cell(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(Q.read_selection(self.write(d, {"name": "chatclause", "chosen": "mc,0.25,2"})), "mc,0.25,2")
            exits(self, 1, Q.read_selection, self.write(d, {"name": "discordtune", "chosen": "mc,0.25,2"}))
            exits(self, 1, Q.read_selection, self.write(d, {"name": "chatclause", "chosen": None}))
            exits(self, 1, Q.read_selection, self.write(d, {"chosen": "mc,0.25,2"}))

    ARGS = ["--dump", "x", "--dump-words", "x", "--rowstats", "x", "--tinylm-scores", "x", "--t2", "x"]

    def test_cell_from_refuses_a_non_chatclause_json_before_loading(self):
        with tempfile.TemporaryDirectory() as d:
            sel, out = self.write(d, {"name": "discordtune", "chosen": "mc,0.25,2"}), os.path.join(d, "out.json")
            exits(self, 1, Q.main, self.ARGS + ["--rows", "r", "--out", out, "--name", "discordtune", "--cell-from", sel, "--clause", "1"])
            self.assertFalse(os.path.exists(out))

    def test_select_refuses_clause_and_outputs(self):
        with tempfile.TemporaryDirectory() as d:
            base = self.ARGS + ["--rows", "r", "--out", os.path.join(d, "o.json"), "--name", "chatclause", "--select"]
            for extra in (["--out-picks", os.path.join(d, "p.tsv")], ["--out-rowstats", os.path.join(d, "r.rs")], ["--clause", "1"], ["--clause", "2b"]):
                with self.assertRaises(SystemExit) as cm:
                    Q.main(base + extra)
                self.assertIn("--select", str(cm.exception.code))

    def test_cell_takes_only_the_report_clause(self):
        with tempfile.TemporaryDirectory() as d:
            for clause, name in (("1", "discordtune"), ("2a", "typing76"), ("2b", "cvtune-clean"), ("3", "user-reported")):
                with self.assertRaises(SystemExit) as cm:
                    Q.main(self.ARGS + ["--rows", "r", "--out", os.path.join(d, "o.json"), "--name", name, "--cell", "mc,0.25,2", "--clause", clause])
                self.assertIn("--cell is report only", str(cm.exception.code))

    def test_a_clause_on_the_wrong_set_stops_before_loading(self):
        with tempfile.TemporaryDirectory() as d:
            exits(self, 1, Q.main, self.ARGS + ["--rows", "r", "--out", os.path.join(d, "o.json"), "--name", "cvtune-clean",
                                                 "--cell-from", os.path.join(d, "none.json"), "--clause", "3"])

    def test_a_bad_cell_label_stops_before_loading(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "o.json")
            with self.assertRaises(SystemExit) as cm:
                Q.main(self.ARGS + ["--rows", "r", "--out", out, "--name", "dev302", "--cell", "mc,0.2,2"])   # "x" files would fail the load: this must come first
            self.assertIn("off-grid", str(cm.exception.code))
            sel = self.write(d, {"name": "chatclause", "chosen": "lex,0.25,3"})
            with self.assertRaises(SystemExit) as cm:
                Q.main(self.ARGS + ["--rows", "r", "--out", out, "--name", "dev302", "--cell-from", sel])
            self.assertIn("off-grid", str(cm.exception.code))
            self.assertFalse(os.path.exists(out))

    def test_out_is_checked_early_and_written_at_the_end_atomically(self):
        with tempfile.TemporaryDirectory() as d:
            sel = self.write(d, {"name": "chatclause", "chosen": "mc,0.25,2"})
            with self.assertRaises(SystemExit) as cm:
                Q.check_out(os.path.join(d, "no-such-dir", "o.json"), None)
            self.assertIn("writable", str(cm.exception.code))
            with self.assertRaises(SystemExit) as cm:
                Q.check_out(sel, sel)
            self.assertIn("--cell-from", str(cm.exception.code))
            Q.check_out(os.path.join(d, "o.json"), sel)
            # a STOP before the end (here: the registered rows hash) leaves an earlier good output as it was
            out = os.path.join(d, "good.json")
            with open(out, "w") as f:
                f.write("GOOD")
            rows = os.path.join(d, "rows.txt")
            with open(rows, "w") as f:
                f.write("not the registered rows\n")
            exits(self, 1, Q.main, self.ARGS + ["--rows", rows, "--out", out, "--name", "dev302", "--cell", "mc,0.25,2"])
            with open(out) as f:
                self.assertEqual(f.read(), "GOOD")
            self.assertEqual(sorted(os.listdir(d)), ["good.json", "rows.txt", "sel.json"])
            Q.write_atomic(out, "NEW")
            with open(out) as f:
                self.assertEqual(f.read(), "NEW")
            self.assertEqual(sorted(os.listdir(d)), ["good.json", "rows.txt", "sel.json"])      # no temp file left behind


class Wiring(unittest.TestCase):
    """Which baseline each number is taken against."""
    # two identical rows: candidate 1 is right, the colloquial column (4) makes the P4d cell (on) pick it, off keeps candidate 0
    ROWS = [sc([0.0, -0.5], [0.0, 0.0], [0, 1]), sc([0.0, -0.5], [0.0, 0.0], [0, 1])]
    COLS = [[0.0, 4.0], [0.0, 4.0]]
    CELL = ("lex", 0.1, 1)
    BY_C = {("lex", 1): [[0.0, 0.0], [0.0, 0.0]]}                      # channel 0: the cell is the P4d cell here
    P4D_OK, BASE_OK = [False, True], [True, True]                      # the cell's own ok is [True, True]

    def test_the_cell_is_judged_against_the_p4d_control_and_vs_ship_against_shipping(self):
        s, picks = Q.run_cell(self.ROWS, self.COLS, self.BY_C, self.P4D_OK, self.BASE_OK, self.CELL)
        self.assertEqual(picks, [1, 1])
        self.assertEqual((s["fixes"], s["breaks"]), (1, 0))                # against P4D_OK
        self.assertEqual((s["vs_ship"]["fixes"], s["vs_ship"]["breaks"]), (0, 0))   # against BASE_OK
        # swapped baselines give different numbers: the test would catch a swap
        t, _ = Q.run_cell(self.ROWS, self.COLS, self.BY_C, self.BASE_OK, self.P4D_OK, self.CELL)
        self.assertEqual((t["fixes"], t["breaks"]), (0, 0))
        self.assertEqual((t["vs_ship"]["fixes"], t["vs_ship"]["breaks"]), (1, 0))
        self.assertEqual(s["u2"], 1)

    def test_off_vs_ship_has_the_colloquial_term_off(self):
        off = Q.off_vs_ship(self.ROWS, self.COLS, self.BY_C, self.P4D_OK, self.BASE_OK, self.CELL)
        self.assertEqual((off["fixes"], off["breaks"], off["net"]), (0, 2, -2))     # off picks candidate 0 (wrong) where shipping was right
        on = Q.run_cell(self.ROWS, self.COLS, self.BY_C, self.P4D_OK, self.BASE_OK, self.CELL)[0]["vs_ship"]
        self.assertNotEqual((off["fixes"], off["breaks"]), (on["fixes"], on["breaks"]))

    def test_out_picks_has_the_p4d_pick_and_the_cell_pick(self):
        self.assertEqual(Q.picks_text([0, 1, 0], [1, 1, 2]), "1\t0\t1\n2\t1\t1\n3\t0\t2\n")

    def test_2b_baseline_is_the_p4d_control_not_shipping(self):
        scores = [{**sc([0.0, -0.5], [0.0, 0.0], [0, 1]), "ed": [3, 0]}, {**sc([0.0, -0.5], [0.0, 0.0], [1, 0]), "ed": [0, 2]}]
        p4d_picks, picks = [0, 1], [1, 1]
        self.assertEqual(Q.bootstrap_rows(scores, p4d_picks), [(0, 3, 4), (0, 2, 4)])
        with mock.patch.object(Q.evalstats, "paired_bootstrap", return_value=((-30, 5), (0.0, 0.0))) as pb:
            v, lo, pp = Q.clause_2b(scores, p4d_picks, picks)
        base, cand = pb.call_args[0]
        self.assertEqual(base, [(0, 3, 4), (0, 2, 4)])                     # the P4D control's picks
        self.assertEqual(cand, [(1, 0, 4), (0, 2, 4)])
        self.assertEqual(pb.call_args[1], {"level": 0.90})
        self.assertEqual((v, lo, pp), (False, -30, -1500.0))               # -30 rows of 2 is far below -1 pp
        with mock.patch.object(Q.evalstats, "paired_bootstrap", return_value=((0, 5), (0.0, 0.0))):
            self.assertTrue(Q.clause_2b(scores, p4d_picks, picks)[0])


class Probe(unittest.TestCase):
    ARGS = ["--dump", "x", "--dump-words", "x", "--rowstats", "x", "--tinylm-scores", "x", "--t2", "x", "--cell", "mc,0.25,2"]

    def test_refuses_paths_outside_eval_and_experiments(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "rows.txt")
            open(p, "w").close()
            exits(self, 1, Q.main, ["--probe", p] + self.ARGS)
        exits(self, 1, Q.main, ["--probe", os.path.join(Q.ROOT, "README.md")] + self.ARGS)
        exits(self, 1, Q.main, ["--probe", os.path.join(Q.ROOT, "experiments", "..", "README.md")] + self.ARGS)   # .. does not escape the check

    def test_refuses_untracked_files_even_under_experiments(self):
        with tempfile.NamedTemporaryFile(dir=HERE, suffix=".txt") as f:
            exits(self, 1, Q.public_path, f.name)

    def test_refuses_the_holdout(self):
        with mock.patch.object(Q.subprocess, "run") as run:                  # tracked or not, the holdout is refused before git is asked
            exits(self, 1, Q.public_path, os.path.join(Q.ROOT, "eval", "holdout", "news.txt"))
            run.assert_not_called()

    def test_allows_public_tracked_files(self):
        Q.public_path(os.path.join(HERE, "r140.txt"))
        Q.public_path(os.path.join(Q.ROOT, "eval", "dev", "user-reported.txt"))


if __name__ == "__main__":
    unittest.main()
