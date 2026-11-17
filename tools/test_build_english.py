"""Unit tests for tools/lib/english_tokens.py, tools/build_english.py, tools/build_english_probe.py and tools/select_english_cell.py on synthetic
input (docs/spec/english-mixed.md §16.4, §16.5, §16.8). No real data, no network, no model.
Run: python3 -W ignore -m unittest tools/test_build_english.py"""
import collections
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "reference", "proto"))
import build_english as be  # noqa: E402
import build_english_probe as bp  # noqa: E402
import english_score as es  # noqa: E402
import select_english_cell as sel  # noqa: E402

bc, et = be.bc, be.et
WORDS = ["我", "用", "中文", "很", "好", "的", "推", "今天", "開會", "看", "了"]


def mklex(words=WORDS):
    lex = bc.ime.Lexicon.__new__(bc.ime.Lexicon)
    lex.by_word = {w: ((), -3.0) for w in words}
    lex.max_len = max(map(len, words))
    return lex


class FakeLM:
    def __init__(self, words):
        self.ids = {w: i for i, w in enumerate(words)}
        self.ctx = {i: (1,) for i in self.ids.values()}


LEX = mklex()
HIST = be.make_history(FakeLM(WORDS))
CONV = ({}, {}, 1)


def toks(s, **kw):
    return [t for _, _, t in et.tokens(s, **kw)]


def doc_in(buckets, source="pool", prefix="d"):
    """A document id of `source` whose bucket is in `buckets` (searched, so the tests do not depend on a hash by hand)."""
    return next(f"{prefix}{i}" for i in range(10000) if et.bucket(source, f"{prefix}{i}") in buckets)


def docs_in(buckets, n, source="forum"):
    out, i = [], 0
    while len(out) < n:
        if et.bucket(source, f"b/a{i}") in buckets:
            out.append(f"b/a{i}")
        i += 1
    return out


class Tokens(unittest.TestCase):
    def test_a_token_is_beside_chinese(self):
        self.assertEqual(toks("我用ok了"), ["ok"])
        self.assertEqual(toks("我用 Google 看"), ["google"])           # blanks skipped, lower-cased
        self.assertEqual(toks("ok 很好"), ["ok"])                       # the sentence start is a side
        self.assertEqual(toks("今天 meeting"), ["meeting"])
        self.assertEqual(toks("我用，ok，了"), [])                       # punctuation on both sides is neither Han nor the sentence's end
        self.assertEqual(toks("很好, ok"), ["ok"])                      # the end is
        self.assertEqual(toks("hello world"), [])                       # no Han in the sentence
        self.assertEqual(toks("我用gta6和3d"), ["gta6"])                 # digits inside; a run that starts with a digit is no token
        self.assertEqual(toks("用" + "a" * 32 + "了"), ["a" * 32])
        self.assertEqual(toks("用" + "a" * 33 + "了"), [])

    def test_the_adjacent_marks(self):
        for s in ["我用@ok了", "看http://ok了", "用ok.了", "用a/b了", "看#tag了", "用x:了", "我的 ok@ 了"]:
            self.assertEqual(toks(s), [], s)
        self.assertEqual(toks("用www.google.com了"), [])                # every run of it touches a dot
        self.assertEqual(toks("用ok-了"), ["ok"])                       # a hyphen is not in the list

    def test_the_push_head(self):
        s = "推 someid 中文 ok 中文"
        self.assertEqual(toks(s, push=True), ["ok"])
        self.assertEqual(toks(s, push=True, push_head=False), ["someid", "ok"])   # without the rule the account is "English after 推"
        self.assertEqual(toks(s), ["someid", "ok"])                               # the rule is for push units only
        self.assertEqual(toks("→ abc123: 用 ok 了", push=True), ["ok"])
        self.assertEqual(toks("噓 x_y9:好 ok", push=True), ["ok"])
        self.assertEqual(toks("中文 ok 推 someid", push=True), ["ok", "someid"])   # only at the head of the line

    def test_left_text_and_context_end(self):
        s = "今天我用 ok 了"
        (a, _, _), = et.tokens(s)
        self.assertEqual(et.left_text(s, a), "今天我用")
        self.assertEqual(s[:et.text_end_before(s, a)], "今天我用")
        self.assertEqual(et.left_text("好，用ok", 3), "用")               # stops at anything that is not Han
        self.assertEqual(et.left_text("ok了", 0), "")
        long = "中" * 20 + "ok"
        self.assertEqual(et.left_text(long, 20), "中" * 16)

    def test_buckets(self):
        self.assertEqual([et.split_of(b) for b in range(10)], ["table"] * 8 + ["tune", "confirm"])
        self.assertEqual(et.bucket("forum", "b/a1"), et.doc_digest("forum", "b/a1")[0] % 10)
        self.assertNotEqual(et.doc_digest("forum", "x"), et.doc_digest("g0v", "x"))
        with self.assertRaises(ValueError):
            et.bucket("news", "x")
        seen = collections.Counter(et.bucket("pool", f"s{i}") for i in range(2000))
        self.assertEqual(set(seen), set(range(10)))


class Items(unittest.TestCase):
    def test_units_and_contexts(self):
        self.assertEqual(be.line_items("我用 ok 了", LEX, HIST), [("w", "我", "<s>"), ("w", "用", "我"), ("E", "ok", "用"), ("w", "了", "<s>")])
        # an excluded run and a digit run are no units, and no context reaches across them
        self.assertEqual(be.line_items("用a.b好2026的", LEX, HIST), [("w", "用", "<s>"), ("w", "好", "<s>"), ("w", "的", "<s>")])

    def test_a_held_out_run_takes_the_sentences_english_with_it(self):
        class G:
            def any(self, run):
                return "開會" in run
        items = be.line_items("今天開會，我用ok了", LEX, HIST, G())
        self.assertEqual(items, [("w", "我", "<s>"), ("w", "用", "我"), ("w", "了", "<s>")])


def corpus():
    """Three forum articles in the table's buckets, each with one push line `推 someid 中文 ok 中文`, one more with a rare token."""
    a = docs_in(be.TABLE_BUCKETS, 4)
    push = [(d, "推 someid 中文 ok 中文") for d in a[:3]] + [(a[3], "我用 rare 了")]
    return a, push


class Table(unittest.TestCase):
    def count(self, kind, items, **kw):
        return be.count_batch(kind, items, LEX, CONV, HIST, None, **kw)

    def test_the_account_of_a_push_is_not_english(self):
        _, push = corpus()
        tb = es.Table(be.render(self.count("forum-push", push), "# h"))
        self.assertIn("ok", tb.e)
        self.assertNotIn("someid", tb.e)
        self.assertEqual(tb.e["ok"], 3 * be.WEIGHTS["forum-push"])
        # the same lines read as body lines (no head rule): the account is there, which is what the rule keeps out
        self.assertIn("someid", es.Table(be.render(self.count("forum-body", push), "# h")).e)
        # and with the rule switched off in the token function the push table has it too
        saved = et.tokens
        et.tokens = lambda s, push=False: saved(s, push, push_head=False)
        try:
            self.assertIn("someid", es.Table(be.render(self.count("forum-push", push), "# h")).e)
        finally:
            et.tokens = saved

    def test_three_documents_or_none(self):
        _, push = corpus()
        tb = es.Table(be.render(self.count("forum-push", push), "# h"))
        self.assertNotIn("rare", tb.e)                                   # one document
        self.assertEqual(tb.a, 0)                                        # and it is not pruned mass either: it was never English
        two = [(d, s) for d, s in push[:2]] + [(push[1][0], "很好 ok")]   # three lines, two documents
        self.assertEqual(be.totals(self.count("forum-push", two))[1], set())
        # body and pushes of one article are one document
        d = docs_in(be.TABLE_BUCKETS, 3)
        c = self.count("forum-push", [(d[0], "用 ok 了"), (d[1], "用 ok 了")])
        c.merge(self.count("forum-body", [(d[0], "用 ok 了")]))
        self.assertEqual(be.totals(c)[1], set())
        c.merge(self.count("forum-body", [(d[2], "用 ok 了")]))
        self.assertEqual(be.totals(c)[1], {"ok"})

    def test_rows_by_hand(self):
        d = docs_in(be.TABLE_BUCKETS, 3, "g0v")
        c = self.count("g0v", [(d[0], "我用 ok 了"), (d[1], "我用ok"), (d[2], "ok 很好"), (d[2], "我用 speech 了")])
        tb = es.Table(be.render(c, "# h"))
        w = be.WEIGHTS["g0v"]
        # words: 我 用 了 | 我 用 | 很 好 | 我 用 了 = 10; ok x3; speech is in one document
        self.assertEqual((tb.n, tb.t, tb.a), (3 * w, 13 * w, 0))
        self.assertEqual(tb.d, {"<s>": (w, 7 * w), "用": (2 * w, 2 * w)})   # <s>: 我 x3, 了 x2 (behind English), 很, ok at the start; 用: ok x2
        self.assertEqual(tb.e, {"ok": 3 * w})
        self.assertEqual(tb.g3, {"^^o": 1, "^ok": 1, "ok$": 1})
        self.assertEqual(tb.l, {})

    def test_other_buckets_are_not_read(self):
        d = docs_in({8, 9}, 3, "g0v")
        c = self.count("g0v", [(x, "我用 ok 了") for x in d])
        self.assertEqual((c.words, len(c.pair)), (0, 0))

    def test_size_cut_and_pruned_mass(self):
        d = docs_in(be.TABLE_BUCKETS, 6, "g0v")
        items = [(x, "我用 ok 了") for x in d] + [(x, "我用 speech 了") for x in d[:3]] + [(x, "看 speeches 了") for x in d[:3]]
        c = self.count("g0v", items)
        w = be.WEIGHTS["g0v"]
        full = es.Table(be.render(c, "# h", 1))
        self.assertEqual((full.e, full.a), ({"ok": 6 * w, "speech": 3 * w, "speeches": 3 * w}, 0))
        cut = es.Table(be.render(c, "# h", 3 * w + 1))
        self.assertEqual((cut.e, cut.n, cut.a), ({"ok": 6 * w}, 6 * w, 6 * w))
        self.assertEqual(cut.t, full.t - 6 * w)                          # a token the cut removed is no unit
        self.assertEqual(cut.d["用"], (6 * w, 6 * w))                     # nor an English follower of its context
        self.assertEqual((cut.g3, cut.l), (full.g3, full.l))             # the letter model is counted before the cut
        self.assertEqual(full.l, {6: 1, 8: 1})
        self.assertTrue(cut.lp_word("speech") > es.NEG and full.lp_word("unseenword") == es.NEG)   # the pruned mass is what an unseen word gets
        text, k = be.build_text(c, lambda k: f"# k={k}")
        self.assertEqual((k, text), (1, be.render(c, "# k=1", 1)))
        saved = be.MAX_BYTES
        be.MAX_BYTES = len(be.render(c, "# k=20", 20).encode()) + 1
        try:
            self.assertEqual(be.build_text(c, lambda k: f"# k={k}")[1], 20)
            be.MAX_BYTES = 10
            self.assertEqual(be.build_text(c, lambda k: f"# k={k}"), (None, None))
        finally:
            be.MAX_BYTES = saved

    def test_sorted_and_the_same_twice_whatever_the_merge_order(self):
        d = docs_in(be.TABLE_BUCKETS, 5, "g0v")
        parts = [[(x, "我用 ok 了"), (x, "今天 meeting 很好")] for x in d]
        a, b = be.Counts(), be.Counts()
        for p in parts:
            a.merge(self.count("g0v", p))
        for p in reversed(parts):
            b.merge(self.count("g0v", p))
        text = be.render(a, "# h")
        self.assertEqual(text, be.render(b, "# h"))
        rows = text.splitlines()[1:]
        self.assertEqual(rows, sorted(rows, key=lambda r: r.encode()))

    def test_wiki_articles_go_through_the_sentence_pipeline(self):
        saved = dict(bc._W)
        title = doc_in(be.TABLE_BUCKETS, "wiki", "條目")
        try:
            c = be.count_batch("wiki", [(title, "{{模板|甲}}我用ok了。今天[[甲|meeting]]很好")], LEX, CONV, HIST, None)
        finally:
            bc._W.clear()
            bc._W.update(saved)
        self.assertEqual(sorted(c.pair), [("今天", "meeting"), ("用", "ok")])

    def test_review_list(self):
        d = docs_in(be.TABLE_BUCKETS, 3, "g0v")
        c = self.count("g0v", [(x, f"我用 w{chr(97 + i // 26)}{chr(97 + i % 26)} 了") for i in range(450) for x in d] + [(d[0], "用 ok 了")] * 9 + [(d[1], "用 ok"), (d[2], "用 ok")])
        got = be.review_list(c, 1)
        self.assertEqual((len(got), len(set(got)), got[0]), (400, 400, "ok"))
        self.assertEqual(got, be.review_list(c, 1))


class Probe(unittest.TestCase):
    def test_rows_come_only_from_buckets_8_and_9_and_never_meet_the_table(self):
        ids = [f"b/a{i}" for i in range(300)]
        items = [(d, f"推 someid 中文 tok{i} 中文") for i, d in enumerate(ids)]
        rows = bp.probe_batch("forum-push", items, CONV, None)
        by = {8: set(), 9: set()}
        for b, left, t in rows:
            self.assertEqual(left, "中文")
            self.assertNotEqual(t, "someid")
            by[b].add(int(t[3:]))
        self.assertTrue(by[8] and by[9])
        table = be.count_batch("forum-push", items, LEX, CONV, HIST, None)
        in_table = {int(e[3:]) for _, e in table.pair}
        self.assertEqual((in_table & by[8], in_table & by[9], by[8] & by[9]), (set(), set(), set()))
        self.assertEqual(in_table | by[8] | by[9], set(range(300)))
        self.assertEqual({et.bucket("forum", ids[i]) for i in by[8]}, {8})
        self.assertNotIn("someid", "".join(t for _, _, t in rows))

    def test_a_held_out_sentence_gives_no_row(self):
        class G:
            def any(self, run):
                return "開會" in run
        d = doc_in({8}, "g0v")
        self.assertEqual(bp.probe_batch("g0v", [(d, "今天開會用 ok 了"), (d, "我用 ok 了")], CONV, G()), [(8, "我用", "ok")])

    def test_sample(self):
        rows = list(range(12000))
        got = bp.sample(rows)
        self.assertEqual((len(got), got, bp.sample(rows[:4000])), (5000, sorted(set(got)), rows[:4000]))
        self.assertEqual(got, bp.sample(rows))

    def test_decision_point_rows(self):
        keys = "t1\t我用\t\tspeech_\nt2\t\t\tok_\nt3\t\t\tai_\nt4\t\t\tgo_\nt5\t\t\tabc3de_\n"
        out = ("t1\t0\tㄘㄣ\t\t0\t\t0\t0\t嵾\t6:4,1,0\n"            # Chinese at a decision point
               "t2\t0\t_punct_ok\t\t0\t2:c\t0\t0\tok\t\n"          # already English
               "t3\t0\tㄇㄛ\t\t0\t\t0\t0\t摸\t\n"                    # Chinese, typed in order: no decision point
               "t4\t0\t_punct_go\t\t0\t2:c\t0\t0\tgo\t\n"
               "t5\t0\tㄅˇ ㄎㄍ\t\t0\t\t0\t0\t某某\t3:1,0,0 6:1,0,0\n")  # two syllables, the last key at a decision point
        keep, multi = bp.decision_rows(keys, out)
        self.assertEqual((keep, multi), (["t1\t我用\t\tspeech_\n", "t5\t\t\tabc3de_\n"], 1))
        with self.assertRaises(ValueError):
            bp.decision_rows(keys, out.replace("t3\t", "x3\t"))
        with self.assertRaises(ValueError):
            bp.decision_rows(keys, out + "t6\n")
        # a decision point at an earlier key only is not the class
        self.assertEqual(bp.decision_rows("t1\t\t\tab3cd_\n", "t1\t0\tㄅˇ ㄎㄍ\t\t0\t\t0\t0\t某某\t2:1,0,0\n")[0], [])


class Select(unittest.TestCase):
    def test_rate(self):
        keys = "t1\t\t\tspeech_\nt2\t\t\tgoogle_\nt3\t\t\ttest_\nt4\t\t\tkdl_\n"
        out = ("t1\t0\t_punct_speech\t\t0\t6:e\t0\t0\tspeech\t6:4,1,0\n"
               "t2\t0\t_punct_google\t\t0\t6:e\t0\t0\tGoogle\t6:3,1,0\n"     # the unit's reading is what was typed
               "t3\t0\tㄔ\t\t0\t\t0\t0\t吃\t4:3,0,0\n"                        # Chinese
               "t4\t0\t_punct_kd ㄠ\t\t0\t\t0\t0\tkd凹\t\n")                  # not exactly one token
        self.assertEqual(sel.rate(keys, out), (4, 2))
        with self.assertRaises(ValueError):
            sel.rate(keys, out.replace("t2\t", "t9\t"))

    def test_feasible_set_and_tie_rule(self):
        grid = "\n".join(["# theta kappa kappa_dup rate neg1..4",
                          "1\t1\t0\t0.80\t0.6\t0.1\t0.1\t0.1",      # best rate, one negative run over 0.5: not feasible
                          "1\t1\t3\t0.70\t0.5\t0.5\t0.0\t0.0",      # exactly 0.5 is feasible
                          "2\t1\t0\t0.70\t0.0\t0.0\t0.0\t0.0",      # same rate, larger theta wins
                          "2\t1.5\t0\t0.70\t0.0\t0.0\t0.0\t0.0",    # same theta, larger kappa wins
                          "2\t1.5\t3\t0.70\t0.0\t0.0\t0.0\t0.0",    # same kappa, larger kappa_dup wins
                          "3\t2\t3\t0.60\t0.0\t0.0\t0.0\t0.0"])
        cells = sel.parse_grid(grid)
        self.assertEqual(len(sel.feasible(cells)), 5)
        self.assertEqual(sel.pick(cells)[:3], (2.0, 1.5, 3.0))
        self.assertEqual(sel.pick(cells[:1]), None)
        self.assertEqual(sel.pick(cells[:2])[:3], (1.0, 1.0, 3.0))
        self.assertEqual(sel.pick([cells[5], cells[4]])[:3], (2.0, 1.5, 3.0))   # the higher rate beats the larger theta
        with self.assertRaises(ValueError):
            sel.parse_grid("1\t1\t0\t0.8")


if __name__ == "__main__":
    unittest.main()
