"""holdout_guard.py and the counting scripts' --holdout-guard on synthetic data (docs/contracts/holdout-refresh.md section 8).
No OpenCC files, no dump: a tiny fake conversion table stands in for build_counts.convert."""
import bz2
import contextlib
import io
import multiprocessing as mp
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

MARK = "秘密標記字串"
FAKE = str.maketrans({"网": "網", "络": "路", "们": "們"})   # simplified -> traditional, three characters
conv = lambda s: s.translate(FAKE)
fake_conv = lambda: ({}, {"网": "網", "络": "路", "们": "們"}, 1)


def run_main(args, c=conv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = g.main(args, conv=c)
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

    def test_wrapped_quoted_halfwidth_spaces(self):
        t = self.w("t", "「今天天氣很好」\n\"今天天氣很好\".\n(今天天氣很好)!\n  今天天氣很好  \n")
        _, out, _ = self.report(self.rows(), text=t)
        self.assertIn("\t4\t4\n", out)

    def test_match_only_after_conversion(self):
        # traditional pattern, simplified text: matches only after the text is converted
        _, out, _ = self.report(self.rows("a|上網路們|x\n"), text=self.w("t", "上网络们\n"))
        self.assertIn("\t1\t1\n", out)
        # simplified pattern, traditional text: matches through the pattern's converted form
        _, out, _ = self.report(self.rows("a|上网络们|x\n"), text=self.w("t2", "上網路們\n"))
        self.assertIn("\t1\t1\n", out)

    def test_rows_parsing(self):
        p = self.w("r", "﻿# c\r\n\r\nx|今天天氣很好|y\r\n  \nz|另一句話啊|q\n")
        self.assertEqual(g.parse_rows(p, strict=True), [(3, "今天天氣很好"), (5, "另一句話啊")])
        for bad in ("a|b\n", "a|b|c|d\n", "nobar\n"):
            with self.assertRaises(g.GuardError):
                g.parse_rows(self.w("bad", bad), strict=True)

    def test_row_files_two_and_three_fields(self):
        p = self.w("r", "前|今天天氣很好\n前|另一句|甲|乙|丙\n")
        self.assertEqual(g.parse_rows(p, strict=False), [(1, "今天天氣很好"), (2, "另一句")])
        with self.assertRaises(g.GuardError):
            g.parse_rows(self.w("bad", "nobar\n"), strict=False)
        _, out, _ = self.report(self.rows(), row_files=p)
        self.assertIn("\t2\t1\n", out)

    def test_error_carries_no_text(self):
        bad = self.w("bad.txt", f"{MARK}|{MARK}\n")
        rc, out, err = self.report(bad, text=self.w("t", "x\n"))
        self.assertEqual(rc, 2)
        self.assertNotIn(MARK, out + err)
        self.assertIn("bad.txt:1", err)
        self.assertIn("2 field", err)

    def test_invalid_utf8_does_not_crash(self):
        t = self.w("t", b"\xff\xfe \xe4\xbb\x8a\xe5\xa4\xa9\xe5\xa4\xa9\xe6\xb0\xa3\xe5\xbe\x88\xe5\xa5\xbd \xc3\n\x80\n")
        rc, out, _ = self.report(self.rows(), text=t)
        self.assertEqual(rc, 0)
        self.assertIn("\t2\t1\n", out)
        rc, _, _ = self.report(self.w("r2", b"a|\xff\xfe|x\n"), text=t)
        self.assertEqual(rc, 0)

    def test_report_prints_only_numbers(self):
        t = self.w("t", "今天天氣很好\n")
        _, out, _ = self.report(self.rows(), text=t)
        lines = [l.split("\t") for l in out.splitlines()]
        self.assertEqual(lines[0][0], t)
        self.assertTrue(all(x.isdigit() for x in lines[0][1:]))
        self.assertEqual(lines[1][0], "rows")
        self.assertRegex(lines[1][1], r"rows\.txt:\d+$")
        self.assertNotIn("今天", out)

    def test_wiki_path(self):
        pages = ("<page><ns>0</ns><text xml:space=\"preserve\">{{tpl}}我說'''今天天氣很好'''，沒錯。</text></page>\n"
                 "<page><ns>0</ns><text>別的條目</text></page>\n"
                 "<page><ns>0</ns><text>[[X|今天天氣很好]]啊</text></page>\n")
        dump = os.path.join(self.d.name, "d.xml.bz2")
        with bz2.open(dump, "wt", encoding="utf-8") as f:
            f.write(pages)
        _, out, _ = self.report(self.rows(), wiki=dump, articles="2", skip="0")
        self.assertIn("wiki\t2\t1\n", out)
        _, out, _ = self.report(self.rows(), wiki=dump, articles="1", skip="2")
        self.assertIn("wiki\t1\t1\n", out)


class Counting(Base):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(bc, "load_conv", fake_conv); p.start(); self.addCleanup(p.stop)
        self.rows = self.w("rows.txt", "a|今天天氣很好|x\nb|上網路|y\n")
        self.text = self.w("in.txt", "我覺得今天天氣很好吧\n我们上网络\n大家都來吃飯吧\n")

    def count(self, *flag):
        out = os.path.join(self.d.name, "o%d.pkl" % len(os.listdir(self.d.name)))
        with contextlib.redirect_stdout(io.StringIO()) as so:
            bct.main([out, self.text, *flag])
        return pickle.load(open(out, "rb")), so.getvalue()

    def test_off_equals_baseline_and_on_skips_runs(self):
        base, so0 = self.count()
        again, _ = self.count()
        self.assertEqual(base, again)
        self.assertNotIn("holdout-guard", so0)
        on, so = self.count("--holdout-guard", self.rows)
        self.assertEqual(base["runs"] - on["runs"], 2)   # line 1, and line 2 once converted
        self.assertIn("holdout-guard: skipped 2 runs", so)
        self.assertLess(sum(on["uni"].values()), sum(base["uni"].values()))
        none = self.w("none.txt", "a|完全不相干的句子|x\n")   # matches nothing: same counts as off
        same, _ = self.count("--holdout-guard", none)
        self.assertEqual(same, base)

    def test_workers_get_the_matcher(self):
        texts = ["我覺得今天天氣很好吧", "大家都來吃飯吧"]
        with mp.get_context("fork").Pool(1, initializer=bc._init, initargs=(False, False, False, (), [self.rows])) as pool:
            on = pool.apply(bc._count, (texts,))
        bc._init(False, False, False, ())
        off = bc.count_batch(texts)
        self.assertEqual(len(off), 4)   # count_batch keeps its shape
        self.assertEqual(on[4], 1)
        self.assertEqual(off[3] - on[3], 1)


if __name__ == "__main__":
    unittest.main()
