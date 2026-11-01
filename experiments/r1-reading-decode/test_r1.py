import importlib.util, json, os, re, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import r1 as R  # noqa: E402

SJTL = os.path.join(R.ROOT, "data", "lm", "tinylm.sjtl")


def row(ng, ll, ok, ch=None, col=None, c=0):
    """A synthetic loaded row: `ng` / `ok` for all candidates, `ll` for the first len(ll)."""
    ch = ch or [0.0] * len(ng)
    k = min(len(ng), 8)
    sc = {"ng": ng[:k], "ok": ok[:k], "ed": [1 - o for o in ok[:k]], "glen": 4, "c": c}
    if ll is not None:
        sc["ll"] = ll
    r = {"sc": sc, "ok64": [bool(o) for o in ok], "ed64": [1 - o for o in ok], "ng64": ng, "ch": {1: ch, 2: ch, 4: ch}}
    r["ch_r1"] = r["ch"]
    if col is not None:
        r["col"] = col
    return r


PLAIN = {"kind": "plain", "plain": (0.5, 2.0, 0.25)}


class Header(unittest.TestCase):
    @unittest.skipUnless(os.path.exists(SJTL), "data/lm/tinylm.sjtl")
    @unittest.skipUnless(importlib.util.find_spec("numpy"), "numpy is not installed (export_sjtl, the reader this test compares with, needs it)")
    def test_levels_come_from_the_file(self):
        import triage
        std = triage.tinylm_params(SJTL)            # the exporter's reader: tensors and all
        for p in ("chat", "formal"):
            self.assertEqual(R.header_params(SJTL, p, "standard"), std[p])
        trip = {(p, l): R.header_params(SJTL, p, l) for p in ("chat", "formal") for l in R.LEVELS}
        self.assertTrue(all(len(t) == 3 and all(x == x for x in t) for t in trip.values()))
        self.assertNotEqual(trip["chat", "conservative"], trip["chat", "standard"], "the level triples are read, not the standard one")


class Pick(unittest.TestCase):
    def test_lambda_zero_is_the_ngram_order_and_the_plain_pick(self):
        r = row([0.0, -0.5, -1.0], [0.0, 3.0, 0.0], [0, 1, 0])
        self.assertEqual(R.ship_picks(PLAIN, [r]), [1])                 # -0.5 + 1.5 - 0 >= 0.25
        self.assertEqual(R.r1_pick(PLAIN, r, 0.0, 4)[0], 1)

    def test_the_reading_term_reorders_before_the_fusion(self):
        # candidate 2 has the best ch; with lambda 1 it becomes the first of s, so the fusion's anchor is candidate 2
        r = row([0.0, -0.5, -1.2], [0.0, 0.0, 0.0], [0, 0, 1], ch=[-4.0, -4.0, 0.0])
        # lambda 0.25: s = -1, -1.5, -1.2
        j, order, how = R.r1_pick(PLAIN, r, 0.25, 4)
        self.assertEqual((order, how), ([0, 2, 1], "ok"))
        j, order, _ = R.r1_pick(PLAIN, r, 1.0, 4)
        self.assertEqual((order, j), ([2, 0, 1], 2))

    def test_candidate_beyond_the_eighth_enters_when_it_has_ll(self):
        n = 12
        ng = [-0.1 * i for i in range(n)]
        ch = [-4.0] * 10 + [0.0, 0.0]
        r = row(ng, [0.0] * n, [0] * 11 + [1], ch=ch)
        j, order, how = R.r1_pick(PLAIN, r, 2.0, 4)
        self.assertEqual((order[:2], how), ([10, 11], "ok"))
        r["sc"]["ll"][10] = None                                           # a top-8 candidate without ll (null): s first decides
        self.assertEqual(R.r1_pick(PLAIN, r, 2.0, 4), (10, order, "nollcand"))
        r["sc"]["ll"][10] = 0.0
        r["sc"]["ll"][9] = None                                            # a null outside the new top 8 does not matter
        self.assertEqual(R.r1_pick(PLAIN, r, 2.0, 4)[2], "ok")
        del r["sc"]["ll"]
        self.assertEqual(R.r1_pick(PLAIN, r, 2.0, 4)[0::2], (10, "none"))

    def test_clip_c_limits_the_term(self):
        r = row([0.0, -1.0], [0.0, 0.0], [0, 1], ch=[-5.0, 0.0])
        r["ch"] = r["ch_r1"] = {1: [-1.0, 0.0], 2: [-2.0, 0.0], 4: [-4.0, 0.0]}
        self.assertEqual(R.r1_pick(PLAIN, r, 0.5, 1)[1], [0, 1])           # -0.5 vs -1
        self.assertEqual(R.r1_pick(PLAIN, r, 1.0, 4)[1], [1, 0])           # -4 vs -1

    def test_colloquial_cell_uses_the_new_first_as_the_anchor_and_drops_p5_with_nu_zero(self):
        # equal ll: the gate is sigmoid(0) = 0.5, so candidate 1 gains 0.5 * (0.5 / 0.5) * 6 = 3 in ll_eff, fused -0.5 + 0.5 * 3 = 1.0
        # synthetic cells, not the shipping ones: this tests the fusion arithmetic, whichever cells core/src/colloquial.rs holds (R.COLLOQ_CELLS)
        cfg = {"kind": "colloq", "nu": 0.0, "plain": (0.3, 2.0, 0.25), "cell": (0.5, 2.0, 0.25)}
        r = row([0.0, -0.5], [0.0, 0.0], [0, 1], col=[0.0, 6.0])
        self.assertEqual(R.r1_pick(cfg, r, 0.5, 4)[0], 1)
        # conservative (1.0, 2, 3.0): mu / alpha with the level's alpha, 0.5 * (0.5 / 1.0) * 6 = 1.5, fused -0.5 + 1.0 * 1.5 = 1.0 < delta 3
        cons = {**cfg, "cell": (1.0, 2.0, 3.0)}
        self.assertEqual(R.r1_pick(cons, r, 0.5, 4)[0], 0)
        self.assertEqual(R.r1_pick(cons, row([0.0, -0.5], [0.0, 0.0], [0, 1], col=[0.0, 16.0]), 0.5, 4)[0], 1)   # 4 - 0.5 = 3.5 >= 3
        # alpha cancels in alpha * (mu / alpha): with standard's alpha 0.5 in mu / alpha, col 10 would fuse 0.5 * 1.0 * 10 - 0.5 = 4.5 >= 3
        self.assertEqual(R.r1_pick(cons, row([0.0, -0.5], [0.0, 0.0], [0, 1], col=[0.0, 10.0]), 0.5, 4)[0], 0)   # 2.5 - 0.5 = 2.0 < 3
        r2 = row([0.0, -0.5], [0.0, 0.0], [0, 1], col=[0.0, 0.0], ch=[0.0, -4.0])
        self.assertEqual(R.r1_pick(cfg, r2, 0.0, 4)[0], 0)
        ship = {**cfg, "nu": 1.0}                                          # P5 term: only the shipping cell has it
        r3 = row([0.0, -0.5], [0.0, 0.0], [0, 1], col=[0.0, 0.0], ch=[-4.0, 0.0])
        self.assertEqual(R.ship_picks(ship, [r3]), [1])
        self.assertEqual(R.ship_picks(cfg, [r3]), [0])
        bad = row([0.0, -0.5], [0.0, 0.0], [0, 1], col=[0.0, float("inf")])  # non-finite col: the plain triple, no terms
        self.assertEqual(R.ship_picks(ship, [bad]), [0])

    def test_colloquial_cells_are_core_colloquial_rs(self):
        """COLLOQ_CELLS is read from core/src/colloquial.rs, so this checks the reading against an independent one: the `cell(level)` match arms
        name the three constants for the three levels, and each constant's literal triple is the cell."""
        with open(os.path.join(R.ROOT, "core", "src", "colloquial.rs"), encoding="utf-8") as f:
            src = f.read()
        arms = dict(re.findall(r"RerankLevel::(\w+) => (\w+),", src[src.index("pub fn cell("):]))
        self.assertEqual(arms, {"Conservative": "CONSERVATIVE", "Standard": "STANDARD", "Aggressive": "AGGRESSIVE"})
        for level, name in (("standard", "STANDARD"), ("conservative", "CONSERVATIVE"), ("aggressive", "AGGRESSIVE")):
            m = re.search(rf"pub const {name}: \(f64, f64, f64\) = \(([^)]*)\);", src)
            self.assertEqual(tuple(float(x) for x in m.group(1).split(",")), R.COLLOQ_CELLS[level], name)
        self.assertEqual(len(set(R.COLLOQ_CELLS.values())), 3)

    def test_rows_without_ll_keep_the_first(self):
        self.assertEqual(R.ship_picks(PLAIN, [row([0.0, -0.1], None, [0, 1])]), [0])


class Anchors(unittest.TestCase):
    def test_anchor1_stops_on_a_pick_or_ok_mismatch(self):
        r = row([0.0, -0.5], [0.0, 3.0], [0, 1], c=1)
        self.assertEqual(R.anchor1(PLAIN, [r], [True]), [1])
        for rows, ok in (([row([0.0, -0.5], [0.0, 3.0], [0, 1], c=0)], [True]), ([r], [False])):
            with self.assertRaises(SystemExit) as cm:
                R.anchor1(PLAIN, rows, ok)
            self.assertEqual(cm.exception.code, 1)

    def test_lambda0_check_stops_when_the_dump_is_not_in_ngram_order(self):
        r = row([-0.1, 0.0], [0.0, 0.0], [0, 1])       # second candidate has the higher ng: the shipping cell keeps 0, the sorted one picks 1
        with self.assertRaises(SystemExit):
            R.check_lambda0(PLAIN, [r], R.ship_picks(PLAIN, [r]))

    def test_anchor3_compares_the_first_eight_ll_as_text(self):
        d = tempfile.mkdtemp()
        l8 = '{"i": 1, "n": 9, "ng": [1.0], "c": 0, "ll": [%s]}\n' % ", ".join(f"-{k}.5" for k in range(8))
        l64 = l8.replace("-7.5]", "-7.5, -9.0, -1e-05]")
        for name, text in (("a", l64 + '{"i": 2, "c": 0}\n'), ("b", l8 + '{"i": 2, "c": 0}\n'), ("c", l8.replace("-3.5", "-3.50") + '{"i": 2, "c": 0}\n')):
            with open(os.path.join(d, name), "w") as f:
                f.write(text)
        p = lambda n: os.path.join(d, n)
        res = R.anchor3(type("A", (), {"n64": p("a"), "n8": p("b"), "setting": "chat-std"}))
        self.assertEqual((res["pass"], res["rows_with_more_than_8_ll"], len(res["n64_sha256"])), (True, 1, 64))
        bad = type("A", (), {"n64": p("a"), "n8": p("c"), "setting": "chat-std", "out": p("res.json")})
        with self.assertRaises(SystemExit):
            R.anchor3(bad)
        R.main(["anchor3", "--setting", "chat-std", "--n64", p("a"), "--n8", p("b"), "--out", p("ok.json")])
        with open(p("ok.json")) as f:
            self.assertTrue(json.load(f)["pass"])
        with self.assertRaises(SystemExit):
            R.main(["anchor3", "--setting", "chat-std", "--n64", p("a"), "--n8", p("c"), "--out", p("bad.json")])
        with open(p("bad.json")) as f:
            self.assertFalse(json.load(f)["pass"])


class Tags(unittest.TestCase):
    CHAT = f"lm-chat+ctx+kn:abcd1234:θ1:β0.5+tl:e7c179d8+acg+colloq:{R.COLLOQ8}"
    FORMAL = f"lm-formal+ctx+tl:e7c179d8+acg+tri:{R.TRI8}"

    def bad(self, tag, setting, rc=False):
        with self.assertRaises(SystemExit):
            R.check_tag(tag, setting, "e7c179d8", rc)

    def test_each_setting_has_its_tag(self):
        R.check_tag(self.CHAT + "+rc", "chat-std", "e7c179d8", True)
        R.check_tag(self.CHAT, "chat-std", "e7c179d8", False)                # the --no-reading-channel twin
        R.check_tag(self.CHAT.replace("+acg", "+rl:conservative+acg") + "+rc", "chat-cons", "e7c179d8", True)    # PR #271: +rc at every level
        R.check_tag(self.CHAT.replace("+acg", "+rl:conservative+acg"), "chat-cons", "e7c179d8", False)            # its --no-reading-channel twin
        R.check_tag(self.FORMAL.replace("+acg", "+rl:aggressive+acg"), "formal-aggr", "e7c179d8", False)
        R.check_tag(self.FORMAL, "formal-std", "e7c179d8", False)

    def test_wrong_tags_stop(self):
        self.bad(self.CHAT, "chat-std", True)                                # no +rc where shipping
        self.bad(self.CHAT + "+rc", "chat-std", False)
        self.bad(self.CHAT + "+rc", "chat-cons")                             # +rc and no +rl:
        self.bad(self.CHAT, "chat-aggr")                                     # no +rl:aggressive
        self.bad(self.CHAT.replace("+acg", "+rl:aggressive+acg"), "chat-aggr", True)   # shipping chat-aggr without +rc
        self.bad(self.FORMAL.replace(f"+tri:{R.TRI8}", ""), "formal-std")    # no +tri
        self.bad(self.FORMAL + f"+colloq:{R.COLLOQ8}", "formal-std")
        self.bad(self.CHAT.replace("+ctx", ""), "chat-cons")
        self.bad(self.CHAT.replace("e7c179d8", "00000000") + "+rc", "chat-std", True)


class Selection(unittest.TestCase):
    @staticmethod
    def cells(u2s):
        return {R.label(g): {"u2": u2s.get(R.label(g), -1)} for g in R.GRID}

    def test_largest_sum_then_min_then_small_lambda_then_large_c(self):
        ca = self.cells({"0.5,2": 10, "1.0,4": 8, "0.25,1": 4, "0.25,2": 4})
        cw = self.cells({"0.5,2": 2, "1.0,4": 4, "0.25,1": 8, "0.25,2": 8})
        # sums 12, 12, 12, 12; min 2, 4, 4, 4; then lambda 0.25 before 1.0; then c 2 before 1
        self.assertEqual(R.choose(ca, cw), (0.25, 2))

    def test_both_u2_must_be_positive(self):
        self.assertIsNone(R.choose(self.cells({"0.5,2": 9}), self.cells({"0.25,1": 9, "0.5,2": 0})))

    def test_run_cell_counts(self):
        rows = [row([0.0, -0.1], [0.0, 0.0], [1, 0], ch=[-4.0, 0.0]), row([0.0, -0.1], [0.0, 0.0], [0, 1], ch=[0.0, -4.0])]
        s, picks = R.run_cell(PLAIN, rows, [True, False], (2.0, 4))
        # row 1: s = -8 vs -0.1 -> candidate 1 (wrong), a break; row 2: s = 0 vs -8.1 stays, candidate 0 wrong (no change)
        self.assertEqual((picks, s["fixes"], s["breaks"], s["u2"]), ([1, 0], 0, 1, -2))
        self.assertEqual((s["ngram_fixes"], s["ngram_breaks"], s["top8_changed_rows"]), (0, 1, 0))


class Final(unittest.TestCase):
    def judged(self, over=None):
        over = over or {}
        d = tempfile.mkdtemp()
        paths = []
        for s, n in R.JUDGED:
            role = R.ROLES[s][n]
            doc = {"setting": s, "name": n, "role": role, "n": R.ROW_COUNTS[n], "anchor1": "ok", "cell": "0.5,2", "fixes": 3, "breaks": 1,
                   "net": 2, "error_ratio": 0.33, "ngram_fixes": 1, "ngram_breaks": 0, "top8_changed_rows": 2, "top8_needs_beyond_8_rows": 1,
                   "rows_without_ll": 0, "rows_top8_candidate_without_ll": 0, "gold_in_64": 10}
            if (s, n) in R.ANCHOR2:
                doc["anchor2"] = "ok"
            if role in ("1", "2a", "2b"):
                doc["verdict"] = True
            if role == "3":
                doc.update({"base_ok": "1" * 150 + "0" * 4, "cell_ok": "1" * 152 + "0" * 2, "gold_deducted": [[3, 1, -2.0]]})
            if n == "cvtune-clean":
                doc["scores_sha256"] = "sha-" + s
            doc.update(over.get((s, n), {}))
            paths.append(os.path.join(d, f"{s}-{n}.json"))
            with open(paths[-1], "w") as f:
                json.dump(doc, f)
        self.a3 = []
        for s in ("chat-std", "formal-std"):
            self.a3.append(os.path.join(d, f"a3-{s}.json"))
            with open(self.a3[-1], "w") as f:
                json.dump({"setting": s, "name": "cvtune-clean", "pass": True, "n64_sha256": "sha-" + s, "n8_sha256": "x"}, f)
        sel = os.path.join(d, "sel.json")
        with open(sel, "w") as f:
            json.dump({"chosen": "0.5,2"}, f)
        return sel, paths, os.path.join(d, "out.json")

    def run_final(self, over=None, a3=None):
        sel, paths, out = self.judged(over)
        R.main(["final", sel, *paths, "--out", out, "--anchor3", *(a3 or self.a3)])
        with open(out) as f:
            return json.load(f)

    def test_all_pass_and_pooled_clause3(self):
        res = self.run_final()
        self.assertTrue(res["all_pass"])
        self.assertEqual((res["clause3_stats"]["n"], res["clause3_stats"]["fixes"], res["clause3_stats"]["breaks"]), (308, 4, 0))

    def test_one_failed_clause_is_not_all_pass(self):
        self.assertFalse(self.run_final({("formal-cons", "wikitune-clean"): {"verdict": False}})["all_pass"])

    def test_final_needs_a_passing_anchor3_of_the_judged_scores_files(self):
        sel, paths, _ = self.judged()
        with open(self.a3[0]) as f:
            doc = json.load(f)
        for change in ({"pass": False}, {"n64_sha256": "another file"}):
            with open(self.a3[0], "w") as f:
                json.dump({**doc, **change}, f)
            with self.assertRaises(SystemExit):
                R.main(["final", sel, *paths, "--anchor3", *self.a3])
        with self.assertRaises(SystemExit):
            R.main(["final", sel, *paths, "--anchor3", self.a3[1], self.a3[1]])
        with self.assertRaises(SystemExit):
            R.main(["final", sel, *paths])

    def test_missing_wrong_cell_or_unchecked_anchor2_stop(self):
        sel, paths, _ = self.judged()
        with self.assertRaises(SystemExit):
            R.main(["final", sel, *paths[1:], "--anchor3", *self.a3])
        with self.assertRaises(SystemExit):
            self.run_final({("chat-std", "discordtune"): {"cell": "1.0,4"}})
        with self.assertRaises(SystemExit):
            self.run_final({("chat-std", "discordtune"): {"anchor2": "no"}})


class Sandhi(unittest.TestCase):
    """R1b arm B: the sandhi readings of 一 and 不 cost 0 in the R1 term only; every other pair, and the input, are unchanged."""

    def test_exempt_only_the_three_sandhi_pairs(self):
        logp = {(("ㄧˊ",), "一"): -0.69, (("ㄧˋ",), "一"): -1.39, (("ㄧ",), "一"): 0.0, (("ㄅㄨˊ",), "不"): -0.5,
                (("ㄓㄤˇ",), "長"): -3.75}
        out = R.sandhi_exempt(logp)
        self.assertEqual([out[k] for k in R.SANDHI], [0.0, 0.0, 0.0])
        self.assertEqual(out[(("ㄓㄤˇ",), "長")], -3.75)
        self.assertEqual(logp[(("ㄧˋ",), "一")], -1.39, "the input is not modified")

    def test_missing_pair_is_not_added(self):
        self.assertNotIn((("ㄅㄨˊ",), "不"), R.sandhi_exempt({(("ㄧˋ",), "一"): -1.0}))


class FloorMc(unittest.TestCase):
    """R1c: only single-character pairs at the lexicon floor take mc."""

    def test_floor_pairs_only(self):
        lex = {(("ㄓㄤˇ",), "長"): -6.8, (("ㄔㄤˊ",), "長"): -3.05, (("ㄗㄚ",), "嘁"): -6.8, (("ㄓㄤˇ", "ㄉㄚˋ"), "長大"): -6.8}
        logp = {(("ㄓㄤˇ",), "長"): -3.75, (("ㄔㄤˊ",), "長"): 0.0, (("ㄗㄚ",), "嘁"): 0.0}
        mc = {(("ㄓㄤˇ",), "長"): -0.1, (("ㄔㄤˊ",), "長"): -0.5, (("ㄗㄚ",), "嘁"): -0.98}
        out = R.floor_mc(logp, lex, mc)
        # every floor pair takes mc, also one whose lex value is 0 (all its readings at the floor)
        self.assertEqual(out, {(("ㄓㄤˇ",), "長"): -0.1, (("ㄔㄤˊ",), "長"): 0.0, (("ㄗㄚ",), "嘁"): -0.98})
        self.assertEqual(logp[(("ㄓㄤˇ",), "長")], -3.75, "the input is not modified")


class SandhiWiring(unittest.TestCase):
    def test_r1_pick_reads_ch_r1_not_the_shipping_terms(self):
        # same row twice: with ch_r1 = ch the reading term keeps candidate 0; with an exempted ch_r1 candidate 1 wins
        r = row([0.0, -0.5], [0.0, 0.0], [0, 1], ch=[0.0, -1.39])
        self.assertEqual(R.r1_pick(PLAIN, r, 2.0, 4)[0], 0)
        r["ch_r1"] = {1: [0.0, 0.0], 2: [0.0, 0.0], 4: [-2.0, 0.0]}
        self.assertEqual(R.r1_pick(PLAIN, r, 2.0, 4)[0], 1)
        self.assertEqual(r["ch"][4], [0.0, -1.39], "the shipping terms are untouched")

    def test_parse_cell_refuses_off_grid(self):
        self.assertEqual(R.parse_cell("1.0,4"), (1.0, 4))
        with self.assertRaises(SystemExit):
            R.parse_cell("3.0,4")


class SandhiVariant(unittest.TestCase):
    """sandhi.py: the spoken tones of 一 and 不 by the next syllable, the ordinal / date exceptions, misaligned rows untouched."""

    def test_rules(self):
        import sandhi as S
        cases = [("不要", ["ㄅㄨˋ", "ㄧㄠˋ"], ["ㄅㄨˊ", "ㄧㄠˋ"]), ("不好", ["ㄅㄨˋ", "ㄏㄠˇ"], ["ㄅㄨˋ", "ㄏㄠˇ"]),
                 ("一個", ["ㄧ", "ㄍㄜˋ"], ["ㄧˊ", "ㄍㄜˋ"]), ("一天", ["ㄧ", "ㄊㄧㄢ"], ["ㄧˋ", "ㄊㄧㄢ"]),
                 ("第一次", ["ㄉㄧˋ", "ㄧ", "ㄘˋ"], ["ㄉㄧˋ", "ㄧ", "ㄘˋ"]), ("一月", ["ㄧ", "ㄩㄝˋ"], ["ㄧ", "ㄩㄝˋ"]),
                 ("統一", ["ㄊㄨㄥˇ", "ㄧ"], ["ㄊㄨㄥˇ", "ㄧ"]), ("一個", ["ㄧ", "ㄍㄜ˙"], ["ㄧˊ", "ㄍㄜ˙"]), ("一下", ["ㄧ", "ㄒㄧㄚ˙"], ["ㄧ", "ㄒㄧㄚ˙"]),
                 ("之一，我們", ["ㄓ", "ㄧ", "ㄨㄛˇ", "ㄇㄣ˙"], ["ㄓ", "ㄧ", "ㄨㄛˇ", "ㄇㄣ˙"]),
                 ("用A一個", ["ㄩㄥˋ", "ㄧ", "ㄍㄜˋ"], ["ㄩㄥˋ", "ㄧˊ", "ㄍㄜˋ"])]
        for s, syls, want in cases:
            self.assertEqual(S.sandhi(s, syls)[0], want, s)
        self.assertEqual(S.sandhi("不要", ["ㄅㄨˋ", "ㄧㄠˋ"])[1], 1)
        self.assertIsNone(S.sandhi("一個", ["ㄧ"]))

    def test_rows_file(self):
        import sandhi as S
        text = "# comment\n|不要|ㄅㄨˋ ㄧㄠˋ\n|一個人|ㄧ ㄍㄜˋ\n"
        out, n = S.sandhi_rows(text)
        self.assertEqual(out, "# comment\n|不要|ㄅㄨˊ ㄧㄠˋ\n|一個人|ㄧ ㄍㄜˋ\n")
        self.assertEqual(n, {"rows": 2, "rows_changed": 1, "syllables_changed": 1, "misaligned": 1})
        self.assertEqual(S.sandhi_rows("|不要|ㄅㄨˋ  ㄧㄠˋ\r\n")[0], "|不要|ㄅㄨˊ ㄧㄠˋ\r\n", "CRLF and a double space read as the CLI does")


if __name__ == "__main__":
    unittest.main()
