"""merge_guard_rows.py on synthetic reports (docs/contracts/tune-no-twphrases.md section 1.2)."""
import contextlib
import hashlib
import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import merge_guard_rows as m  # noqa: E402

A, B = "cv.txt", "wiki.txt"
A_LINES = [f"|甲乙丙丁{i}|ㄅ\n".encode() for i in range(1, 7)]
B_LINES = [f"|戊己庚辛{i}|ㄆ\n".encode() for i in range(1, 5)]


def report(*rows, patterns="3\t0"):
    return f"patterns\t{patterns}\nsrc.txt\t100\t{len(rows)}\n" + "".join(f"rows\t{r}\n" for r in rows)


class Merge(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory(); self.addCleanup(self.d.cleanup)
        self.a, self.b = self.p(A), self.p(B)
        open(self.a, "wb").write(b"".join(A_LINES)); open(self.b, "wb").write(b"".join(B_LINES))

    def p(self, name):
        return os.path.join(self.d.name, name)

    def rep(self, name, text):
        open(self.p(name), "w", encoding="utf-8").write(text)
        return self.p(name)

    def run_merge(self, reports):
        return m.merge(reports, [(self.a, self.p("a.out")), (self.b, self.p("b.out"))])

    def test_union_per_file_keeps_order_and_bytes(self):
        r1 = self.rep("r1", report(f"{self.a}:2", f"{self.b}:1", f"{self.a}:5"))
        r2 = self.rep("r2", report(f"{self.a}:5", f"{self.a}:6"))
        r3 = self.rep("r3", report())                                             # a source with no hit
        out = self.run_merge([r1, r2, r3])
        self.assertEqual(open(self.p("a.out"), "rb").read(), b"".join(A_LINES[i] for i in (0, 2, 3)))
        self.assertEqual(open(self.p("b.out"), "rb").read(), b"".join(B_LINES[1:]))
        self.assertEqual(out[self.a][:3], (6, 3, 3))
        self.assertEqual(out[self.b][:3], (4, 1, 3))
        self.assertEqual(out[self.a][3], hashlib.sha256(open(self.p("a.out"), "rb").read()).hexdigest())

    def test_lone_carriage_returns_do_not_split_lines(self):
        open(self.a, "wb").write(b"x\ry\nz\n")
        m.merge([self.rep("r", report(f"{self.a}:1"))], [(self.a, self.p("a.out"))])
        self.assertEqual(open(self.p("a.out"), "rb").read(), b"z\n")

    def test_a_report_without_patterns_line_stops_everything(self):
        r = self.rep("r", f"src.txt\t100\t1\nrows\t{self.a}:1\n")
        with self.assertRaisesRegex(SystemExit, "did not finish"):
            self.run_merge([r])
        self.assertFalse(os.path.exists(self.p("a.out")))

    def test_reports_from_different_row_sets_stop(self):
        with self.assertRaisesRegex(SystemExit, "disagree"):
            self.run_merge([self.rep("r1", report(patterns="3\t0")), self.rep("r2", report(patterns="4\t0"))])

    def test_unknown_file_and_line_out_of_range_write_nothing(self):
        with self.assertRaisesRegex(SystemExit, "not a --clean input"):
            self.run_merge([self.rep("r1", report("other.txt:1"))])
        with self.assertRaisesRegex(SystemExit, "outside the file"):
            self.run_merge([self.rep("r2", report(f"{self.b}:5"))])
        self.assertFalse(os.path.exists(self.p("a.out")) or os.path.exists(self.p("b.out")))

    def test_output_is_never_overwritten(self):
        open(self.p("b.out"), "wb").write(b"keep")
        with self.assertRaisesRegex(SystemExit, "never overwritten"):
            self.run_merge([self.rep("r1", report())])
        self.assertEqual(open(self.p("b.out"), "rb").read(), b"keep")
        self.assertFalse(os.path.exists(self.p("a.out")))

    def test_main_prints_counts_not_text(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = m.main(["--clean", self.a, self.p("a.out"), "--clean", self.b, self.p("b.out"), self.rep("r1", report(f"{self.a}:1"))])
        self.assertEqual(rc, 0)
        self.assertIn("lines 6\tremoved 1\tkept 5", out.getvalue())
        self.assertNotIn("甲乙", out.getvalue())


if __name__ == "__main__":
    unittest.main()
