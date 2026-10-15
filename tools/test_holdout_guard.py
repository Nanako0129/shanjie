"""holdout_guard.py and the counting scripts' --holdout-guard on synthetic data (docs/contracts/holdout-refresh.md section 8).
No OpenCC files, no real dump: a tiny fake conversion table stands in for build_counts.load_conv. The real MERGE fold and HAN are used."""
import bz2
import contextlib
import io
import os
import pickle
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import holdout_guard as g  # noqa: E402  (also puts experiments/s2, s7f-corpus, reference/proto on sys.path)
import build_counts as bc  # noqa: E402
import build_counts_text as bct  # noqa: E402

BOM = "﻿"
MARK = "秘密標記字串"
CONV = ({}, {"网": "網", "络": "路", "们": "們"}, 1)   # simplified -> traditional, three characters
fake_conv = lambda: CONV

# Counts the code BEFORE this change gave for the corpus below under CONV (build_counts_text at 04e9966, fake conversion table).
CORPUS = ["我们上网络", "臺北市政府今天宣布了", "大家都來吃飯吧", "今天天氣很好吧"]
BASE_UNI = {'我們': 1, '上': 1, '網路': 1, '臺北市政府': 1, '今天': 2, '宣布': 1, '了': 1, '大家': 1, '都來': 1, '吃飯': 1, '吧': 2, '天氣': 1, '很好': 1}
BASE_BI = {'<s> 我們': 1, '我們 上': 1, '上 網路': 1, '網路 </s>': 1, '<s> 臺北市政府': 1, '臺北市政府 今天': 1, '今天 宣布': 1, '宣布 了': 1,
           '了 </s>': 1, '<s> 大家': 1, '大家 都來': 1, '都來 吃飯': 1, '吃飯 吧': 1, '吧 </s>': 2, '<s> 今天': 1, '今天 天氣': 1,
           '天氣 很好': 1, '很好 吧': 1}
ARTICLE = "<text>{{x}}'''我们上网络'''，臺北市政府今天宣布了。大家都來吃飯吧</text>"
BASE_ART_RUNS, BASE_ART_UNI = 3, {'我們': 1, '上': 1, '網路': 1, '臺北市政府': 1, '今天': 1, '宣布': 1, '了': 1, '大家': 1, '都來': 1, '吃飯': 1, '吧': 1}


def run_main(args, conv=CONV):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = g.main(args, conv=conv)
        except SystemExit as e:
            rc = e.code
    return rc, out.getvalue(), err.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)

    def w(self, name, data):
        p = os.path.join(self.d.name, name)
        with open(p, "wb") as f:
            f.write(data if isinstance(data, bytes) else data.encode("utf-8"))
        return p

    def dump(self, pages):
        p = os.path.join(self.d.name, "d.xml.bz2")
        with bz2.open(p, "wt", encoding="utf-8") as f:
            f.write("".join(f"<page><ns>0</ns><text>{t}</text></page>\n" for t in pages))
        return p


class Guard(Base):
    def rows(self, text="a|今天天氣很好|x\n"):
        return self.w("rows.txt", text)

    def report(self, rows, **src):
        args = ["report", "--rows", rows]
        for k, v in src.items():
            args += ["--" + k.replace("_", "-"), *([v] if isinstance(v, str) else v)]
        return run_main(args)

    def test_containment_in_longer_run(self):
        rc, out, _ = self.report(self.rows(), text=self.w("t", "我覺得今天天氣很好吧\n無關的一行\n"))
        self.assertEqual(rc, 0)
        self.assertIn("\t2\t1\n", out)
        self.assertIn("rows.txt:1", out)
        self.assertIn("patterns\t1\t0\n", out)

    def test_wrapped_quoted_halfwidth_spaces(self):
        t = self.w("t", "「今天天氣很好」\n\"今天天氣很好\".\n(今天天氣很好)!\n  今天天氣很好  \n")
        _, out, _ = self.report(self.rows(), text=t)
        self.assertIn("\t4\t4\n", out)

    def test_match_only_after_conversion(self):
        _, out, _ = self.report(self.rows("a|上網路們好|x\n"), text=self.w("t", "上网络们好\n"))
        self.assertIn("\t1\t1\n", out)
        _, out, _ = self.report(self.rows("a|上网络们好|x\n"), text=self.w("t2", "上網路們好\n"))   # pattern's converted form
        self.assertIn("\t1\t1\n", out)

    def test_merge_fold_on_both_sides(self):
        # real build_counts.MERGE: 臺 -> 台 on the pattern and on the run
        _, out, _ = self.report(self.rows("a|臺北市政府今天宣布|x\n"), text=self.w("t", "台北市政府今天宣布了\n"))
        self.assertIn("\t1\t1\n", out)
        _, out, _ = self.report(self.rows("a|台北市政府今天宣布|x\n"), text=self.w("t2", "臺北市政府今天宣布了\n"))
        self.assertIn("\t1\t1\n", out)
        for a, b in (("佔", "占"), ("佈", "布"), ("祕", "秘"), ("牀", "床"), ("汙", "污")):
            self.assertEqual(bc.fold(a), bc.fold(b), a)

    def test_short_patterns_skipped_and_counted(self):
        _, out, _ = self.report(self.rows("a|今天天氣很好|x\nb|天氣|x\nc|好|x\n"), text=self.w("t", "天氣好\n"))
        self.assertIn("patterns\t1\t2\n", out)
        self.assertIn("\t1\t0\n", out)

    def test_zero_patterns_is_an_error(self):
        for body in ("", "# only a comment\n", "a||x\n", "a|天氣|x\n"):
            rc, out, err = self.report(self.rows(body), text=self.w("t", "x\n"))
            self.assertEqual(rc, 2, body)
            self.assertIn("0 patterns loaded", err)

    def test_rows_parsing(self):
        p = self.w("r", BOM + "# c\r\n\r\nx|今天天氣很好|y\r\n  \nz|另一句話啊|q\n")
        self.assertEqual(list(g.parse_rows(p, strict=True)), [(3, "今天天氣很好"), (5, "另一句話啊")])
        for bad in ("a|b\n", "a|b|c|d\n", "nobar\n"):
            with self.assertRaises(g.GuardError):
                list(g.parse_rows(self.w("bad", bad), strict=True))

    def test_bom_only_on_first_line(self):
        self.assertEqual(list(g.parse_rows(self.w("r", f"{BOM}# comment\nx|今天天氣很好|y\n"), strict=True)), [(2, "今天天氣很好")])
        with self.assertRaises(g.GuardError):   # a BOM at the start of a later line is not stripped, so it is no comment
            list(g.parse_rows(self.w("r2", f"x|今天天氣很好|y\n{BOM}# comment\n"), strict=True))

    def test_non_han_pattern_is_an_error(self):
        rc, out, err = self.report(self.rows(f"a|{MARK}〇|x\n"), text=self.w("t", "x\n"))
        self.assertEqual(rc, 2)
        self.assertIn("rows.txt:1: 3 field(s), sentence is not all Han", err)
        self.assertNotIn(MARK, out + err)

    def test_line_break_characters_in_rows(self):
        for ch in ("\r", " ", " ", "\u0085"):
            p = self.w("r", f"x|今天天氣很好|y{ch}z\n")
            with self.assertRaises(g.GuardError, msg=repr(ch)):
                list(g.parse_rows(p, strict=True))
        # line numbers count '\n' only
        p = self.w("r", "# a\r\nx|今天天氣很好|y\r\n")
        self.assertEqual(list(g.parse_rows(p, strict=True)), [(2, "今天天氣很好")])

    def test_row_files_two_and_three_fields(self):
        p = self.w("r", "前|今天天氣很好\n前|另一句|甲|乙|丙\n")
        self.assertEqual(list(g.parse_rows(p, strict=False)), [(1, "今天天氣很好"), (2, "另一句")])
        with self.assertRaises(g.GuardError):
            list(g.parse_rows(self.w("bad", "nobar\n"), strict=False))
        _, out, _ = self.report(self.rows(), row_files=p)
        self.assertIn("\t2\t1\n", out)

    def test_error_carries_no_text(self):
        bad = self.w("bad.txt", f"{MARK}|{MARK}\n")
        rc, out, err = self.report(bad, text=self.w("t", "x\n"))
        self.assertEqual(rc, 2)
        self.assertNotIn(MARK, out + err)
        self.assertIn("bad.txt:1", err)
        self.assertIn("2 field", err)

    def test_text_bz2_tsv_and_invalid_utf8(self):
        raw = "id\t無關\t我覺得今天天氣很好吧\n".encode() + b"\xff\xfe\t\xc3\n"
        t = os.path.join(self.d.name, "t.tsv.bz2")
        with bz2.open(t, "wb") as f:
            f.write(raw)
        rc, out, _ = self.report(self.rows(), text=t)
        self.assertEqual(rc, 0)
        self.assertIn("\t2\t1\n", out)
        plain = self.w("p", b"\xff\xfe \xe4\xbb\x8a\xe5\xa4\xa9\xe5\xa4\xa9\xe6\xb0\xa3\xe5\xbe\x88\xe5\xa5\xbd \xc3\n\x80\n")
        rc, out, _ = self.report(self.rows(), text=plain)
        self.assertEqual(rc, 0)
        self.assertIn("\t2\t1\n", out)
        rc, _, _ = self.report(self.w("r2", b"a|\xff\xfe|x\n"), text=plain)   # invalid bytes in a rows file: an error, not a crash
        self.assertEqual(rc, 2)

    def test_report_prints_only_numbers_and_row_ids(self):
        t = self.w("t", "今天天氣很好\n")
        _, out, _ = self.report(self.rows(), text=t)
        lines = [l.split("\t") for l in out.splitlines()]
        self.assertEqual(lines[0], ["patterns", "1", "0"])
        self.assertEqual(lines[1][0], t)
        self.assertTrue(all(x.isdigit() for x in lines[1][1:]))
        self.assertEqual(lines[2][0], "rows")
        self.assertRegex(lines[2][1], r"rows\.txt:\d+$")
        self.assertNotIn("今天", out)

    def test_wiki_requires_articles(self):
        rc, _, err = self.report(self.rows(), wiki=self.dump(["x"]))
        self.assertEqual(rc, 2)

    def wiki_args(self, dump, **kw):
        return ["report", "--rows", self.rows(), "--wiki", dump, "--procs", "2"] + [x for k, v in kw.items() for x in ("--" + k, str(v))]

    def test_wiki_pool_and_skip(self):
        before = bc.DUMP
        dump = self.dump(["{{tpl}}我說'''今天天氣很好'''，沒錯。", "別的條目", "[[X|今天天氣很好]]啊"])
        _, out, _ = run_main(self.wiki_args(dump, articles=2))
        self.assertIn("wiki\t2\t1\n", out)
        _, out, _ = run_main(self.wiki_args(dump, articles=1, skip=2))
        self.assertIn("wiki\t1\t1\n", out)
        self.assertEqual(bc.DUMP, before)   # never reassigned: the path is passed to articles()

    def test_wiki_spawn_start_method(self):
        dump = self.dump(["我說今天天氣很好吧", "別的條目"])
        with mock.patch.object(g, "START", "spawn"):
            rc, out, err = run_main(self.wiki_args(dump, articles=2))
        self.assertEqual(rc, 0, err)
        self.assertIn("wiki\t2\t1\n", out)


class Counting(Base):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(bc, "load_conv", fake_conv); p.start(); self.addCleanup(p.stop)
        self.rows = self.w("rows.txt", "a|我們上網路|x\nb|臺北市政府今天宣布|y\nc|短句|z\n")
        self.text = self.w("in.txt", "\n".join(CORPUS) + "\n")

    def count_text(self, *flag):
        out = os.path.join(self.d.name, "o%d.pkl" % len(os.listdir(self.d.name)))
        with contextlib.redirect_stdout(io.StringIO()) as so:
            bct.main([out, self.text, *flag])
        return pickle.load(open(out, "rb")), so.getvalue()

    @staticmethod
    def flat(c):
        return {" ".join(k) if isinstance(k, tuple) else k: v for k, v in c.items()}

    def test_off_equals_the_code_before_this_change(self):
        d, so = self.count_text()
        self.assertEqual((d["runs"], dict(d["uni"]), self.flat(d["bi"])), (4, BASE_UNI, BASE_BI))
        self.assertNotIn("holdout-guard", so)
        bc._init(False, False)
        r = bc.count_batch([ARTICLE])
        self.assertEqual((len(r), r[3], dict(r[0])), (4, BASE_ART_RUNS, BASE_ART_UNI))

    def test_on_skips_matching_runs_in_text(self):
        base, _ = self.count_text()
        on, so = self.count_text("--holdout-guard", self.rows)
        self.assertEqual(base["runs"] - on["runs"], 2)   # line 1 after conversion, line 2 through the 臺/台 fold
        self.assertIn("holdout-guard: 2 rows loaded, 1 skipped as shorter than 4 characters", so)
        self.assertIn("holdout-guard: skipped 2 runs", so)
        none = self.w("none.txt", "a|完全不相干的句子|x\n")
        same, _ = self.count_text("--holdout-guard", none)
        self.assertEqual((same["runs"], dict(same["uni"])), (base["runs"], dict(base["uni"])))

    def test_bad_rows_stop_before_any_pool_and_carry_no_text(self):
        bad = self.w("bad.txt", f"{MARK}|{MARK}\n")
        for run in (lambda: self.count_text("--holdout-guard", bad), lambda: self.main_counts(bad)):
            err, out = io.StringIO(), io.StringIO()
            with mock.patch("multiprocessing.Pool", side_effect=AssertionError("Pool created")), \
                    contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
                with self.assertRaises(SystemExit) as cm:
                    run()
            msg = str(cm.exception.code)
            self.assertNotEqual(cm.exception.code, 0)
            self.assertIn("bad.txt:1", msg)
            self.assertNotIn(MARK, msg + err.getvalue() + out.getvalue())

    def main_counts(self, *guard, articles=3):
        dump = self.dump(["我们上网络", "臺北市政府今天宣布了", "大家都來吃飯吧"])
        argv = ["build_counts.py", "--articles", str(articles), "--procs", "2"] + (["--holdout-guard", *guard] if guard else [])
        out = io.StringIO()
        with mock.patch.object(bc, "DUMP", dump), mock.patch.object(bc, "OUT", self.d.name), \
                mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(out):
            bc.main()
        return out.getvalue()

    def test_build_counts_main_with_pool(self):
        off = self.main_counts()
        base = pickle.load(open(os.path.join(self.d.name, "counts-3.pkl"), "rb"))
        self.assertNotIn("holdout-guard", off)
        self.assertEqual(base["runs"], 3)
        on = self.main_counts(self.rows)
        got = pickle.load(open(os.path.join(self.d.name, "counts-3.pkl"), "rb"))
        self.assertIn("holdout-guard: 2 rows loaded, 1 skipped as shorter than 4 characters", on)
        self.assertIn("holdout-guard: skipped 2 runs", on)
        self.assertEqual(got["runs"], 1)
        self.assertEqual(dict(got["uni"]), {"大家": 1, "都來": 1, "吃飯": 1, "吧": 1})


if __name__ == "__main__":
    unittest.main()
