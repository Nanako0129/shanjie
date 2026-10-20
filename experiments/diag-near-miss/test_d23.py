"""Tests for d23.py (README D2/D3): synthetic rows, dump files and lexicons; no model, no network, no private data.
Run from the repo root: python3 -W ignore -m unittest experiments/diag-near-miss/test_d23.py -v"""
import json, os, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import d23 as D  # noqa: E402

T = D.T
LEN = T.load_lenient()
BASE = "zai-b 再乙 -2\nzai-b 在乙 -2\nzai 再 -1\nzai 在 -1\na 甲 -1\nb 乙 -1\nb-c 乙丙 -2\na-b 甲乙 -3\nx-y 舊詞 -2\n"
LEX = T.Lex(BASE, "x-y\t新詞\t-2\twikt\n", "", "", "")
OVL = {"新詞"}   # words of overlay-add.tsv and the pack (names-add.tsv is not in it)


def row(truth, syls, cands, c=0, ll=None, ng=None):
    """cands: [(surface, ng score, "w1 w2")]; the scores record covers the first 8, as the CLI writes it."""
    cs = [(s, sc, w.split()) for s, sc, w in cands]
    ng = ng or [sc for _, sc, _ in cands[:8]]
    rec = {"i": 1, "n": len(ng), "ng": ng, "ok": [int(LEN(s) == LEN(truth)) for s, _, _ in cands[:8]], "c": c}
    if ll is not None:
        rec["ll"] = ll
    return {"ctx": "", "truth": truth, "syls": syls.split(), "cands": cs, "gold0": None, "c": c, "scores": rec}


def oks(rows):
    return [LEN(r["cands"][r["c"]][0]) == LEN(r["truth"]) for r in rows]


def c1(truth, wrong):   # a C1 row: the wrong one is the first, the gold the second
    return row(truth, "a zai", [(wrong, -1.0, "甲 " + wrong[1]), (truth, -1.5, "甲 " + truth[1])], ll=[0.0, 1.0])


class Pairs(unittest.TestCase):
    def setUp(self):
        self.rows = [c1("甲再", "甲在"), c1("甲再", "甲在"), c1("甲在", "甲再"),
                     row("新詞", "x y", [("舊詞", -1.0, "舊詞"), ("新詞", -1.2, "新詞")], ll=[0.0, 0.0])]   # a C2 row: not in the C1 denominator
        self.ok = oks(self.rows)

    def test_c1_both_directions_and_denominator(self):
        crows = D.c_rows(self.rows, self.ok, LEX, LEN)
        c1t, n_c1, unpaired, _, _ = D.pair_tables(crows)
        self.assertEqual(len(crows), 4)
        self.assertEqual(n_c1, 3, "the C2 row is not in the denominator")
        self.assertEqual({k: v["n"] for k, v in c1t.items()}, {("在", "再", "zai"): 2, ("再", "在", "zai"): 1})
        self.assertEqual(D.ranked(c1t)[0], (("在", "再", "zai"), 2))
        self.assertEqual(c1t[("在", "再", "zai")]["margin"]["0.5-1"], 2)   # n-gram gap 0.5, lower edge included
        res = D.d2(self.rows, self.ok, crows, LEN)
        self.assertEqual((res["c1"], res["top10"], res["top10_share"]), (3, 3, 1.0))

    def test_c4_equal_split_by_position_unequal_only_counted(self):
        r = row("乙丙甲再", "b c a zai", [("丙乙甲在", -1.0, "丙 乙 甲 在"), ("乙丙甲再", -1.5, "乙丙 甲 再")], ll=[0.0, 0.0])
        crows = D.c_rows([r], [False], LEX, LEN)
        self.assertEqual(crows[0][1]["sub"], "C4")
        _, n_c1, _, c4, unequal = D.pair_tables(crows)
        self.assertEqual((n_c1, unequal), (0, 1), "乙丙 against 丙 + 乙: counted, not split")
        self.assertEqual(dict(c4), {("在", "再", "zai"): 1}, "the equal segment is split by position; it is not in the C1 table")


    def test_c1_pair_from_final_pick_not_rank_1(self):
        r = row("甲再", "a zai", [("甲乙", -1.0, "甲 乙"), ("甲在", -1.1, "甲 在"), ("甲再", -1.2, "甲 再")], c=1, ll=[0.0, 0.0, 0.0])
        c1t = D.pair_tables(D.c_rows([r], oks([r]), LEX, LEN))[0]
        self.assertEqual(list(c1t), [("在", "再", "zai")])

    def test_unpaired_c1_stays_in_the_denominator(self):
        r = row("再", "zai", [("在乙", -1.0, "在 乙"), ("再", -1.5, "再")], ll=[0.0, 0.0])   # sentences of different length
        crows = D.c_rows([r], oks([r]), LEX, LEN)
        self.assertEqual(crows[0][1]["sub"], "C1")
        c1t, n_c1, unpaired, _, _ = D.pair_tables(crows)
        self.assertEqual((n_c1, unpaired, len(c1t)), (1, 1, 0))


class Bias(unittest.TestCase):
    PAIR = ("在", "再", "zai")

    def cells(self, rows):
        return D.bias_cells(rows, oks(rows), self.PAIR, LEN)

    def test_fix_and_break_over_the_whole_set(self):
        fix = c1("甲再", "甲在")                                                                       # gold 再 at rank 2: fixed from b 0.5
        brk = row("甲在", "a zai", [("甲在", -1.0, "甲 在"), ("甲再", -1.2, "甲 再")], ll=[0.0, 0.5])   # right, and 再 wins once b is large
        other = row("甲乙", "a b", [("甲乙", -1.0, "甲乙"), ("乙乙", -1.1, "乙 乙")], ll=[0.0, 0.0])   # no 再 anywhere: untouched
        c = self.cells([fix, brk, other])
        self.assertEqual((c[0.1]["fixes"], c[0.1]["breaks"]), (0, 0))
        self.assertEqual(c[0.5]["fixes"], 1, "gold 再 passes the wrong one from b 0.5")
        self.assertEqual((c[0.8]["fixes"], c[0.8]["breaks"], c[0.8]["net"]), (1, 1, 0), "a row that was right and becomes wrong counts as a break")
        self.assertEqual(c[0.8]["affected"], 2)

    def test_baseline_is_the_final_pick_not_ngram_rank_1(self):
        r = row("甲在", "a zai", [("甲乙", -1.0, "甲 乙"), ("甲再", -1.05, "甲 再"), ("甲在", -1.1, "甲 在")], c=2, ll=[0.0, 0.0, 2.0])   # fusion picked rank 3, right
        c = self.cells([r])
        self.assertEqual((c[0.1]["affected"], c[0.1]["fixes"], c[0.1]["breaks"]), (1, 0, 0), "the fusion still picks the right one")
        self.assertEqual((c[0.5]["fixes"], c[0.5]["breaks"]), (0, 1), "right by the final pick, wrong after: a break (rank 1 was wrong all along)")

    def test_two_positions_add_the_bonus_twice(self):
        r = row("再再", "zai zai", [("在在", -1.0, "在 在"), ("再再", -1.7, "再 再")], ll=[0.0, 0.0])
        c = D.bias_cells([r], oks([r]), self.PAIR, LEN)
        self.assertEqual([c[b]["fixes"] for b in (0.3, 0.5)], [0, 1], "-1.7 + 2b passes -1.0 at b 0.5, -1.7 + b would not")

    def test_row_without_ll_is_decided_by_ngram_and_not_rescoring(self):
        r = row("甲再", "a zai", [("甲在", -1.0, "甲 在"), ("甲再", -1.2, "甲 再")])
        c = self.cells([r])
        self.assertEqual((c[0.1]["affected"], c[0.1]["fixes"]), (1, 0), "order kept but the bonus is not uniform: pick runs")
        self.assertEqual((c[0.5]["affected"], c[0.5]["rescoring"], c[0.5]["fixes"]), (1, 0, 1))

    def test_tau_gate_opens_without_an_order_change(self):
        r = row("甲再", "a zai", [("甲在", -1.0, "甲 在"), ("甲再", -3.5, "甲 再")], ll=[0.0, 10.0])   # margin 2.5 >= tau: the pick stays on rank 1
        c = self.cells([r])
        self.assertEqual([(c[b]["affected"], c[b]["fixes"]) for b in (0.5, 0.8)], [(1, 0), (1, 1)], "b 0.8: margin 1.7 < tau, the fused gold wins; order unchanged")

    def test_uniform_shift_is_not_affected(self):
        r = row("甲再", "a zai", [("甲再", -1.0, "甲 再"), ("乙再", -1.0001, "乙 再")], ll=[0.0, 0.0])   # every candidate has 再 at zai
        c = self.cells([r])
        self.assertEqual([(o["affected"], o["fixes"], o["breaks"]) for o in c.values()], [(0, 0, 0)] * len(c))

    def test_unreliable_is_strictly_above_10_percent(self):
        self.assertEqual([D.unreliable(1, 10), D.unreliable(2, 11), D.unreliable(0, 5), D.unreliable(1, 9), D.unreliable(0, 0)],
                         [False, True, False, True, False])

    def test_reading_must_match_at_the_position(self):
        r = row("甲再", "a b", [("甲在", -1.0, "甲 在"), ("甲再", -1.5, "甲 再")], ll=[0.0, 1.0])   # 再 sits on the reading b, not zai
        self.assertEqual(self.cells([r])[1.0]["affected"], 0)

    @staticmethod
    def rescoring_row(ng9=-2.5):
        fill = [(c + "在", -1.1 - 0.1 * i, c + " 在") for i, c in enumerate("乙丙丁戊己庚辛")]
        return row("甲在", "a zai", [("甲在", -1.0, "甲 在")] + fill + [("甲再", ng9, "甲 再")], ll=[0.0] * 8)

    def test_positive_only_in_unreliable_cells_is_not_a_verdict_positive(self):
        rows = [c1("甲再", "甲在"), self.rescoring_row(-1.9)]
        res = D.d2(rows, oks(rows), D.c_rows(rows, oks(rows), LEX, LEN), LEN)
        cells = res["bias"][0]["cells"]
        self.assertEqual((cells["0.8"]["net"], cells["0.8"]["affected"], cells["0.8"]["rescoring"], cells["0.8"]["unreliable"]), (1, 2, 1, True))
        self.assertEqual((res["positive"], len(res["positive_unreliable"]) > 0), ([], True))
        self.assertIn("不可靠", res["verdict"])

    def test_candidate_without_ll_is_needs_rescoring(self):
        r = self.rescoring_row()
        self.assertEqual(len(r["cands"]), 9)
        c = self.cells([r])
        self.assertEqual((c[0.1]["rescoring"], c[0.1]["affected"]), (0, 0), "still outside the top 8: the top 8 is unchanged")
        self.assertEqual((c[1.0]["rescoring"], c[1.0]["fixes"], c[1.0]["breaks"]), (1, 0, 0), "in the top 8 without ll: not a fix, not a break")
        self.assertTrue(c[1.0]["unreliable"], "1 of 1 affected rows is above 10%")
        self.assertFalse(c[0.1]["unreliable"])


class Classes(unittest.TestCase):
    COUNT = {"在": 1000, "再": 10, "丙": 100, "丁": 100, "甲乙": 1000}.get
    LS = {(("zai",), "在"): -3.0, (("zai",), "再"): -1.0, (("a",), "丙"): -2.5, (("b",), "丁"): -2.5, (("a", "b"), "甲乙"): -4.0}

    def klass(self, r):
        (row_, res, segs, gw, _, _), = D.c_rows([r], oks([r]), LEX, LEN)
        return D.d3_class(row_, res, segs, gw, LEX, OVL, lambda w: self.COUNT(w, 0), self.LS)[0]

    def test_priority_i_over_ii_and_iii(self):
        self.assertEqual(self.klass(c1("甲再", "甲在")), "i", "在 has more corpus and a lower prior (iii), but both are function characters: (i)")

    def test_ii_overlay_only_word_in_the_first(self):
        self.assertEqual(self.klass(row("舊詞", "x y", [("新詞", -1.0, "新詞"), ("舊詞", -1.2, "舊詞")], ll=[0.0, 0.0])), "ii")

    def test_iii_sums_log10_over_a_multi_word_segment(self):
        r = row("甲乙", "a b", [("丙丁", -1.0, "丙 丁"), ("甲乙", -1.2, "甲乙")], ll=[0.0, 0.0])
        # corpus: log10(100) + log10(100) = 4 > log10(1000) = 3; prior: -2.5 + -2.5 = -5 < -4 (each single word alone would fail one of them)
        self.assertEqual(self.klass(r), "iii")
        flipped = D.d3_class(*self._args(r), OVL, lambda w: {"丙": 10, "丁": 10, "甲乙": 1000}.get(w, 0), self.LS)[0]
        self.assertEqual(flipped, "iv", "corpus sum 2 < 3: not (iii)")
        self.assertEqual(D.d3_class(*self._args(r), OVL, lambda w: {"丙": 100, "甲乙": 1000}.get(w, 0), self.LS)[0], "iv", "count 0 is -inf, never higher")
        self.assertEqual(D.d3_class(*self._args(r), OVL, lambda w: {"丙": 100, "丁": 100}.get(w, 0), self.LS)[0], "iii", "gold side -inf")
        self.assertEqual(D.d3_class(*self._args(r), OVL, lambda w: {"丙": 100, "丁": 100, "甲乙": 1000}.get(w, 0), self.LS)[2], False, "2 words against 1")

    def _args(self, r):
        (row_, res, segs, gw, _, _), = D.c_rows([r], oks([r]), LEX, LEN)
        return row_, res, segs, gw, LEX

    def test_missing_prior_is_not_iii(self):
        r = row("甲乙", "a b", [("丙丁", -1.0, "丙 丁"), ("甲乙", -1.2, "甲乙")], ll=[0.0, 0.0])
        self.assertEqual(D.d3_class(*self._args(r), OVL, lambda w: 99, {})[1], True)

    def test_breakdown_by_subtype(self):
        rows = [c1("甲再", "甲在"), row("舊詞", "x y", [("新詞", -1.0, "新詞"), ("舊詞", -1.2, "舊詞")], ll=[0.0, 0.0]),
                row("甲乙", "a b", [("丙丁", -1.0, "丙 丁"), ("甲乙", -1.2, "甲乙")], ll=[0.0, 0.0])]
        res = D.d3(D.c_rows(rows, oks(rows), LEX, LEN), LEX, OVL, lambda w: self.COUNT(w, 0), self.LS)
        self.assertEqual(res["classes"], {"i": 1, "ii": 1, "iii": 1, "iv": 0})
        self.assertEqual((res["by_sub"]["C1"]["i"], res["by_sub"]["C2"]["ii"], res["by_sub"]["C3"]["iii"]), (1, 1, 1))
        self.assertAlmostEqual(res["share_ii_iii"], 2 / 3)
        self.assertEqual((res["iii_same_words"], res["c_not_rank1"], res["gold_path_not_gold0"]), (0, 0, 3), "gold0 is None in the fixtures")

    def test_ii_needs_not_in_base(self):
        r = row("新詞", "x y", [("舊詞", -1.0, "舊詞"), ("新詞", -1.2, "新詞")], ll=[0.0, 0.0])
        (row_, res, segs, gw, _, _), = D.c_rows([r], oks([r]), LEX, LEN)
        self.assertEqual(D.d3_class(row_, res, segs, gw, LEX, {"新詞", "舊詞"}, lambda w: 0, {})[0], "iv", "舊詞 is in the overlay set but also in the base")
        self.assertEqual(D.d3_class(row_, res, segs, gw, LEX, set(), lambda w: 0, {})[0], "iv", "names-add words are not in the set")

    def test_i_counts_for_c2_rows(self):
        r = row("再乙", "zai b", [("在乙", -1.0, "在乙"), ("再乙", -1.2, "再乙")], ll=[0.0, 0.0])
        self.assertEqual(D.c_rows([r], oks([r]), LEX, LEN)[0][1]["sub"], "C2")
        self.assertEqual(self.klass(r), "i")

    def test_c_not_rank1_and_labels(self):
        r = row("甲在", "a zai", [("甲再", -1.0, "甲 再"), ("甲在", -1.1, "甲 在")], c=1, ll=[0.0, 2.0])
        bad = row("甲再", "a zai", [("甲乙", -1.0, "甲 乙"), ("甲在", -1.1, "甲 在"), ("甲再", -1.2, "甲 再")], c=1, ll=[0.0, 0.0, 0.0])
        rows = [r, bad]
        res = D.analyze("chatclause", rows, oks(rows), LEX, LEN, lambda w: 0, {}, OVL)
        self.assertEqual(res["d3"]["c_not_rank1"], 1)
        self.assertTrue(res["d3"]["verdict"].startswith("只報") and res["d2"]["verdict"].startswith("只報"))
        self.assertFalse(D.analyze("discordtune", rows, oks(rows), LEX, LEN, lambda w: 0, {}, OVL)["d3"]["verdict"].startswith("只報"))


class Verdicts(unittest.TestCase):
    def test_d2(self):
        self.assertIn("< 40%", D.d2_verdict(39, 100, True))
        self.assertIn("開「偏置表」小片", D.d2_verdict(40, 100, True))
        self.assertIn("淨值都 ≤ 0", D.d2_verdict(40, 100, False))
        self.assertIn("不可靠的格子", D.d2_verdict(40, 100, False, True))
        self.assertIn("開「偏置表」小片", D.d2_verdict(40, 100, True, True))
        self.assertIn("結案", D.d2_verdict(0, 0, False))

    def test_d3(self):
        self.assertIn("開 P4", D.d3_verdict(20, 100))
        self.assertIn("報告，維護者決定", D.d3_verdict(19, 100))
        self.assertIn("報告，維護者決定", D.d3_verdict(10, 100))
        self.assertIn("結案", D.d3_verdict(9, 100))


class Files(unittest.TestCase):
    def test_files_to_json_without_sentences(self):
        rows = [c1("甲再", "甲在"), row("新詞", "x y", [("舊詞", -1.0, "舊詞"), ("新詞", -1.2, "新詞")], ll=[0.0, 0.0])]
        with tempfile.TemporaryDirectory() as d:
            w = lambda n, t: (open(os.path.join(d, n), "w", encoding="utf-8").write(t), os.path.join(d, n))[1]
            ctx = "秘密前文"
            rf = w("rows", "".join(f"{ctx}|{r['truth']}|{' '.join(r['syls'])}\n" for r in rows))
            df = w("dump", "".join(f"{i}\t{k}\t{s}\t{sc}\n" for i, r in enumerate(rows, 1) for k, (s, sc, _) in enumerate(r["cands"], 1)))
            wf = w("words", "".join(f"{i}\t0\t-\n" + "".join(f"{i}\t{k}\t{' '.join(ws)}\n" for k, (_, _, ws) in enumerate(r["cands"], 1)) for i, r in enumerate(rows, 1)))
            rs = w("rs", "".join(f"{i}\t{int(o)}\t0\t2\n" for i, o in enumerate(oks(rows), 1)))
            sf = w("sc", "".join(json.dumps({**r["scores"], "i": i}) + "\n" for i, r in enumerate(rows, 1)))
            got, ok = D.build_rows(rf, df, wf, rs, sf, LEN)
            self.assertEqual((len(got), ok), (2, [False, False]))
            self.assertEqual(got[0]["ctx"], ctx)
            res = D.analyze("t", got, ok, LEX, LEN, lambda w: 0, {}, OVL)
            text = json.dumps(res, ensure_ascii=False) + D.render("t", 2, res)
            for s in [ctx] + [c[0] for r in rows for c in r["cands"]] + ["甲", "新詞", "舊詞"]:
                if len(s) > 1 or s == "甲":
                    self.assertNotIn(s, text)
            self.assertIn("在", text)   # the pair itself may appear
            bad = w("rs2", "1\t1\t0\t2\n2\t0\t0\t2\n")   # rowstats says row 1 is right, the chosen candidate is wrong
            with self.assertRaises(RuntimeError):
                D.build_rows(rf, df, wf, bad, sf, LEN)


if __name__ == "__main__":
    unittest.main()
