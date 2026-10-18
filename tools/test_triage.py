"""Tests for tools/triage.py (docs/contracts/triage.md section 5.2): synthetic lexicons, rows and CLI files, one test per rule.
Run: python3 -m unittest tools/test_triage.py   (from the repo root). No network, no models, no private data.

Syllables are placeholders ("a", "ni", ...): the tool only compares them. A row is built by `row`; `classify` is called directly for wrong rows."""
import hashlib
import concurrent.futures, io, json, os, struct, sys, tempfile, unittest

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
NAMES = "p-q\t名店\t-2\tnames\n"
PAR = (1.0, 2.0, 0.5)   # alpha, tau, delta


def lex(sandhi=SANDHI):
    return T.Lex(BASE, OVERLAY, sandhi, PACK, NAMES)


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
        self.assertEqual(l.base & {"新詞", "包詞", "只變", "名店"}, set())
        self.assertEqual(l.by_reading[("p", "q")], {"名店"}, "names-add.tsv rows are entries (model-v6: the names layer is always on)")
        self.assertEqual(l.source, {"新詞", "舊詞", "包詞", "名店"}, "overlay, names and pack words are sources; sandhi readings are not")

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
        """Real tiny files from the exporter, one per format version (the version 3 one used to be refused: ValueError 'not a .sjtl file')."""
        sys.path.insert(0, os.path.join(T.ROOT, "experiments", "s7a-tinylm"))
        import export_sjtl as ex, tinylm
        chars = "甲乙丙丁戊己庚辛"
        chat, formal = (0.3, 2.0), (0.7, 1.0)
        levels = (0.1, 1.0, 0.5, 0.9, 3.0, 0.5, 0.2, 0.5, 0.25, 0.8, 2.0, 1.0)
        with tempfile.TemporaryDirectory() as td:
            npz = os.path.join(td, "m.npz")
            tinylm.save_params(npz, tinylm.random_params(list(chars), n_layer=1, d=8, n_head=2, seed=1), chars, n_layer=1, n_head=2)
            cases = [("v1", chat, formal, None, {"chat": (0.3, 2.0, 0.0), "formal": (0.7, 1.0, 0.0)}),
                     ("v2", chat + (0.25,), formal + (2.0,), None, {"chat": (0.3, 2.0, 0.25), "formal": (0.7, 1.0, 2.0)}),
                     ("v3", chat + (0.25,), formal + (2.0,), levels, {"chat": (0.3, 2.0, 0.25), "formal": (0.7, 1.0, 2.0)}),
                     ("v4", chat + (0.25,), formal + (2.0,), levels, {"chat": (0.3, 2.0, 0.25), "formal": (0.7, 1.0, 2.0)})]
            for name, c, f, lv, want in cases:
                out = os.path.join(td, name + ".sjtl")
                # version 4 (model-v6 section 2.7): the same header plus the SHA-256 of the kn.sjkn it was tuned with
                ex.export(npz, out, "int8", c, f, b"\0" * 32, levels=lv, kn_sha=b"\x07" * 32 if name == "v4" else None)
                self.assertEqual(open(out, "rb").read()[:8], f"SJTL000{name[1]}".encode(), name)
                self.assertEqual(T.tinylm_params(out), want, name)
            junk = os.path.join(td, "junk.sjtl")
            open(junk, "wb").write(b"x" * 300)
            self.assertRaises(ValueError, T.tinylm_params, junk)


class KnTag(unittest.TestCase):
    def test_kn_tag_is_the_clis(self):
        """The setting tag the CLI prints for the side file beside the model (core Lm::kn_tag; the eval/golden/kn-tiny files), "" without one."""
        kn = os.path.join(T.ROOT, "eval/golden/kn-tiny")
        for f, beta in (("kn.sjkn", "1"), ("kn-beta075.sjkn", "0.75")):
            b = open(os.path.join(kn, f), "rb").read()
            self.assertEqual(T.kn_tag(os.path.join(kn, f)), f"+kn:{hashlib.sha256(b).hexdigest()[:8]}:θ1:β{beta}")
        self.assertEqual(T.kn_tag(os.path.join(kn, "no-such.sjkn")), "")
        self.assertRaises(ValueError, T.kn_tag, os.path.join(kn, "bigram.sjlm"))


class SettingTag(unittest.TestCase):
    def test_formal_with_the_table_has_tri_and_no_kn_chat_keeps_kn(self):
        """The tag the CLI prints (cli/src/main.rs): trigram.sjt3 beside the model turns KN off for formal and appends +tri: after +acg."""
        kn, tri = "+kn:cbefbced:θ1:β1", "+tri:fdd3b05d"
        self.assertEqual(T.expected_tag("formal", "+ctx", "abcd1234", kn, tri), "lm-formal+ctx+tl:abcd1234+acg+tri:fdd3b05d")
        self.assertEqual(T.expected_tag("formal", "", "abcd1234", kn, tri), "lm-formal+tl:abcd1234+acg+tri:fdd3b05d")
        self.assertEqual(T.expected_tag("chat", "+ctx", "abcd1234", kn, tri), "lm-chat+ctx+kn:cbefbced:θ1:β1+tl:abcd1234+acg")
        self.assertEqual(T.expected_tag("formal", "+ctx", "abcd1234", kn, ""), "lm-formal+ctx+kn:cbefbced:θ1:β1+tl:abcd1234+acg")

    def test_tri_tag_is_the_sha8_of_the_file_or_empty(self):
        with tempfile.TemporaryDirectory() as td:
            f = os.path.join(td, "trigram.sjt3")
            open(f, "wb").write(b"table")
            self.assertEqual(T.tri_tag(f), "+tri:" + hashlib.sha256(b"table").hexdigest()[:8])
            self.assertEqual(T.tri_tag(os.path.join(td, "no-such.sjt3")), "")


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


def _patched(td, files, fake_cell, out=None):
    """The patches under which `cmd_run` runs the fake cell on the given set files: no CLI, models or lexicon."""
    from unittest import mock
    import contextlib
    stack = contextlib.ExitStack()
    stack.enter_context(mock.patch.multiple(T, OUT_DIR=out or os.path.join(td, "out"), load_lenient=lambda: None, kn_tag=lambda p: "",
                                            tinylm_params=lambda p: {"chat": (0.3, 2.0, 0.25), "formal": (0.7, 2.0, 1.0)},
                                            tri_tag=lambda p: "", git_head=lambda: "abc", run_cell=fake_cell, set_file=lambda n, r: files[n]))
    stack.enter_context(mock.patch.object(T.Lex, "load", lambda: None))
    stack.enter_context(mock.patch.object(T.bench, "sha_file", lambda p: "h"))
    stack.enter_context(mock.patch("sys.stderr", io.StringIO()))
    return stack


class Jobs(unittest.TestCase):
    def test_parallel_cells_write_the_same_json_and_rows(self):
        """`run --jobs 3` writes byte for byte the JSON and rows files of `--jobs 1` (the CLI, models and lexicon are faked)."""
        import argparse
        with tempfile.TemporaryDirectory() as td:
            files = {}
            for name, size, private in (("dev302", 30, False), ("fw", 10, False), ("discordtune", 20, True)):
                p = os.path.join(td, name + ".txt")
                with open(p, "w", encoding="utf-8") as f:
                    f.write("x" * size)
                files[name] = (p, private)

            seen = []

            def fake_cell(cli, lm, tl, prof, f, lex, par, lenient, report, tdir, kn, tri, threads):
                seen.append(threads)
                cn = T.empty_counts()
                cn["n"], cn["errors"] = os.path.getsize(f) + len(prof), len(prof)
                return cn, [("C", 1, "0.5", 0), ("A1", 2, "1-2", 1)]

            out = {}
            for jobs in (1, 3):
                rows_out = os.path.join(td, f"rows{jobs}")
                with _patched(td, files, fake_cell, out=os.path.join(td, f"out{jobs}")):
                    T.cmd_run(argparse.Namespace(label="L", set=list(files), private_root=td, cli="cli", rows_out=rows_out, jobs=jobs, cli_threads=3))
                got = {"json": open(os.path.join(td, f"out{jobs}", "L.json"), encoding="utf-8").read()}
                got.update({n: open(os.path.join(rows_out, n), encoding="utf-8").read() for n in sorted(os.listdir(rows_out))})
                out[jobs] = got
            self.assertEqual(seen, [3] * 12, "cmd_run passes --cli-threads to every cell")
            self.assertEqual(out[1], out[3])
            self.assertEqual(sorted(out[1]), ["L-dev302-chat.tsv", "L-dev302-formal.tsv", "L-fw-chat.tsv", "L-fw-formal.tsv", "json"])
            self.assertEqual(list(json.loads(out[3]["json"])["sets"]), ["dev302", "fw", "discordtune"])


class FailingCell(unittest.TestCase):
    def test_a_failing_cell_starts_no_later_cell_and_leaves_no_temp_dir(self):
        """--jobs 3 on threads: cell A fails while B and C are running; no cell starts after the failure, B and C finish and their temp
        dirs are deleted, the error is A's. Synchronised by events: A waits until B and C have started; B and C wait until A's temp dir
        has been removed (the test's TemporaryDirectory subclass sets the event), which happens after the stop event is set, so the
        two workers that become free find it set. The other cells must never reach run_cell."""
        import argparse, threading, time
        from unittest import mock

        class Dir(tempfile.TemporaryDirectory):
            def cleanup(self):
                super().cleanup()
                if self.name == a_dir[0]:
                    a_cleaned.set()
                    time.sleep(0.05)   # not a synchronisation: it widens the window in which a stop set only after the cleanup would be late

        with tempfile.TemporaryDirectory() as td:
            files, tmp = {}, os.path.join(td, "tmp")
            os.makedirs(tmp)
            for i, name in enumerate(("n0", "n1", "n2", "n3", "n4")):   # decreasing size: the pool's order is n0 chat, n0 formal, n1 chat, ...
                p = os.path.join(td, name + ".txt")
                with open(p, "w", encoding="utf-8") as f:
                    f.write("x" * (50 - i))
                files[name] = (p, False)
            a_dir, a_cleaned, started, ran, finished = [None], threading.Event(), [threading.Event(), threading.Event()], [], []

            def fake_cell(cli, lm, tl, prof, f, lex, par, lenient, report, tdir, kn, tri, threads):
                job = (os.path.basename(f)[:-4], prof)
                ran.append(job)
                open(os.path.join(tdir, "sentences"), "w").close()   # something a cell leaves in its temp dir
                if job == ("n0", "chat"):
                    a_dir[0] = tdir
                    for e in started:
                        self.assertTrue(e.wait(10))
                    raise RuntimeError("boom")
                self.assertIn(job, [("n0", "formal"), ("n1", "chat")], "a cell started after the failure")
                started[job == ("n1", "chat")].set()
                self.assertTrue(a_cleaned.wait(10))
                finished.append(job)
                return T.empty_counts(), [("C", 1, "0.5", 0)]

            with _patched(td, files, fake_cell), mock.patch.object(tempfile, "tempdir", tmp), mock.patch.object(tempfile, "TemporaryDirectory", Dir):
                with self.assertRaisesRegex(RuntimeError, "boom"):
                    T.cmd_run(argparse.Namespace(label="L", set=list(files), private_root=td, cli="cli", rows_out=os.path.join(td, "rows"), jobs=3, cli_threads=1))
            self.assertEqual(sorted(ran), [("n0", "chat"), ("n0", "formal"), ("n1", "chat")])
            self.assertEqual(sorted(finished), [("n0", "formal"), ("n1", "chat")])
            self.assertEqual(os.listdir(tmp), [])
            self.assertFalse(os.path.exists(os.path.join(td, "out", "L.json")))

    def test_a_failing_temp_dir_cleanup_also_stops_the_later_cells(self):
        """--jobs 2: cell A's run_cell succeeds but removing its temp dir raises; cell B is held until A's future is done (a done
        callback, added by wrapping submit), so the worker B frees finds the stop event set: no third cell runs."""
        import argparse, threading
        from unittest import mock

        class Dir(tempfile.TemporaryDirectory):
            def cleanup(self):
                super().cleanup()
                if self.name == a_dir[0]:
                    raise OSError("cannot remove the temp dir")

        with tempfile.TemporaryDirectory() as td:
            files, tmp = {}, os.path.join(td, "tmp")
            os.makedirs(tmp)
            for i, name in enumerate(("n0", "n1", "n2")):
                p = os.path.join(td, name + ".txt")
                with open(p, "w", encoding="utf-8") as f:
                    f.write("x" * (50 - i))
                files[name] = (p, False)
            a_dir, b_started, a_done, ran = [None], threading.Event(), threading.Event(), []

            def fake_cell(cli, lm, tl, prof, f, lex, par, lenient, report, tdir, kn, tri, threads):
                job = (os.path.basename(f)[:-4], prof)
                ran.append(job)
                if job == ("n0", "chat"):
                    a_dir[0] = tdir
                    self.assertTrue(b_started.wait(10))
                else:
                    self.assertEqual(job, ("n0", "formal"), "a cell started after the failure")
                    b_started.set()
                    self.assertTrue(a_done.wait(10))
                return T.empty_counts(), []

            real_submit = concurrent.futures.ThreadPoolExecutor.submit

            def submit(self, fn, job):
                fut = real_submit(self, fn, job)
                if job == ("n0", "chat"):
                    fut.add_done_callback(lambda _: a_done.set())
                return fut

            with _patched(td, files, fake_cell), mock.patch.object(tempfile, "tempdir", tmp), mock.patch.object(tempfile, "TemporaryDirectory", Dir), \
                    mock.patch.object(concurrent.futures.ThreadPoolExecutor, "submit", submit):
                with self.assertRaisesRegex(OSError, "cannot remove"):
                    T.cmd_run(argparse.Namespace(label="L", set=list(files), private_root=td, cli="cli", rows_out=os.path.join(td, "rows"), jobs=2, cli_threads=1))
            self.assertEqual(sorted(ran), [("n0", "chat"), ("n0", "formal")])
            self.assertEqual(os.listdir(tmp), [])

    def test_a_repeated_set_name_runs_once(self):
        import argparse
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "n0.txt")
            open(p, "w").close()
            for jobs in (1, 2):
                ran = []

                def fake_cell(cli, lm, tl, prof, f, lex, par, lenient, report, tdir, kn, tri, threads):
                    ran.append(prof)
                    return T.empty_counts(), []
                with _patched(td, {"n0": (p, True)}, fake_cell):
                    T.cmd_run(argparse.Namespace(label="L", set=["n0", "n0"], private_root=td, cli="cli", rows_out=td, jobs=jobs, cli_threads=1))
                self.assertEqual(sorted(ran), ["chat", "formal"])


class CliThreads(unittest.TestCase):
    def test_threads_reach_both_cli_calls_of_a_cell(self):
        from unittest import mock
        calls = []
        line = "## r.txt  lm-chat+ctx  {'n': 1, 'top1': 0, 'oracle@64': 0, 'top1_sha256': '" + "0" * 64 + "'}\n"

        def fake_sh(cmd, **kw):
            calls.append(cmd)
            return line
        with tempfile.TemporaryDirectory() as td:
            rows = os.path.join(td, "r.txt")
            with open(rows, "w", encoding="utf-8") as f:
                f.write("|你|ni\n")
            for threads, want in ((3, ["--threads", "3"]), (1, [])):
                calls.clear()
                with mock.patch.object(T.bench, "sh", fake_sh), mock.patch.object(T.bench, "sha_file", lambda p: "h" * 64):
                    with self.assertRaisesRegex(RuntimeError, "setting tag"):   # the fake summary's tag is not the shipping one; both calls are made first
                        T.run_cell("cli", "lm", "tl", "chat", rows, None, PAR, LEN, False, td, "", "", threads)
                self.assertEqual(len(calls), 2)
                for cmd in calls:
                    self.assertEqual(cmd[cmd.index("--tinylm") + 2:][:len(want)], want)
                    self.assertEqual("--threads" in cmd, bool(want))


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
