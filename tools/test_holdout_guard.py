"""holdout_guard.py on synthetic data (docs/contracts/holdout-refresh.md section 8)."""
import hashlib
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import holdout_guard as g


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)

    def path(self, name, data):
        p = os.path.join(self.d.name, name)
        with open(p, "wb") as f:
            f.write(data)
        return p

    def run_guard(self, rows, data, report=False):
        import contextlib, io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):  # hash file built from rows with the companion, as in real use
            g.main(["--hash-sentences", self.path("rows", rows)])
        hp = self.path("h", buf.getvalue().encode())
        ip = self.path("in", data)
        op = os.path.join(self.d.name, "out")
        rp = os.path.join(self.d.name, "rep")
        g.main(["--hashes", hp, "--out", op, ip] + (["--report-matches", rp] if report else []))
        rep = open(rp).read().split() if report else None
        return open(op, "rb").read(), rep

    def test_hash_format(self):
        self.assertEqual(g.h("你好"), hashlib.sha256("你好".encode()).hexdigest())
        self.assertRegex(g.h("你好"), r"^[0-9a-f]{64}$")

    def test_hash_sentences_uses_sentence_field_only(self):
        p = self.path("rows", "前文，|你好|ㄋㄧˇ ㄏㄠˇ\n".encode())
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            g.main(["--hash-sentences", p])
        self.assertEqual(buf.getvalue(), g.h("你好") + "\n")

    def test_drop_whole_line_on_clause_match(self):
        out, _ = self.run_guard("a|今天天氣|x\n".encode(), "別的話，今天天氣，還有別的。\n乾淨的一行\n".encode())
        self.assertEqual(out, "乾淨的一行\n".encode())

    def test_kept_lines_byte_for_byte(self):
        data = "無關\r\n  有空白  \n最後一行沒換行".encode()
        out, _ = self.run_guard("a|今天天氣|x\n".encode(), data)
        self.assertEqual(out, data)

    def test_five_separators_and_surrounding_space(self):
        for sep in "。！？；，":
            out, _ = self.run_guard("a|命中句|x\n".encode(), f"前{sep} 命中句 {sep}後\n保留\n".encode())
            self.assertEqual(out, "保留\n".encode(), sep)
        # no separator: the whole line is the clause; \r is stripped as whitespace
        out, _ = self.run_guard("a|命中句|x\n".encode(), "命中句\r\n".encode())
        self.assertEqual(out, b"")

    def test_report_matches_lists_exactly_matched_hashes(self):
        rows = "a|甲句|x\nb|乙句|x\nc|丙句|x\n".encode()
        data = "甲句。\n無關\n乙句，甲句\n".encode()
        _, rep = self.run_guard(rows, data, report=True)
        self.assertEqual(rep, sorted({g.h("甲句"), g.h("乙句")}))


if __name__ == "__main__":
    unittest.main()
