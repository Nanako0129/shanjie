"""p2cdiag on synthetic records: pair extraction, agreement on fixable rows, and the seen / unseen split of separating pairs."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import p2cdiag as d  # noqa: E402


def rec(i, ok, ll, skip=0):
    return {"i": i, "n": len(ok), "ok": ok, "ll": ll, "skip": skip}


class TestDiag(unittest.TestCase):
    def test_diff_pairs_are_unordered_and_positional(self):
        self.assertEqual(d.diff_pairs("在家", "再家"), [("再", "在")])
        self.assertEqual(d.diff_pairs("甲乙", "乙甲"), [("乙", "甲"), ("乙", "甲")])
        self.assertEqual(d.diff_pairs("一樣", "一樣"), [])

    def test_train_pairs_only_same_length_same_row(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "t.dump8")
            with open(p, "w", encoding="utf-8") as f:
                f.write("1\t1\t在家\t-1\n1\t2\t再家\t-2\n1\t3\t在家裡\t-3\n2\t1\t天氣\t-1\n2\t9\t天汽\t-9\n")
            self.assertEqual(d.train_pairs([p]), {("再", "在")})       # rank 9 is past the top 8; lengths must match

    def test_diagnose_counts(self):
        cands = {1: ["再家", "在家"], 2: ["天汽", "天氣"], 3: ["好", "號"], 4: ["對", "隊"], 5: ["甲", "乙"]}
        s = {1: rec(1, [0, 1], [-1, -2]),            # fixable; student keeps the wrong first, 0c picks the right one
             2: rec(2, [0, 1], [-1, -2]),            # same, pair 汽/氣 not seen in training
             3: rec(3, [1, 0], [-1, -2]),            # first is right: not fixable
             4: rec(4, [0, 1], [-2, -1]),            # fixable, both right
             5: rec(5, [0, 1], [-1, -2])}            # 0c skipped this row
        t = {1: rec(1, [0, 1], [-3, -1]), 2: rec(2, [0, 1], [-3, -1]), 3: rec(3, [1, 0], [-1, -2]),
             4: rec(4, [0, 1], [-2, -1]), 5: rec(5, [0, 1], [0, 0], skip=1)}
        r = d.diagnose(s, t, cands, {("再", "在")})
        self.assertEqual((r["n_all"], r["agree_all"], r["n_fix"], r["agree_fix"]), (4, 2, 3, 1))
        self.assertEqual((r["n_tr"], r["first_seen"], r["all_seen"]), (2, 1, 1))


class TestStudentOption(unittest.TestCase):
    def run_main(self, td, args):
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            d.main(["--scores", td, "--dumps", td, "--train", os.path.join(td, "t.dump8"), "--sets", "x", *args])
        return buf.getvalue()

    def test_student_prefix_selects_the_score_file(self):
        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(td, "t.dump8"), "w", encoding="utf-8") as f:
                f.write("1\t1\t甲乙\t-1\n")
            with open(os.path.join(td, "x.dump"), "w", encoding="utf-8") as f:
                f.write("1\t1\t再家\t-1\n1\t2\t在家\t-2\n")
            recs = {"S": rec(1, [0, 1], [-1, -2]), "T": rec(1, [0, 1], [-2, -1]), "X0C": rec(1, [0, 1], [-2, -1])}
            for name, r in recs.items():
                with open(os.path.join(td, f"{name}.x.jsonl"), "w", encoding="utf-8") as f:
                    f.write(json.dumps(r) + "\n")
            self.assertIn("S x: student and 0c pick the same candidate on fixable rows 0/1", self.run_main(td, []))      # default S
            self.assertIn("T x: student and 0c pick the same candidate on fixable rows 1/1", self.run_main(td, ["--student", "T"]))


if __name__ == "__main__":
    unittest.main()
