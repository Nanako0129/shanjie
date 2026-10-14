"""Tests for tools/triage.py (docs/contracts/triage.md section 5.2): synthetic lexicons, rows and CLI files, one test per rule.
Run: python3 -m unittest tools/test_triage.py   (from the repo root). No network, no models, no private data.

Syllables are placeholders ("a", "ni", ...): the tool only compares them. A row is built by `row`; `classify` is called directly for wrong rows."""
import io, json, os, struct, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import triage as T  # noqa: E402

LEN = T.load_lenient()
BASE = """# comment line
_ctrl_punctuation_! ！ 0.0
a 甲 -1
b 乙 -1
c 丙 -1
d 丁 -1
e 戊 -1
b-c 乙丙 -2
ni 你 -1
hao 好 -1
ni-hao 你好 -2
ma 嗎 -1
hao-ma 好嗎 -2
zai 再 -1
zai 在 -1
yi-ge 一個 -2
x-y 舊詞 -2
bad-line-with-two-fields 字
a-b 單 -1
"""
OVERLAY = "x-y\t新詞\t-2\twikt\nz-w\t舊詞\t-2\twikt\n"   # 舊詞 is also in the base file: not an overlay-only word
SANDHI = "yi2-ge\t一個\t-2\tsandhi\nm-n\t只變\t-2\tsandhi\n"
PACK = "k-l\t包詞\t-2\tacg\n"
PAR = (1.0, 2.0, 0.5)   # alpha, tau, delta


def lex(sandhi=SANDHI):
    return T.Lex(BASE, OVERLAY, sandhi, PACK)


def row(truth, syls, cands, gold0=None, c=0, scores=None, ctx=""):
    """cands: [(surface, score, "w1 w2")] for ranks 1..; gold0: the rank-0 split as a string or None (`-`)."""
    return {"truth": truth, "syls": syls.split(), "cands": [(s, sc, w.split()) for s, sc, w in cands], "gold0": gold0.split() if gold0 else None,
            "c": c, "scores": scores, "ctx": ctx}


def cls(r, l=None, par=PAR):
    return T.classify(r, l or lex(), par, LEN)


def scores(ng, ok, ll=None, c=0):
    d = {"i": 1, "n": len(ng), "ng": ng, "ok": ok, "c": c}
    if ll is not None:
        d["ll"] = ll
    return d


class Lexicon(unittest.TestCase):
    def test_four_files_and_their_filters(self):
        l = lex()
        self.assertIn("甲", l.by_reading[("a",)])
        self.assertNotIn("！", l.readings_of, "control lines are skipped like Lexicon::parse_with")
        self.assertFalse(l.has("字"), "a row with two fields is skipped")
        self.assertFalse(l.has("單"), "a word whose length differs from its syllable count is skipped")
        self.assertEqual(l.by_reading[("yi2", "ge")], {"一個"}, "sandhi-add.tsv rows are entries")
        self.assertEqual(l.base & {"新詞", "包詞", "只變"}, set())
        self.assertEqual(l.source, {"新詞", "舊詞", "包詞"}, "overlay and pack words are sources; sandhi readings are not")

    def test_bad_overlay_row_is_refused(self):
        with self.assertRaises(ValueError):
            T.Lex(BASE, "x-y\t新詞\t-2\n", "", "")


class Pick(unittest.TestCase):
    def test_pick_follows_core(self):
        self.assertEqual(T.pick([0, -0.5], [-1, 0.0], 1.0, 2.0, 0.5), 1, "fused gain 0.5 >= delta 0.5")
        self.assertEqual(T.pick([0, -0.5], [-1, 0.0], 1.0, 2.0, 0.9), 0, "fused gain 0.5 < delta")
        self.assertEqual(T.pick([0, -3], [-1, 5.0], 1.0, 2.0, 0.0), 0, "margin >= tau keeps the first")
        self.assertEqual(T.pick([0], [-1], 1.0, 2.0, 0.0), 0)
        self.assertEqual(T.pick([0, -1, -1], [-2, 0.0, 0.0], 1.0, 9.0, 0.0), 1, "ties go to the better n-gram rank")

    def test_header_params(self):
        def head(v):
            b = bytearray(116 if v == 2 else 100)
            b[:8] = b"SJTL0002" if v == 2 else b"SJTL0001"
            struct.pack_into("<I", b, 8, v)
            struct.pack_into("<dd", b, 36, 0.3, 2.0)
            struct.pack_into("<dd", b, 52, 0.7, 1.0)
            if v == 2:
                struct.pack_into("<dd", b, 100, 0.25, 2.0)
            return bytes(b)
        with tempfile.TemporaryDirectory() as td:
            for v, want in ((1, {"chat": (0.3, 2.0, 0.0), "formal": (0.7, 1.0, 0.0)}), (2, {"chat": (0.3, 2.0, 0.25), "formal": (0.7, 1.0, 2.0)})):
                p = os.path.join(td, f"v{v}.sjtl")
                open(p, "wb").write(head(v))
                self.assertEqual(T.tinylm_params(p), want)


class Segments(unittest.TestCase):
    def test_lenient_compare_and_original_fallback(self):
        self.assertEqual(T.diff_segments("她好", ["他", "好"], LEN), [], "她/他 are the same after normalising")
        self.assertEqual(T.diff_segments("安好", ["他", "好"], LEN), [(0, 1)])
        shorten = lambda s: s.replace("XY", "Z")   # a normaliser that changes lengths: the original strings are compared
        self.assertEqual(T.diff_segments("XYb", ["Z", "Y", "b"], shorten), [(0, 1)])

    def test_widened_to_word_boundaries_and_touching_merged(self):
        self.assertEqual(T.diff_segments("甲丁丙", ["甲乙", "丙"], LEN), [(0, 2)])
        self.assertEqual(T.diff_segments("丁丁丙戊戊", list("甲乙丙丁戊"), LEN), [(0, 2), (3, 4)], "0 and 1 touch, 3 stands apart")

    def test_bands(self):
        self.assertEqual([T.margin_band(m) for m in (0, 0.249, 0.25, 0.5, 0.99, 1, 1.99, 2, 9)],
                         ["<0.25", "<0.25", "0.25-0.5", "0.5-1", "0.5-1", "1-2", "1-2", ">=2", ">=2"])
        self.assertEqual([T.rank_band(r) for r in (2, 3, 8, 9, 64)], ["2", "3-8", "3-8", "9-64", "9-64"])


class Buckets(unittest.TestCase):
    def test_a1_is_per_word_and_any_word_counts(self):
        # gold only in the rank-0 split; the first word reads fine, the second has no entry under hao4 but has one under hao
        r = row("你好", "ni hao4", [("泥號", -1, "泥 號")], gold0="你 好")
        res = cls(r)
        self.assertEqual((res["bucket"], res["seg"]), ("A1", "lex"))

    def test_a1_looks_only_inside_the_difference_segments(self):
        # 好 has the wrong reading but sits outside the one differing position (0)
        res = cls(row("你好", "ni hao4", [("泥好", -1, "泥 好")], gold0="你 好"))
        self.assertEqual(res["bucket"], "B2")

    def test_sandhi_only_reading_is_not_a1(self):
        r = row("一個", "yi2 ge", [("以個", -1, "以 個")], gold0="一個")
        self.assertEqual(cls(r)["bucket"], "B2", "the sandhi row makes 一個 an entry under yi2-ge")
        self.assertEqual(cls(r, lex(sandhi=""))["bucket"], "A1", "without sandhi-add.tsv the word is only under yi-ge")

    def test_lenient_only_match_is_a_ranking_error_not_b2(self):
        r = row("她好", "ta hao", [("安好", -1, "安 好"), ("他好", -2, "他 好")])
        res = cls(r)
        self.assertEqual((res["bucket"], res["sub"], res["rank"], res["margin"]), ("C", "C1", 2, "1-2"))

    def test_two_ranks_equal_use_the_first_ranks_split(self):
        r = row("你好嗎", "ni hao ma", [("泥好嗎", -1, "泥好 嗎"), ("你好嗎", -2, "你好 嗎"), ("你好嗎", -2.5, "你 好嗎")])
        res = cls(r)
        self.assertEqual((res["bucket"], res["sub"], res["rank"]), ("C", "C2", 2), "with the third rank's split it would be C3")

    def test_row_outside_the_list_uses_rank_0_and_its_split_fixes_the_segment(self):
        c = [("甲丁丙", -1, "甲 丁 丙")]
        # 甲乙 as one gold word: the one differing position widens to it, and 甲乙 is no entry -> B1
        res = cls(row("甲乙丙", "a b c", c, gold0="甲乙 丙"))
        self.assertEqual((res["bucket"], res["seg"]), ("B1", "lex"))
        self.assertEqual(cls(row("甲乙丙", "a b c", c, gold0="甲 乙 丙"))["bucket"], "B2", "split in characters: the segment is 乙 alone")
        res = cls(row("甲乙丙", "a b c", c, gold0=None))
        self.assertEqual((res["bucket"], res["seg"]), ("B2", "none"), "no split at all: the difference position itself")

    def test_b1_needs_two_characters(self):
        res = cls(row("己甲", "f a", [("乙甲", -1, "乙 甲")], gold0="己 甲"))
        self.assertEqual(res["bucket"], "B2", "己 is no entry but a one-character segment is not a missing word")

    def test_b1_takes_any_segment(self):
        c = [("丁丁丙戊戊", -1, "丁 丁 丙 戊 戊")]
        res = cls(row("甲乙丙丁戊", "a b c d e", c, gold0="甲乙 丙 丁 戊"))
        self.assertEqual(res["bucket"], "B1", "(3,4) is one character, but (0,2) qualifies")

    def test_d1_needs_scores_and_a_moved_right_first(self):
        cands = [("甲", -1, "甲"), ("乙", -1.2, "乙")]
        self.assertEqual(cls(row("甲", "a", cands, c=1, scores=scores([-1, -1.2], [1, 0], [0, 0], c=1)))["bucket"], "D1")
        self.assertEqual(cls(row("甲", "a", cands, c=1, scores=None))["bucket"], "C", "without --tinylm-scores nothing says the rerank moved it")

    def test_d1_needs_the_first_ngram_candidate_to_be_right(self):
        cands = [("乙", -1, "乙"), ("丙", -1.1, "丙"), ("甲", -1.2, "甲")]
        res = cls(row("甲", "a", cands, c=1, scores=scores([-1, -1.1, -1.2], [0, 0, 1], [0, 0, 0], c=1)))
        self.assertEqual(res["bucket"], "C", "the first was already wrong: the rerank did not spoil it")

    def test_a1_is_judged_when_the_gold_is_in_the_list_too(self):
        res = cls(row("你好", "ni hao4", [("泥號", -1, "泥 號"), ("你好", -2, "你 好")]))
        self.assertEqual((res["bucket"], res["seg"]), ("A1", "rank"), "好 has no entry under hao4; the gold split comes from the list, not rank 0")

    def test_c4_two_segments_even_when_both_are_single_characters(self):
        r = row("甲乙丙丁戊", "a b c d e", [("戊乙丙丁丁", -1, "戊 乙 丙 丁 丁"), ("甲乙丙丁戊", -2, "甲 乙 丙 丁 戊")])
        self.assertEqual(cls(r)["sub"], "C4")

    def test_c3_word_boundaries_differ_even_for_a_one_character_segment(self):
        r = row("你好嗎", "ni hao ma", [("泥好嗎", -1, "泥好 嗎"), ("你好嗎", -2, "你 好嗎")])
        self.assertEqual(cls(r)["sub"], "C3")

    def test_c1_function_character_flag(self):
        res = cls(row("再", "zai", [("在", -1, "在"), ("再", -2, "再")]))
        self.assertEqual((res["sub"], res["func"]), ("C1", True))
        res = cls(row("甲", "a", [("乙", -1, "乙"), ("甲", -2, "甲")]))
        self.assertEqual((res["sub"], res["func"]), ("C1", False))

    def test_function_characters_in_any_c_sub_type(self):
        res = cls(row("你再", "ni zai", [("你在", -1, "你在"), ("你再", -2, "你 再")]))   # 在 glued into 你在: C3, still a function-character error
        self.assertEqual((res["sub"], res["func"], res["func_any"]), ("C3", False, True))
        self.assertTrue(cls(row("再", "zai", [("在", -1, "在"), ("再", -2, "再")]))["func_any"])
        self.assertFalse(cls(row("再甲", "zai a", [("在乙", -1, "在 乙"), ("再甲", -2, "再 甲")]))["func_any"], "one pair is not function characters")
        self.assertFalse(cls(row("再", "zai", [("在乙", -1, "在 乙"), ("再", -2, "再")]))["func_any"], "lengths differ: the zipped prefix pair proves nothing")

    def test_c2_source_flag_is_overlay_or_pack_only_never_sandhi(self):
        def go(truth, other, syls):
            r = row(truth, syls, [(other, -1, other), (truth, -2, truth)])
            res = cls(r)
            self.assertEqual(res["sub"], "C2")
            return res["source"]
        self.assertTrue(go("新詞", "舊詞", "x y"), "only in overlay-add.tsv")
        self.assertFalse(go("舊詞", "新詞", "x y"), "in the base file")
        self.assertTrue(go("包詞", "別的", "k l"), "only in the pack")
        self.assertFalse(go("只變", "別的", "m n"), "only in sandhi-add.tsv")

    def test_c5_several_words_with_the_same_boundaries(self):
        r = row("你好", "ni hao", [("泥號", -1, "泥 號"), ("你好", -2, "你 好")])
        self.assertEqual(cls(r)["sub"], "C5")

    def test_c_order_c3_before_c1(self):
        # a one-character segment whose boundaries differ (the chosen word is longer) must be C3, not C1
        r = row("你好嗎", "ni hao ma", [("泥好嗎", -1, "泥好 嗎"), ("你好嗎", -2, "你 好 嗎")])
        self.assertEqual(cls(r)["sub"], "C3")


class D2(unittest.TestCase):
    def c_row(self, third, sc):
        cands = [("乙", -1, "乙"), ("甲", -1.5, "甲"), (third, -1.6, third)]
        return row("甲", "a", cands, scores=sc)

    def test_b_when_the_fused_argmax_is_a_right_candidate_other_than_the_first_right_one(self):
        # gold at indices 1 and 2; j = 2 (fused -0.8 vs -1); the gain 0.2 < delta 0.5, so the pick stays on 0
        res = cls(self.c_row("甲", scores([0, -0.5, -0.6], [0, 1, 1], [-1, -2, -0.2])))
        self.assertEqual((res["bucket"], res["d2"]), ("C", "D2b"))

    def test_ties_go_to_the_better_ngram_rank(self):
        # fused scores of 1 and 2 are equal and better than the first: pick takes index 1, which is right
        res = cls(self.c_row("丙", scores([0, -0.2, -0.2], [0, 1, 0], [-1, -0.5, -0.5])))
        self.assertEqual(res["d2"], "D2b")

    def test_c_when_the_small_model_also_picks_a_wrong_candidate(self):
        res = cls(self.c_row("丙", scores([0, -0.5, -0.6], [0, 1, 0], [-1, -2, -0.2])))
        self.assertEqual(res["d2"], "D2c")

    def test_a_when_the_ngram_margin_reaches_tau(self):
        res = cls(self.c_row("丙", scores([0, -3, -3.5], [0, 1, 0], [-1, -2, -0.2])))
        self.assertEqual(res["d2"], "D2a")
        res = cls(self.c_row("丙", scores([0, -2.0, -3.5], [0, 1, 0], [-1, 5, -0.2])))   # tau is 2.0: a margin of exactly tau keeps the first, like core pick
        self.assertEqual(res["d2"], "D2a")

    def test_the_eighth_candidate_is_inside_the_rerank_window(self):
        cands = [("乙", -1 - i, "乙") for i in range(7)] + [("甲", -9, "甲")]
        sc = scores([0, -0.1, -0.2, -0.3, -0.4, -0.5, -0.6, -0.7], [0] * 8, [0] * 8)
        self.assertEqual(cls(row("甲", "a", cands, scores=sc))["d2"], "D2c", "rank 8 = TOPK is marked; rank 9 is not (test below)")

    def test_none_without_ll_even_with_a_wide_margin(self):
        res = cls(self.c_row("丙", scores([0, -3, -3.5], [0, 1, 0])))
        self.assertEqual(res["d2"], "D2-none")

    def test_not_marked_when_gold_is_beyond_the_first_eight_or_there_are_no_scores(self):
        cands = [("乙", -1 - i, "乙") for i in range(8)] + [("甲", -10, "甲")]
        self.assertIsNone(cls(row("甲", "a", cands, scores=scores([0] * 8, [0] * 8, [0] * 8)))["d2"])
        self.assertIsNone(cls(self.c_row("丙", None))["d2"])


class Counting(unittest.TestCase):
    def wrong(self, ctx=""):
        return row("甲", "a", [("乙", -1, "乙"), ("甲", -2, "甲")], ctx=ctx)

    def test_buckets_add_up_to_the_wrong_rows(self):
        rows = [self.wrong(), row("甲乙丙", "a b c", [("甲丁丙", -1, "甲 丁 丙")], gold0="甲乙 丙"), row("甲", "a", [("乙", -1, "乙")], gold0="甲"),
                row("你好", "ni hao4", [("泥號", -1, "泥 號")], gold0="你 好"), self.wrong(), self.wrong()]
        ok = [0, 0, 0, 0, 1, 1]
        cn, per = T.triage_rows(rows, ok, None, lex(), PAR, LEN)
        self.assertEqual((cn["n"], cn["top1"], cn["errors"]), (6, 2, 4))
        self.assertEqual(cn["buckets"], {"A1": 1, "B1": 1, "B2": 1, "D1": 0, "C": 1})
        self.assertEqual(sum(cn["buckets"].values()), cn["errors"], cn["n"] - cn["top1"])
        self.assertEqual([p[0] for p in per], ["C1", "B1", "B2", "A1", "ok", "ok"])
        self.assertEqual((cn["c_sub"]["C1"], cn["c_rank"]["2"], cn["c_margin"]["1-2"], cn["seg_lex"], cn["seg_none"]), (1, 1, 1, 3, 0))

    def test_f_is_the_right_rows_of_the_report_set_only(self):
        rows = [self.wrong(), self.wrong()]
        self.assertEqual(T.triage_rows(rows, [0, 1], None, lex(), PAR, LEN, report=True)[0]["F"], 1)
        self.assertEqual(T.triage_rows(rows, [0, 1], None, lex(), PAR, LEN, report=False)[0]["F"], 0)
        self.assertEqual(T.triage_rows(rows, [0, 1], None, lex(), PAR, LEN, report=True)[1][1][0], "F")

    def test_e_flags_need_a_han_context_and_a_changed_verdict(self):
        rows = [self.wrong("你"), self.wrong("你"), self.wrong("abc"), self.wrong(""), self.wrong("你"), self.wrong("你，")]
        ok = [1, 0, 1, 0, 1, 0]
        ok_nc = [0, 1, 0, 1, 1, 1]
        cn, per = T.triage_rows(rows, ok, ok_nc, lex(), PAR, LEN)
        self.assertEqual((cn["E1"], cn["E2"]), (1, 1), "row 1 right with the context only, row 2 wrong with it; the others have no Han context or no change")
        self.assertEqual([p[3] for p in per], ["E1", "E2", "-", "-", "-", "-"])


class Files(unittest.TestCase):
    def test_rows_and_lines_follow_the_cli(self):
        self.assertEqual(T.parse_rows("a|你|ni\r\nno bars\n前|文|x y\n|兩|||bad\n|好|ni hao\n"), [("a", "你", ["ni"]), ("前", "文", ["x", "y"]), ("", "好", ["ni", "hao"])])
        self.assertEqual(T.lines_of("a\nb\n"), ["a", "b"])
        self.assertEqual(T.lines_of("a\n\n"), ["a", ""])

    def test_a_lone_cr_inside_a_field_is_not_a_line_break(self):
        # Rust str::lines splits on \n only; text-mode open() would turn the lone \r into a second line and drop the row
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "rows.txt")
            with open(p, "wb") as f:
                f.write("前|你\r好|ni hao\r\n".encode())
            self.assertEqual(T.parse_rows(T.read_text(p)), [("前", "你\r好", ["ni", "hao"])])

    def test_cli_files(self):
        with tempfile.TemporaryDirectory() as td:
            def w(n, t):
                with open(os.path.join(td, n), "w", encoding="utf-8") as f:
                    f.write(t)
                return os.path.join(td, n)
            d = w("d", "1\t1\t甲乙\t-1.5\n1\t2\t乙乙\t-2.5\n2\t1\t丙\t-1.0\n")
            ws = w("w", "1\t0\t甲 乙\n1\t1\t甲乙\n1\t2\t乙 乙\n2\t0\t-\n2\t1\t丙\n")
            s = w("s", json.dumps({"i": 1, "c": 0}) + "\n" + json.dumps({"i": 2, "c": 0}) + "\n")
            cands, gold0, recs = T.read_cli_files(d, ws, s, 2)
            self.assertEqual(cands[0], [("甲乙", -1.5, ["甲乙"]), ("乙乙", -2.5, ["乙", "乙"])])
            self.assertEqual((gold0, recs[1]["c"]), ([["甲", "乙"], None], 0))
            bad = w("w2", "1\t1\t甲 丙\n")
            with self.assertRaises(RuntimeError):
                T.read_cli_files(d, bad, s, 2)


class Run(unittest.TestCase):
    def test_a_file_from_another_build_is_refused(self):
        new = {"commit": "a", "models": {"m": "1"}, "alpha_tau_delta": {"chat": [1, 2, 3]}, "cli_sha256": "c", "setting": "s"}
        T.check_same_run({}, new)
        T.check_same_run({k: v for k, v in new.items() if k != "cli_sha256"}, new)   # an older file without the CLI hash is not refused for it
        T.check_same_run(dict(new), new)
        for k, v in (("commit", "b"), ("models", {"m": "2"}), ("alpha_tau_delta", {"chat": [1, 2, 4]}), ("cli_sha256", "d")):
            with self.assertRaises(SystemExit):
                T.check_same_run({**new, k: v}, new)


class Render(unittest.TestCase):
    def test_table_refuses_a_file_with_other_fields(self):
        cn = T.empty_counts()
        cn["b1b"] = 0   # a field of an older triage.py
        doc = {"label": "t", "commit": "0" * 40, "setting": "s", "models": {}, "alpha_tau_delta": {}, "sets": {"x": {"profiles": {"chat": cn}}}}
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "t.json")
            with open(p, "w") as f:
                json.dump(doc, f)
            with self.assertRaises(SystemExit):
                T.cmd_table(type("A", (), {"json": [p], "out": os.path.join(td, "o.md")}))

    def test_table_prints_the_three_numbers_and_sorts_by_value(self):
        cn = T.empty_counts()
        cn.update(n=10, top1=5, errors=5)
        cn["buckets"].update(A1=1, B1=2, B2=0, D1=0, C=2)
        cn["c_sub"]["C3"] = 2
        cn["d2"].update({"D2b": 2})
        doc = {"label": "t", "commit": "0" * 40, "setting": "s", "models": {"m": "a" * 64}, "alpha_tau_delta": {"chat": [0.3, 2.0, 0.25], "formal": [0.7, 1.0, 2.0]},
               "sets": {"x": {"profiles": {"chat": cn}}}}
        text = T.render(doc)
        fam = [l for l in text.split("\n") if l.startswith("| ") and "×" in l]
        self.assertEqual(len(fam), len(T.FAMILIES))
        self.assertTrue(any("重排與融合的取捨" in l and "| 2 | 0.3 |" in l and "2 × 0.3 ÷ 1 = 0.6" in l for l in fam), "D1 + D2b = 2 rows, ratio 0.3, cost 1: all three numbers printed")
        self.assertTrue(any("斷詞（C3）" in l and "2 × 0.3 ÷ 2 = 0.3" in l for l in fam))
        values = [float(l.rsplit("=", 1)[1].strip(" |")) for l in fam]
        self.assertEqual(values, sorted(values, reverse=True))
        self.assertIn("| x | 10 | 5 | 50.0% | - | 1 | 2 | 0 | 0 | 2 |", text)


if __name__ == "__main__":
    unittest.main()
