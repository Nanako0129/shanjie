"""Unit tests for tools/build_english_next.py on synthetic inputs (docs/contracts/english-next.md section 1, revision 3). No real data, no network.
Run: python3 -W ignore -m unittest tools/test_build_english_next.py"""
import bz2
import gzip
import hashlib
import itertools
import os
import sys
import tempfile
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_english_next as B  # noqa: E402

WORDS = {"是要對決", "是", "要", "對", "決", "好", "笑死"}
MAXLEN = max(map(len, WORDS))
LOOSE = dict(min_events=1, min_boards=1, min_units=1, min_w=1)


def ids(bucket, n):
    """n article IDs that are (bucket True) or are not in the 7/8 build bucket."""
    return list(itertools.islice((i for i in (f"M.{k}.A.T" for k in itertools.count()) if B.in_build_bucket(i) == bucket), n))


def page(body="", pushes=()):
    p = "".join(f'<div class="push"><span class="hl push-tag">推 </span><span class="f3 hl push-userid">{u}</span>'
                f'<span class="f3 push-content">: {c}</span><span class="push-ipdatetime"> 10/25 12:00\n</span></div>' for u, c in pushes)
    return ('<div id="main-content" class="bbs-screen bbs-content">'
            f'{body}\n--\n簽名 Joe是簽名\n<span class="f2">※ 發信站: 批踢踢實業坊(ptt.cc)</span>{p}</div>')


def rows(text):
    return [l.split("\t") for l in text.splitlines() if not l.startswith("#")]


def es(text):
    return {x[1]: int(x[2]) for x in rows(text) if x[0] == "E"}


def ws(text):
    return {(x[1], x[2]): int(x[3]) for x in rows(text) if x[0] == "W"}


class Pairs(unittest.TestCase):
    def p(self, text):
        return B.pairs(text, WORDS, MAXLEN)

    def test_longest_first_word_and_one_space(self):
        self.assertEqual(self.p("Joe是要對決"), [("joe", "是要對決")])
        self.assertEqual(self.p("Joe是要對，好"), [("joe", "是")])     # the run ends at the comma; 是要對 is no word, so the longest prefix that is one is 是
        self.assertEqual(self.p("Joe 是要對決"), [("joe", "是要對決")])
        self.assertEqual(self.p("Joe  是要對決"), [])                 # two spaces
        self.assertEqual(self.p("Joe\t是"), [])

    def test_case_is_folded(self):
        self.assertEqual(self.p("JOE好 joe好 Joe好"), [("joe", "好")] * 3)

    def test_a_run_with_no_lexicon_word_still_counts_as_e_before_chinese(self):
        self.assertEqual(self.p("Joe囧"), [("joe", None)])

    def test_the_letters_must_stand_alone(self):
        for t in ("J是", "Joe2是", "2Joe是", "ab3cd是", "x" * 33 + "是", "Joe's是", "Joe is here", "Joe 123"):
            self.assertEqual(self.p(t), [], t)
        self.assertEqual(self.p("x" * 32 + "是"), [("x" * 32, "是")])
        self.assertEqual(self.p("是Joe是"), [("joe", "是")])          # Han in front is fine

    def test_urls_are_cut_out_and_act_as_a_boundary(self):
        self.assertEqual(self.p("https://joe.example.com/abc是"), [])
        self.assertEqual(self.p("Joe https://x.example.com 是"), [])
        self.assertEqual(self.p("看 http://x.example.com/a Joe是"), [("joe", "是")])

    def test_subtitle_steps(self):
        self.assertEqual(B.sub_sentences("- Joe是要對決。"), ["Joe是要對決"])          # one English word in a mixed line: kept
        self.assertEqual(B.sub_sentences("你好，Joe is here"), [])                       # a parallel line (two English words): dropped
        self.assertEqual(B.sub_sentences("- 我是 Joe。 - 好"), ["我是Joe", "好"])   # sentences() tidies the space away


class Build(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.ptt = os.path.join(self.dir, "ptt")
        self.lex = self.lexicon_file()

    def tearDown(self):
        B._G.pop("sentences", None)
        self.tmp.cleanup()

    def lexicon_file(self, words=WORDS):
        f = os.path.join(self.dir, "lex.tsv")
        with open(f, "w", encoding="utf-8") as fh:
            fh.write("# header\n_ctrl_punctuation_! ！ 0.0\n")
            fh.writelines(f"ㄅ\t{w}\t-3.0\tx\n" for w in sorted(words))
        return f

    def corpus(self, files):
        """files: {(board, id): page}"""
        for (board, aid), text in files.items():
            os.makedirs(os.path.join(self.ptt, board), exist_ok=True)
            with open(os.path.join(self.ptt, board, aid + ".html"), "w", encoding="utf-8") as fh:
                fh.write(text)

    def text_file(self, name, lines):
        f = os.path.join(self.dir, name)
        with (gzip.open(f, "wt", encoding="utf-8") if name.endswith(".gz") else open(f, "w", encoding="utf-8")) as fh:
            fh.write("\n".join(lines) + "\n")
        return f

    def build(self, ptt=None, kw=None, **src):
        if ptt:
            self.corpus(ptt)
        if os.path.isdir(self.ptt):
            src["ptt"] = self.ptt
        return B.build([self.lex], src, **{**LOOSE, **(kw or {})})

    def test_ptt_quote_lines_signature_the_hash_bucket_and_weights(self):
        a, b = ids(True, 2)
        out, rep = self.build({
            ("Gossiping", a): page("Joe好\n: Joe笑死\n※ 引述《x》之銘言：\nJoe 是\n"),
            ("Gossiping", b): page("無關", [("u1", "Joe好")]),                    # a push counts x5
            ("Gossiping", ids(False, 1)[0]): page("Joe好 Joe好 Joe好"),            # the 1/8 bucket is not counted
        })
        self.assertEqual(es(out), {"joe": 1 + 1 + 5})                              # two body events x1 (好, 是; the quote and signature are gone), one push x5
        self.assertEqual(ws(out), {("joe", "好"): 1 + 5, ("joe", "是"): 1})
        self.assertEqual(rep["events_by_source"], {"ptt-body": 2, "ptt-push": 1})

    def test_the_bucket_split_is_the_first_byte_of_sha256_mod_8(self):
        i = "M.1700000000.A.ABC"
        self.assertEqual(B.in_build_bucket(i), hashlib.sha256(i.encode()).digest()[0] % 8 != 0)
        self.assertGreater(sum(B.in_build_bucket(f"M.{k}.A.T") for k in range(4000)) / 4000, 0.8)

    def test_weights_of_every_source(self):
        a = ids(True, 1)[0]
        B._G["sentences"] = lambda raw: raw.split("。")
        dump = os.path.join(self.dir, "wiki.xml.bz2")
        with bz2.open(dump, "wt", encoding="utf-8") as f:
            f.write("<page><ns>0</ns><text xml:space=\"preserve\">Joe好。Joe好</text></page>\n")
            f.write("<page><ns>1</ns><text>Joe好</text></page>\n")                  # not an article
            f.write("<page><ns>0</ns><text>#REDIRECT Joe好</text></page>\n")
        out, rep = self.build(
            {("B", a): page("Joe好", [("u", "Joe好")])},                             # body x1 + push x5
            wiki=dump, colloquial=self.text_file("c.txt", ["Joe好", "", "沒有"]),     # x5
            g0v=self.text_file("g.txt", ["Joe好", "Joe好"]),                         # x5 each
            subs=self.text_file("s.txt.gz", ["Joe好", "", "Joe好"]))                 # x1 each, two films
        self.assertEqual(rep["events_by_source"], {"wiki": 2, "colloquial": 1, "g0v": 2, "ptt-body": 1, "ptt-push": 1, "subs": 2})
        self.assertEqual(es(out), {"joe": 2 * 1 + 1 * 5 + 2 * 5 + 1 + 5 + 2 * 1})
        self.assertEqual(ws(out)[("joe", "好")], es(out)["joe"])
        self.assertEqual(B.WEIGHTS, {"wiki": 1, "colloquial": 5, "g0v": 5, "ptt-push": 5, "ptt-body": 1, "subs": 1})

    def test_wiki_limit(self):
        B._G["sentences"] = lambda raw: [raw]
        dump = os.path.join(self.dir, "wiki.xml.bz2")
        with bz2.open(dump, "wt", encoding="utf-8") as f:
            f.writelines("<page><ns>0</ns><text>Joe好</text></page>\n" for _ in range(5))
        out, _ = self.build(wiki=dump)
        self.assertEqual(es(out), {"joe": 5})
        out, _ = B.build([self.lex], {"wiki": dump, "wiki_limit": 2}, **LOOSE)
        self.assertEqual(es(out), {"joe": 2})

    def test_subtitles_one_english_word_counts_and_a_parallel_line_does_not(self):
        s = self.text_file("s.txt.gz", ["- Joe是要對決。", "你好，Joe is here 好", "", "Amy好"])
        out, rep = self.build(subs=s)
        self.assertEqual(es(out), {"amy": 1, "joe": 1})
        self.assertEqual(rep["films"], 2)

    def test_a_single_subtitle_segment_is_a_stop_and_a_raw_zip_reads_as_films(self):
        s = self.text_file("s.txt.gz", ["Joe好", "Joe好", "Joe好"])
        with self.assertRaises(SystemExit):
            self.build(subs=s)
        z = os.path.join(self.dir, "zh_tw.zip")
        doc = '<?xml version="1.0"?>\n<document id="1">\n  <s id="1">\n    <time id="T1S" value="00:00:01,000" />\nJoe好\n  </s>\n</document>\n'
        with zipfile.ZipFile(z, "w") as f:
            for i in (1, 2, 3):
                f.writestr(f"OpenSubtitles/raw/zh_tw/2000/{i}/{i}.xml", doc)
        out, rep = self.build(subs=z, kw=dict(min_units=3))
        self.assertEqual((rep["films"], es(out)), (3, {"joe": 3}))
        s2 = self.text_file("s2.txt.gz", ["Joe好", "", "Joe好", "", "Joe好"])
        out, rep = self.build(subs=s2, kw=dict(min_units=3))
        self.assertEqual(es(out), {"joe": 3})                                        # three films, three units
        out, _ = self.build(subs=s2, kw=dict(min_units=4))
        self.assertEqual(es(out), {})

    def test_the_two_spread_routes(self):
        a = ids(True, 4)
        ptt = {("B1", a[0]): page("Joe好"), ("B2", a[1]): page("Joe好"), ("B3", a[2]): page("Joe好"), ("B1", a[3]): page("Amy好")}
        out, _ = self.build(ptt, kw=dict(min_boards=3, min_units=99))
        self.assertEqual(set(es(out)), {"joe"})                                      # joe: 3 boards; amy: 1
        out, _ = self.build(kw=dict(min_boards=4, min_units=99))
        self.assertEqual(set(es(out)), set())
        g = self.text_file("g.txt", ["Amy好", "Amy好", "Zed好"])
        out, _ = B.build([self.lex], {"g0v": g}, **{**LOOSE, "min_boards": 99, "min_units": 2})
        self.assertEqual(set(es(out)), {"amy"})                                      # amy: 2 messages; zed: 1
        out, _ = B.build([self.lex], {"ptt": self.ptt, "g0v": g}, **{**LOOSE, "min_boards": 3, "min_units": 2})
        self.assertEqual(set(es(out)), {"joe", "amy"})                               # either route
        # a unit with e twice counts once
        g2 = self.text_file("g2.txt", ["Amy好 Amy好"])
        out, _ = B.build([self.lex], {"g0v": g2}, **{**LOOSE, "min_boards": 99, "min_units": 2})
        self.assertEqual(set(es(out)), set())

    def test_an_e_with_many_occurrences_but_few_events_before_chinese_is_not_admitted(self):
        lines = ["Joe好"] * 19 + ["Joe is here", "Joe 123", "Joe."] * 20
        out, _ = self.build(g0v=self.text_file("g.txt", lines), kw=dict(min_events=20))
        self.assertEqual(es(out), {})
        out, _ = self.build(g0v=self.text_file("g.txt", lines + ["Joe好"]), kw=dict(min_events=20))
        self.assertEqual(es(out), {"joe": 20 * 5})
        self.assertEqual((B.MIN_EVENTS, B.MIN_BOARDS, B.MIN_UNITS, B.MIN_W, B.MAX_W, B.ACCOUNT_N), (20, 5, 20, 3, 20, 100))

    def test_w_needs_unweighted_events(self):
        g = self.text_file("g.txt", ["Joe好", "Joe好", "Joe好", "Joe笑死"])           # 好 3 events (weight 15), 笑死 1 (weight 5)
        out, _ = self.build(g0v=g, kw=dict(min_w=3))
        self.assertEqual(ws(out), {("joe", "好"): 15})
        out, _ = self.build(g0v=g, kw=dict(min_w=1))
        self.assertEqual(ws(out), {("joe", "好"): 15, ("joe", "笑死"): 5})

    def test_a_push_account_that_passes_through_ptt_alone_needs_events_at_the_account_threshold(self):
        a = ids(True, 2)
        ptt = {("B", a[0]): page("Joe好 Joe好 Joe好 Zed好", [("Joe", "同意"), ("amy", "ok")]), ("B", a[1]): page("Amy好 Amy好 Amy好 Amy好"),
               # an account seen only in an article of the 1/8 bucket still blocks
               ("B", ids(False, 1)[0]): page("", [("zed", "x")])}
        out, rep = self.build(ptt, kw=dict(account_n=4))
        self.assertEqual(set(es(out)), {"amy"})                                      # amy has 4 events >= 4; joe 3 and zed 1 are dropped
        self.assertEqual(rep["blocked_by_account"], 2)
        out, rep = self.build(kw=dict(account_n=3))
        self.assertEqual(set(es(out)), {"amy", "joe"})
        self.assertEqual(rep["blocked_by_account"], 1)
        # an e that also passes through the other sources' units is kept at any count
        g = self.text_file("g.txt", ["Joe好"])
        out, rep = self.build(g0v=g, kw=dict(account_n=100))
        self.assertEqual(set(es(out)), {"joe"})                                      # amy and zed pass by PTT alone and have < 100 events
        self.assertEqual(rep["blocked_by_account"], 2)

    def test_at_most_20_w_per_e_by_weighted_count_then_bytes(self):
        words = [f"字{chr(0x4e00 + i)}" for i in range(26)]
        self.lex = self.lexicon_file(words)
        body = " ".join(f"Joe{w}" for w in words) + f" Joe{words[25]}" * 2              # every w once, the last one three times
        out, _ = self.build(g0v=self.text_file("g.txt", [body]))
        w = ws(out)
        self.assertEqual(len(w), 20)
        self.assertEqual({k[1] for k in w}, {words[25]} | set(words[:19]))              # the count wins, then bytes cut the rest
        self.assertEqual(w[("joe", words[25])], 3 * 5)

    def test_an_e_with_no_surviving_w_has_no_rows(self):
        g = self.text_file("g.txt", ["Joe好"] * 3 + ["Amy好"] * 2 + ["Zed囧"] * 3)       # amy: w below the cut; zed: no lexicon word at all
        out, rep = self.build(g0v=g, kw=dict(min_w=3))
        self.assertEqual(es(out), {"joe": 15})
        self.assertEqual((rep["e"], rep["e_written"]), (3, 1))
        out, rep = self.build(g0v=g, kw=dict(min_w=2))
        self.assertEqual(set(es(out)), {"joe", "amy"})

    def test_a_lone_carriage_return_is_not_a_line_break(self):
        g = os.path.join(self.dir, "g.txt")
        with open(g, "wb") as f:
            f.write("Amy好\rAmy好\nAmy好\r\n".encode())
        self.assertEqual(list(B.lines_of(g)), ["Amy好\rAmy好", "Amy好"])
        out, rep = self.build(g0v=g, kw=dict(min_units=3))
        self.assertEqual(es(out), {})                                              # 2 messages, 3 events: the \r\n line is one message, the lone \r joins
        out, rep = self.build(g0v=g, kw=dict(min_units=2))
        self.assertEqual(es(out), {"amy": 15})

    def test_size_fallback_raises_the_w_cut_and_stops_when_nothing_fits(self):
        g = self.text_file("g.txt", ["Joe好"] * 20 + ["Joe要"] * 6 + ["Joe對"] * 3)
        out, rep = self.build(g0v=g)
        self.assertEqual((rep["min_w"], rep["rows_w"]), (1, 3))
        small = len(out.encode()) - 1
        out, rep = self.build(g0v=g, kw=dict(max_bytes=small))
        self.assertEqual((rep["min_w"], rep["rows_w"]), (5, 2))                          # 1 does not fit; 3 is not a step; 5 drops 對
        self.assertEqual({k[1] for k in ws(out)}, {"好", "要"})
        out, rep = self.build(g0v=g, kw=dict(max_bytes=10))
        self.assertIsNone(out)

    def test_header_output_order_and_jobs(self):
        a = ids(True, 3)
        ptt = {("B", a[0]): page("Joe好 Amy好"), ("C", a[1]): page("Joe要 Amy要"), ("B", a[2]): page("Zed好")}
        g = self.text_file("g.txt", ["Joe好", "Amy要"])
        s = self.text_file("s.txt.gz", ["Joe好", "", "Amy好"])
        out, _ = self.build(ptt, g0v=g, subs=s)
        head = [l for l in out.splitlines() if l.startswith("#")]
        self.assertTrue(head[0].startswith("# english-next v"))
        self.assertIn(f"# source g0v g.txt sha256 {hashlib.sha256(open(g, 'rb').read()).hexdigest()}", head)
        self.assertIn(f"# source subs s.txt.gz sha256 {B.sha256_file(s)}", head)
        tree = "".join(sorted(f"{b}/{i}.html\0{hashlib.sha256(open(os.path.join(self.ptt, b, i + '.html'), 'rb').read()).hexdigest()}\n" for (b, i) in ptt))
        self.assertIn(f"# source ptt sha256 {hashlib.sha256(tree.encode()).hexdigest()} articles 3", head)
        self.assertTrue(any(h.startswith("# lexicon lex.tsv sha256 ") for h in head))
        body = [l for l in out.splitlines() if not l.startswith("#")]
        self.assertEqual(body, sorted(body, key=lambda l: l.encode()))
        self.assertTrue(out.endswith("\n"))
        out2, _ = B.build([self.lex], {"ptt": self.ptt, "g0v": g, "subs": s}, jobs=2, **LOOSE)
        self.assertEqual(out2, out)

    def test_g0v_ts_format_is_one_message_per_ts_and_a_plain_file_still_works(self):
        g = self.text_file("g.txt", ["1\tAmy好", "1\tAmy好", "2\tAmy要", "2\t沒有", "3\tZed好"])
        out, rep = B.build([self.lex], {"g0v": g}, **{**LOOSE, "min_boards": 99, "min_units": 2})
        self.assertEqual(set(es(out)), {"amy"})                                      # amy: 2 messages (ts 1 and 2), not 3 lines; zed: 1
        self.assertEqual(rep["events_by_source"], {"g0v": 4})
        self.assertEqual(es(out), {"amy": 15})
        g2 = self.text_file("g2.txt", ["Amy好", "Amy好", "Zed好"])                    # no tab: one message per line, as before
        out, _ = B.build([self.lex], {"g0v": g2}, **{**LOOSE, "min_boards": 99, "min_units": 2})
        self.assertEqual(set(es(out)), {"amy"})

    def test_a_raw_zip_gives_the_same_table_through_the_workers_as_through_the_feeder(self):
        z = os.path.join(self.dir, "zh_tw.zip")
        doc = '<?xml version="1.0"?>\n<document id="1">\n<s id="1"><time id="T1S" value="00:00:01,000" />Joe好 &amp; Amy要</s>\n<s id="2">Joe是要對決</s>\n</document>\n'
        with zipfile.ZipFile(z, "w") as f:
            for i in range(1, 40):
                f.writestr(f"OpenSubtitles/raw/zh_tw/2000/{i}/{i}a.xml", doc)
                f.writestr(f"OpenSubtitles/raw/zh_tw/2000/{i}/{i}b.xml", doc.replace("Amy", "Zed"))
            f.writestr("OpenSubtitles/raw/zh_tw/2000/99/99.xml", "<document></document>")      # a film with no sentence
        import clean_subs as CS
        flat = os.path.join(self.dir, "flat.txt")
        open(flat, "w", encoding="utf-8").write("\n".join(CS.read_lines(z)) + "\n")
        src = lambda p: B.build([self.lex], {"subs": p}, jobs=2, **LOOSE)
        (a, ra), (b, rb) = src(z), src(flat)
        drop = lambda t: "".join(l for l in t.splitlines(True) if not l.startswith("# source subs"))
        self.assertEqual(drop(a), drop(b))
        self.assertEqual((ra["films"], rb["films"], ra["events_by_source"]), (39, 39, rb["events_by_source"]))
        self.assertGreater(es(a)["joe"], 39)

    def test_report_e_names_an_e_that_is_not_in_the_table(self):
        g = self.text_file("g.txt", ["Joe是要對決", "Joe好", "Joe好"])
        _, rep = B.build([self.lex], {"g0v": g}, **{**LOOSE, "min_events": 99}, report_e=["Joe:是要對決", "nobody"])
        l = rep["report_e"]
        self.assertEqual(l[0], "joe\tevents 3\tN 15\tptt_boards 0\tunits {'g0v': 3}\tblocked_by_account 0\tin_table 0")
        self.assertEqual(l[1:3], ["joe\t好\tevents 2\tN 10", "joe\t是要對決\tevents 1\tN 5"])
        self.assertTrue(l[3].startswith("nobody\tevents 0\tN 0"))


class Words(unittest.TestCase):
    def test_load_words_reads_both_formats_and_skips_comments_and_control_rows(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "a.txt")
            open(f, "w", encoding="utf-8").write("# x\n_ctrl_ ！ 0.0\nㄅ 好 -1.0\nㄆ\t笑死\t-2\tsrc\n")
            self.assertEqual(B.load_words([f]), {"好", "笑死"})


if __name__ == "__main__":
    unittest.main()
