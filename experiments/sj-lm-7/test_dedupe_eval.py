"""dedupe_eval.py on synthetic sets and sources: the holdout_guard matcher, per-source per-set hit counts, nothing but numbers printed."""
import contextlib
import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dedupe_eval as D  # noqa: E402

CONV = ({}, {"网": "網", "络": "路"}, 1)          # a two-character stand-in for the OpenCC tables
SET_A = "前文|今天天氣真的很好啊|ㄅ\n|我們一起去吃飯|ㄅ\n|太短|ㄅ\n"      # the third is under 4 characters: not a pattern
SET_B = "|上網路看影片|ㄅ\n"
SOURCE = ["今天天氣真的很好啊我們走吧", "完全不相干的一句話", "一起去吃飯喔", "我們一起去吃飯", "上网络看影片真的很有趣", "太短"]


def write(d, name, text):
    p = os.path.join(d, name)
    open(p, "w", encoding="utf-8").write(text)
    return p


class Dedupe(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory(); self.addCleanup(self.d.cleanup)

    def run_main(self, extra=(), source_text=None, **kw):
        d = self.d.name
        a, b = write(d, "a.txt", SET_A), write(d, "b.txt", SET_B)
        s1 = write(d, "pool.tsv", source_text or "".join(f"u{i}\t{s}\n" for i, s in enumerate(SOURCE)))
        s2 = write(d, "plain.txt", "".join(s + "\n" for s in SOURCE[:3]))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            D.main(["--set", f"dev={a}", "--set", f"holdout={b}", "--source", f"pool.tsv={s1}", "--source", f"plain.txt={s2}",
                    "--out", os.path.join(d, "out"), *extra], conv=CONV)
        return out.getvalue()

    def test_hits_per_source_and_set_and_the_kept_lines(self):
        text = self.run_main()
        lines = text.splitlines()
        # pool.tsv: 0 contains dev row 1; 3 contains dev row 2 whole; 4 (simplified form) contains the holdout row after conversion; 2 is only a part
        self.assertEqual(lines[:3], ["pool.tsv\tdev\t2", "pool.tsv\tholdout\t1", f"pool.tsv\ttotal\t6\tremoved\t3\tkept\t3\tsha256\t{D.hashlib.sha256(open(os.path.join(self.d.name, 'out', 'pool.tsv'), 'rb').read()).hexdigest()}"])
        self.assertEqual(lines[3:5], ["plain.txt\tdev\t1", "plain.txt\tholdout\t0"])
        kept = open(os.path.join(self.d.name, "out", "pool.tsv"), encoding="utf-8").read().splitlines()
        self.assertEqual(kept, ["u1\t完全不相干的一句話", "u2\t一起去吃飯喔", "u5\t太短"])      # unchanged and in order
        self.assertEqual(open(os.path.join(self.d.name, "out", "plain.txt"), encoding="utf-8").read().splitlines(), SOURCE[1:3])

    def test_only_counts_and_hashes_are_printed(self):
        text = self.run_main()
        for s in SOURCE + ["今天天氣真的很好啊", "我們一起去吃飯"]:
            self.assertNotIn(s, text)

    def test_two_processes_give_the_same_output(self):
        one = self.run_main()
        two = self.run_main(["--jobs", "2"])
        self.assertEqual(one, two)

    def test_a_bad_set_file_stops_with_a_path_and_line_only(self):
        d = self.d.name
        bad = write(d, "bad.txt", "只有兩欄|今天天氣真的很好啊\n")
        src = write(d, "s.txt", "x\n")
        with self.assertRaises(SystemExit) as cm:
            D.main(["--set", f"dev={bad}", "--source", f"s={src}", "--out", os.path.join(d, "o")], conv=CONV)
        self.assertIn("bad.txt:1", str(cm.exception.code))
        self.assertNotIn("今天", str(cm.exception.code))


if __name__ == "__main__":
    unittest.main()
