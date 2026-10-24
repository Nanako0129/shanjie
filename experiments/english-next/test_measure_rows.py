"""Tests for measure_rows.py on a synthetic PTT tree (docs/contracts/english-next.md section 3.1). No real data, no network.
Run: python3 -W ignore -m unittest experiments/english-next/test_measure_rows.py"""
import itertools
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "tools"))
import build_english_next as B  # noqa: E402
import measure_rows as M  # noqa: E402


class FakeReader:
    def read(self, w):
        return (None if w == "無讀音" else ["ㄏㄠˇ"] * len(w)), [], []


def ids(bucket, n):
    """n article IDs in the build bucket (True) or the measurement bucket (False)."""
    return list(itertools.islice((i for i in (f"M.{k}.A.T" for k in itertools.count()) if B.in_build_bucket(i) == bucket), n))


def page(body="", pushes=()):
    p = "".join(f'<div class="push"><span class="hl push-tag">推 </span><span class="f3 hl push-userid">u{n}</span>'
                f'<span class="f3 push-content">: {c}</span><span class="push-ipdatetime"> 10/25 12:00\n</span></div>' for n, c in enumerate(pushes))
    return f'<div id="main-content" class="bbs-screen bbs-content">{body}\n--\n簽名\n<span class="f2">※ 發信站: 批踢踢實業坊(ptt.cc)</span>{p}</div>'


class Measure(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)
        self.lex = os.path.join(self.d.name, "lex.txt")
        with open(self.lex, "w", encoding="utf-8") as f:
            f.write("ㄏㄠˇ 好 -1\nㄒ 笑死 -1\nㄒ 無讀音 -1\n")
        self.ptt = os.path.join(self.d.name, "ptt")

    def art(self, aid, html, board="Gossiping"):
        os.makedirs(os.path.join(self.ptt, board), exist_ok=True)
        with open(os.path.join(self.ptt, board, aid + ".html"), "w", encoding="utf-8") as f:
            f.write(html)

    def run_(self, es=("joe", "lol"), **kw):
        return M.build(self.ptt, set(es), [self.lex], FakeReader(), **kw)

    def test_bucket_one_per_article_table_and_skip(self):
        for i in ids(True, 3):
            self.art(i, page("joe 好"))                       # build bucket: never contributes
        a, b, c, d = ids(False, 4)
        self.art(a, page("joe 好 and lol 笑死"))              # two events, one row
        self.art(b, page("xyz 好"))                           # e not in the table
        self.art(c, page("", ["joe 無讀音"]))                 # no reading: skipped, counted
        self.art(d, page("see https://x.y/joe 好"))           # URL is a boundary: no event
        rows, rep = self.run_()
        self.assertEqual(len(rows), 1)
        self.assertIn(rows[0], ["joe|好|ㄏㄠˇ", "lol|笑死|ㄏㄠˇ ㄏㄠˇ"])
        self.assertEqual(rep["articles_in_bucket"], 4)
        self.assertEqual(rep["skipped_no_reading"], 1)
        self.assertEqual(rep["events"], 3)

    def test_deterministic_and_seed_matters(self):
        for i in ids(False, 40):
            self.art(i, page("joe 好 lol 笑死"))
        r1, _ = self.run_(seed=1)
        r2, _ = self.run_(seed=1)
        r3, _ = self.run_(seed=2)
        self.assertEqual(r1, r2)
        self.assertEqual(r1, sorted(r1, key=lambda r: r.encode()))
        self.assertEqual(len(r1), 40)
        self.assertNotEqual(r1, r3)

    def test_cap(self):
        for i in ids(False, 30):
            self.art(i, page("joe 好"))
        rows, rep = self.run_(cap=10)
        self.assertEqual((len(rows), rep["rows"]), (10, 10))

    def test_parallel_same(self):
        for i in ids(False, 20):
            self.art(i, page("joe 好 lol 笑死"))
        self.assertEqual(self.run_()[0], self.run_(jobs=2)[0])

    def test_stop_below_threshold(self):
        for i in ids(False, 5):
            self.art(i, page("joe 好"))
        tbl, out = os.path.join(self.d.name, "t.tsv"), os.path.join(self.d.name, "o.txt")
        open(tbl, "w", encoding="utf-8").write("E\tjoe\t9\nW\tjoe\t好\t9\n")
        argv = ["m", "--ptt", self.ptt, "--table", tbl, "--lexicon", self.lex, "--out", out, "--jobs", "1"]
        with mock.patch.object(sys, "argv", argv), mock.patch.object(M, "default_reader", FakeReader):
            with self.assertRaises(SystemExit) as cm:
                M.main()
            self.assertIn("STOP", str(cm.exception))
            self.assertFalse(os.path.exists(out))
            with mock.patch.object(M, "MIN_ROWS", 3):
                M.main()
        self.assertEqual(len(open(out, encoding="utf-8").read().splitlines()), 5)


if __name__ == "__main__":
    unittest.main()
