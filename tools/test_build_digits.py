"""Unit tests for tools/build_digits.py on a synthetic corpus (docs/contracts/digit-context.md C.3). No real data, no network.
Run: python3 -W ignore -m unittest tools/test_build_digits.py"""
import collections
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_digits as bd  # noqa: E402
import holdout_guard  # noqa: E402

bc = bd.bc
L = bd.L
WORDS = ["下午", "點", "開會", "價格", "元", "我", "共", "人", "年", "第", "名", "的", "好", "買", "了"]


def mklex(words=WORDS):
    """segment() only reads by_word[w][1] and max_len; every word has the same score, so the longest-fewest segmentation wins ties."""
    lex = bd.bc.ime.Lexicon.__new__(bd.bc.ime.Lexicon)
    lex.by_word = {w: ((), -3.0) for w in words}
    lex.max_len = max(map(len, words))
    return lex


LEX = mklex()


class FakeLM:
    """What L.history reads of a model: ids and ctx (a word has a history record when its id is in ctx)."""

    def __init__(self, words):
        self.ids = {w: i for i, w in enumerate(words)}
        self.ctx = {i: (1,) for i in self.ids.values()}


HIST_WORDS = WORDS + ["機"]   # 機 has a history record, 算機 and 計算機 do not
LM = FakeLM(HIST_WORDS)
HIST = bd.make_history(LM)
# Every digits.md A.3.2 situation in 14 lines (hand counts in the tests below).
CORPUS = [
    "下午3點開會",           # 1  word D word word
    "下午3點",               # 2
    "價格300元",             # 3
    "價格３００元，共25人",    # 4  full-width digits; a one-character Han segment (共) before a run; a boundary between
    "2026年",                # 5  run at the start of the line
    "第1名",                 # 6  one-character Han segment before the run
    "嗯嗯嗯5元",             # 7  an unsegmentable Han segment is a boundary
    "價格1.5的",             # 8  a decimal is one run
    "我買了12345678901234",  # 9  a run of 14 characters, ends the line
    "共25",                  # 10 run followed by the end of the line
    "25，人",                # 11 run followed by a boundary
    "價格 300 元",           # 12 spaces are boundaries
    "2026-10-15年",          # 13 10 characters: counted as `10+`
    "價格300的",             # 14
]


def count(lines, lex=LEX, guard=None, hist=HIST):
    c = bd.Counts()
    for s in lines:
        c.add_line(bd.line_items(s, lex, hist, guard))
    return c


def table(lines=CORPUS, k=1):
    c = count(lines)
    return bd.Table(bd.render(c, "# header", k))


class Items(unittest.TestCase):
    def items(self, s, guard=None, lex=LEX, hist=HIST):
        return bd.line_items(s, lex, hist, guard)

    def test_one_char_han_next_to_run(self):
        self.assertEqual(self.items("共25人"), [("w", "共", "<s>"), ("D", "25", "共"), ("w", "人", "<s>")])   # after the run the text ends in digits: no context

    def test_unsegmentable_segment_is_boundary(self):
        self.assertEqual(self.items("嗯嗯嗯5元"), [("D", "5", "<s>"), ("w", "元", "<s>")])   # 嗯嗯 has no history record
        self.assertEqual(self.items("下午嗯嗯嗯5"), [("D", "5", "<s>")])        # the whole Han run 下午嗯嗯嗯 has no segmentation
        self.assertEqual(self.items("下午，嗯嗯嗯5"), [("w", "下午", "<s>"), bd.BOUNDARY, ("D", "5", "<s>")])

    def test_boundaries_never_repeat_and_are_implicit_at_the_ends(self):
        self.assertEqual(self.items("，，25，，"), [("D", "25", "<s>")])
        self.assertEqual(self.items("價格 300 元"), [("w", "價格", "<s>"), bd.BOUNDARY, ("D", "300", "<s>"), bd.BOUNDARY, ("w", "元", "<s>")])

    def test_run_shapes_and_fullwidth_digits(self):
        self.assertEqual(self.items("價格１.５的"), [("w", "價格", "<s>"), ("D", "1.5", "價格"), ("w", "的", "<s>")])
        self.assertEqual(self.items("2026-10-15年"), [("D", "2026-10-15", "<s>"), ("w", "年", "<s>")])
        self.assertEqual(self.items("3.")[0], ("D", "3", "<s>"))                      # a trailing separator is not part of the run
        self.assertEqual(self.items("1,000"), [("D", "1", "<s>"), bd.BOUNDARY, ("D", "000", "<s>")])   # `,` ends a run (answer (h))
        self.assertEqual(self.items("1:30"), [("D", "1", "<s>"), bd.BOUNDARY, ("D", "30", "<s>")])    # ... and `:` too
        self.assertEqual(self.items("A123元"), [("D", "123", "<s>"), ("w", "元", "<s>")])   # English is a boundary, and not a unit

    def test_context_is_the_scorers_history_of_the_text_before_the_unit(self):
        """C.6 item 1: v = L.history(L.context_key(text before the unit on the line), model), not the previous word."""
        lex = mklex(WORDS + ["計算機", "機"])
        got = self.items("計算機3台", lex=lex)
        self.assertEqual(got[0], ("w", "計算機", "<s>"))
        self.assertEqual(got[1], ("D", "3", "機"))                                   # the three-character word before the run: its last character
        self.assertEqual(got[1][2], L.history(L.context_key("計算機"), LM))           # ... exactly the function the scorer calls
        lex2 = mklex(WORDS + ["計算機", "算機"])
        self.assertEqual(self.items("計算機3", lex=lex2, hist=bd.make_history(FakeLM(HIST_WORDS + ["算機"])))[1][2], "算機")                # the last two characters win when they have a record
        # segmentation does not decide it: two words 下午 + 開會 give 開會's context 下午 -> its last two characters
        self.assertEqual(self.items("下午開會")[1], ("w", "開會", "下午"))
        self.assertEqual(self.items("下午3點開會"), [("w", "下午", "<s>"), ("D", "3", "下午"), ("w", "點", "<s>"), ("w", "開會", "點")])
        self.assertEqual(self.items("3好")[0], ("D", "3", "<s>"))                     # a line start, and no Han text before: <s>

    def test_history_is_cached_per_context_key(self):
        calls = []

        class Spy(FakeLM):
            @property
            def ctx(self):
                calls.append(1)
                return self._ctx

            @ctx.setter
            def ctx(self, v):
                self._ctx = v
        hist = bd.make_history(Spy(HIST_WORDS))
        for _ in range(5):
            hist("下午")
            hist("")
        self.assertEqual(len(calls), 1)    # "" is answered without a lookup; 下午 once
        self.assertEqual(hist("下午"), L.history("下午", Spy(HIST_WORDS)))

    def test_guarded_han_run_is_not_counted_and_is_a_boundary(self):
        class G:
            def any(self, run):
                return "開會" in run
        # the run 點開會 contains the guarded text: it becomes a boundary; 下午 (a separate Han run) stays
        self.assertEqual(self.items("下午3點開會", G()), [("w", "下午", "<s>"), ("D", "3", "下午"), bd.BOUNDARY])
        # ... and its characters are no context for what follows (here the run before 5 would be 開會 -> v 開會 if it were read)
        self.assertEqual(self.items("下午3點開會5", G())[-1], ("D", "5", "<s>"))

    def test_holdout_guard_matcher_from_section_8(self):
        """The real matcher (holdout_guard.build_matcher, an identity conversion table), rows file in the strict 3-field shape."""
        conv = ({}, {}, 1)
        with tempfile.TemporaryDirectory() as d:
            rows = os.path.join(d, "rows.txt")
            with open(rows, "w", encoding="utf-8") as f:
                f.write("|價格開會明天|ㄒ\n")
            guard = holdout_guard.build_matcher([rows], conv)[0]
        self.assertEqual(self.items("下午3價格開會明天5元", guard), [("w", "下午", "<s>"), ("D", "3", "下午"), bd.BOUNDARY, ("D", "5", "<s>"), ("w", "元", "<s>")])
        self.assertEqual(self.items("下午3點", guard), [("w", "下午", "<s>"), ("D", "3", "下午"), ("w", "點", "<s>")])


class Counting(unittest.TestCase):
    """Hand counts for CORPUS (comments in the module docstring of the test): T = 42 units, 27 words + 15 runs. N(v) counts the units whose
    context is v: 29 `<s>`, 下午 2, 價格 4, 共 2, 第 1, 了 1 (the run of 我買了12345678901234), 買 1 (了), 我 1 (買), 點 1 (開會)."""

    @classmethod
    def setUpClass(cls):
        cls.c = count(CORPUS)
        cls.t = table()

    def test_totals(self):
        c = self.c
        self.assertEqual((c.t, c.nd, c.ctx["<s>"], c.s), (42, 15, 29, 4))
        self.assertEqual((self.t.t, self.t.nd, self.t.s), (42, 15, 4))

    def test_D_rows(self):
        self.assertEqual(self.t.d, {"<s>": (5, 29), "下午": (2, 2), "價格": (4, 4), "第": (1, 1), "共": (2, 2), "了": (1, 1)})
        self.assertEqual(sum(n for n, _ in self.t.d.values()), 15)   # every run has exactly one predecessor row

    def test_W_Q_P_rows(self):
        self.assertEqual(self.t.w, {"點": 2, "元": 3, "人": 1, "年": 2, "名": 1, "的": 2})
        self.assertEqual(self.t.q, {"<s>": 3, "下午": 2, "價格": 4, "第": 1, "共": 1, "了": 0})
        self.assertEqual({v: dict(x) for v, x in self.t.p.items()}, {"下午": {"點": 2}, "價格": {"元": 2, "的": 2}, "<s>": {"年": 2}})
        self.assertEqual(sum(self.t.w.values()) + self.t.s, self.t.nd)

    def test_L_C_rows(self):
        self.assertEqual(self.t.l, {1: 4, 2: 3, 3: 5, 4: 1, 10: 2})              # 14 characters and 10 characters are both `10+`
        self.assertEqual(self.t.c, {"0": 12, "1": 6, "2": 9, "3": 8, "4": 2, "5": 7, "6": 3, "7": 1, "8": 1, "9": 1, ".": 1, "-": 2})
        self.assertEqual(self.t.sum_c, 53)

    def test_text_is_sorted_by_bytes_under_one_header(self):
        text = bd.render(self.c, "# header")
        lines = text.rstrip("\n").split("\n")
        self.assertEqual(lines[0], "# header")
        body = lines[1:]
        self.assertEqual(body, sorted(body, key=lambda r: r.encode("utf-8")))
        self.assertEqual(len(body), len(set(body)))
        self.assertTrue(text.endswith("\n") and "\r" not in text)

    def test_common_context_keeps_a_D_row_without_a_run(self):
        t = table(["我好"] * 1000 + ["下午3點"], k=5)
        self.assertEqual(t.d["我"], (0, 1000))         # 好 follows 我 a thousand times: N(我) = 1000, never followed by a run
        self.assertNotIn("好", t.d)                    # nothing follows 好: N(好) = 0
        self.assertNotIn("下午", t.d)                 # N(下午) = 1 < 1000 and N(v->D) = 1 < k = 5: cut, and no Q row either
        self.assertNotIn("下午", t.q)
        self.assertIn("<s>", t.d)                      # the <s> row is never cut
        self.assertNotIn("我", table(["我好"] * 999 + ["下午3點"]).d)   # 999 < 1,000

    def test_truncation_keeps_every_D_row_with_N_v_at_least_1000(self):
        """C.6 item 2: 1 <= N(v->D) < k with N(v) >= 1000 must not fall back to the global P(D) (it could be larger than the common v's own rate)."""
        lines = ["好好"] * 1000 + ["好3", "好5", "好7"]           # 好: followed 1000 + 3 times, 3 of them by a run
        for k in (1, 2, 3, 5, 50):
            t = table(lines, k=k)
            self.assertEqual(t.d["好"], (3, 1003), k)            # kept at every k, with its real count
        t5 = table(lines, k=5)
        self.assertNotIn("好", t5.q)                              # but D only: Q needs N(v->D) >= k
        self.assertAlmostEqual(t5.p_D_given("好"), (3 + t5.p_D()) / (1003 + 1))   # its own rate, not the global P(D)
        self.assertNotIn("下午", table(lines + ["下午3點"], k=5).d)   # N(下午) = 1: below the common threshold, cut as before

    def test_k_cuts_D_Q_W_P_together(self):
        t = table(k=2)
        self.assertEqual(set(t.d), {"<s>", "下午", "價格", "共"})              # 第 and 了 have N(v->D) = 1
        self.assertEqual(set(t.q), {"<s>", "下午", "價格", "共"})
        self.assertEqual(t.w, {"點": 2, "元": 3, "年": 2, "的": 2})
        k5 = table(k=5)
        self.assertEqual(set(k5.d), {"<s>"})
        self.assertEqual((k5.w, dict(k5.p)), ({}, {}))

    def test_size_fallback_picks_the_first_k_that_fits(self):
        sizes = {k: len(bd.render(self.c, "#", k).encode()) for k in bd.K_STEPS}
        self.assertTrue(sizes[1] > sizes[2] > sizes[5])
        old = bd.MAX_BYTES
        try:
            bd.MAX_BYTES = sizes[1]
            self.assertEqual(bd.build_text(self.c, lambda k: "#")[1], 1)
            bd.MAX_BYTES = sizes[1] - 1
            self.assertEqual(bd.build_text(self.c, lambda k: "#")[1], 2)
            bd.MAX_BYTES = sizes[2] - 1
            self.assertEqual(bd.build_text(self.c, lambda k: "#")[1], 3)
            bd.MAX_BYTES = 10
            self.assertEqual(bd.build_text(self.c, lambda k: "#"), (None, None))
        finally:
            bd.MAX_BYTES = old

    def test_workers_preprocess_wiki_markup_and_text_lines(self):
        """_work: wiki raw text through bc.article_sentences (entity, TEMPLATE, MARKUP, SENT; empty conversion tables), lines through bc.convert."""
        saved = dict(bc._W)
        bc._W.update({"lex": LEX, "conv": ({}, {}, 1), "guard": None, "hist": HIST})
        try:
            raw = "{{模板|甲}}下午&lt;ref&gt;x&lt;/ref&gt;3點。價格[[甲|300]]元；共25人\n第1名"
            c = bd._work(("wiki", [raw]))
            self.assertEqual((c.t, c.nd), (12, 4))   # 下午 D 點 | 價格 D 元 | 共 D 人 | 第 D 名 (the ref is gone; 。 ； \n split)
            c2 = bd._work(("text", ["價格３００元"]))
            self.assertEqual(dict(c2.d), {"價格": 1})
        finally:
            bc._W.clear()
            bc._W.update(saved)

    def test_article_runs_unchanged_by_the_sentence_split(self):
        raw = "甲乙丙。丁戊己2庚辛\n壬癸"
        self.assertEqual(list(bc.article_runs(raw, ({}, {}, 1))), ["甲乙丙", "丁戊己", "庚辛", "壬癸"])


class Formulas(unittest.TestCase):
    """digits.md A.3.2, quantity by quantity, from the table text (k = 1)."""

    @classmethod
    def setUpClass(cls):
        cls.t = table()
        cls.pw = lambda s, w: {"元": 0.01, "人": 0.02, "點": 0.005, "好": 0.03}.get(w, 0.001)

    def test_P_D(self):
        self.assertAlmostEqual(self.t.p_D(), 15 / 42)

    def test_P_D_given_C(self):
        pd = 15 / 42
        self.assertAlmostEqual(self.t.p_D_given("價格", 1.0), (4 + pd) / (4 + 1))
        self.assertAlmostEqual(self.t.p_D_given("價格", 2.0), (4 + 2 * pd) / (4 + 2))
        self.assertAlmostEqual(self.t.p_D_given("<s>", 1.0), (5 + pd) / (29 + 1))
        self.assertAlmostEqual(self.t.p_D_given("了", 1.0), (1 + pd) / (1 + 1))
        self.assertAlmostEqual(self.t.p_D_given("開會"), pd)         # no D row: P(D)
        self.assertAlmostEqual(self.t.p_D_given("點", 5.0), pd)

    def test_P_len(self):
        t = self.t
        self.assertAlmostEqual(t.p_len(3, True), 5 / 15)
        self.assertAlmostEqual(t.p_len(3, False), (5 + 1 + 2) / 15)    # N(L >= 3) = 3-char 5 + 4-char 1 + `10+` 2
        self.assertAlmostEqual(t.p_len(1, False), 1.0)
        self.assertAlmostEqual(t.p_len(5, True), 0.0)                  # no L=5 row
        self.assertAlmostEqual(t.p_len(5, False), 2 / 15)
        for m in (10, 11, 40):                                         # m >= 10: ended and not ended both use N(L = 10+) / N(D)
            self.assertAlmostEqual(t.p_len(m, True), 2 / 15)
            self.assertAlmostEqual(t.p_len(m, False), 2 / 15)

    def test_P_ch(self):
        self.assertAlmostEqual(self.t.p_ch("0"), 12 / 53)
        self.assertAlmostEqual(self.t.p_ch("-"), 2 / 53)
        self.assertEqual(self.t.p_ch("/"), 0.0)

    def test_P_w_given_D(self):
        g = 1.0
        self.assertAlmostEqual(self.t.p_w_given_D("元", self.pw("元"), g), (3 + g * 0.01) / (15 + g))
        self.assertAlmostEqual(self.t.p_w_given_D("元", 0.01, 3.0), (3 + 3 * 0.01) / (15 + 3))

    def test_no_W_row_numerator_is_zero(self):
        self.assertAlmostEqual(self.t.p_w_given_D("好", 0.03, 1.0), (0 + 0.03) / 16)
        self.assertAlmostEqual(self.t.p_w_given_D("開會", 0.001, 1.0), 0.001 / 16)

    def test_P_w_given_v_D(self):
        t, g = self.t, 1.0
        base = lambda w: t.p_w_given_D(w, self.pw(w), g)
        self.assertAlmostEqual(t.p_w_given_vD("元", "價格", self.pw("元"), g), (2 + g * base("元")) / (4 + g))
        self.assertAlmostEqual(t.p_w_given_vD("點", "下午", self.pw("點"), g), (2 + g * base("點")) / (2 + g))
        self.assertAlmostEqual(t.p_w_given_vD("年", "<s>", 0.001, g), (2 + g * base("年")) / (3 + g))

    def test_no_P_row_numerator_is_zero_but_Q_still_divides(self):
        t, g = self.t, 1.0
        base = t.p_w_given_D("人", self.pw("人"), g)
        self.assertAlmostEqual(t.p_w_given_vD("人", "價格", self.pw("人"), g), (0 + g * base) / (4 + g))   # Q row 4, no (價格, 人) row
        base = t.p_w_given_D("名", 0.001, g)
        self.assertAlmostEqual(t.p_w_given_vD("名", "第", 0.001, g), (0 + g * base) / (1 + g))             # count 1: never written as a P row
        self.assertAlmostEqual(t.p_w_given_vD("人", "了", 0.02, g), t.p_w_given_D("人", 0.02, g) * g / (0 + g))   # Q row 0

    def test_no_Q_row_falls_back_to_P_w_given_D(self):
        t = self.t
        for v in ("開會", "點", "我"):
            self.assertEqual(t.p_w_given_vD("元", v, 0.01, 1.0), t.p_w_given_D("元", 0.01, 1.0))
        k2 = table(k=2)   # 第 lost its D and Q rows to k = 2
        self.assertEqual(k2.p_w_given_vD("名", "第", 0.001, 1.0), k2.p_w_given_D("名", 0.001, 1.0))

    def test_no_D_row_no_W_row_no_Q_row_no_P_row_together(self):
        t = self.t
        self.assertAlmostEqual(t.p_D_given("開會"), t.p_D())
        self.assertAlmostEqual(t.p_w_given_vD("好", "開會", 0.03, 1.0), 0.03 / 16)

    def test_successor_layer(self):
        t = self.t
        self.assertEqual(t.successors("價格"), frozenset({"點", "元", "人", "年", "名", "的"}))   # N(D->w) > 0 words; P rows add nothing new here
        t2 = table(k=2)
        self.assertEqual(t2.successors("價格"), frozenset({"點", "元", "年", "的"}))
        self.assertEqual(table(["價格3人", "價格3人"], k=1).successors("價格"), frozenset({"人"}))

    def test_round_trip_through_text(self):
        c = count(CORPUS)
        t1 = bd.Table(bd.render(c, "# h", 1))
        t2 = bd.Table(bd.render(c, "# h", 1))
        self.assertEqual((t1.d, t1.w, t1.q, dict(t1.p), t1.l, t1.c), (t2.d, t2.w, t2.q, dict(t2.p), t2.l, t2.c))
        with self.assertRaises(ValueError):
            bd.Table("X\t1\n")


class Merge(unittest.TestCase):
    def test_merge_equals_one_pass(self):
        a, b, whole = count(CORPUS[:6]), count(CORPUS[6:]), count(CORPUS)
        a.merge(b)
        self.assertEqual(bd.render(a, "#"), bd.render(whole, "#"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
