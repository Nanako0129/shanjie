"""force_classes.py（model-v6 契約 §2.2b）用合成的分群輸出。執行：python3 -m unittest experiments/model-v6/test_force_classes.py"""
import contextlib
import hashlib
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import force_classes as F  # noqa: E402

# (詞, uni, 類別)。個 = 類別 0。
WORDS = [("個", 90000, 0), ("隻", 500, 1), ("條", 5000, 1), ("張", 1300, 1), ("本", 10, 0), ("坨", 5, -1),
         ("邊", 999, 2), ("界", 1000, 2), ("非量詞", 1, 3), ("粒", 40, 4)]
LISTED = "# 註解\n個\n隻\n條\n張\n本\n坨\n邊\n界\n粒\n艘\n\n隻\n"      # 艘 不在詞彙；隻 重複列
# 張 的原始次數若只有維基 300 ＋ 口語 200（×5 = 1000）… 這裡 edges 的 uni 已是加權後的 1300：原始 < 1,000 但加權 ≥ 1,000，不搬。


def make(d, words=WORDS, anchor_cls=True):
    np.savez(os.path.join(d, "edges.npz"), vocab=np.array([w for w, _, _ in words], dtype=object),
             uni=np.array([u for _, u, _ in words], np.int64), src=np.array([0]), dst=np.array([1]), cnt=np.array([1]))
    np.savez(os.path.join(d, "cls-10-5.npz"), cls=np.array([c for _, _, c in words], np.int32), K=5, N=10, mi=np.array([1.5, 1.6]))
    open(os.path.join(d, "classifiers.txt"), "w", encoding="utf-8").write(LISTED)


class Force(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name
        self.addCleanup(self.tmp.cleanup)
        make(self.d)

    def run_main(self, *args, out="new.npz"):
        argv = ["force_classes.py", "--dir", self.d, "--N", "10", "--K", "5", "--list", os.path.join(self.d, "classifiers.txt"),
                "--out", os.path.join(self.d, out), *args]
        buf = io.StringIO()
        with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(buf):
            F.main()
        return buf.getvalue()

    def cls_of(self, path):
        z = np.load(path)
        return dict(zip([w for w, _, _ in WORDS], z["cls"].tolist()))

    def test_only_below_threshold_and_other_class_move_to_the_anchor_class(self):
        self.run_main()
        got = self.cls_of(os.path.join(self.d, "new.npz"))
        self.assertEqual(got, {"個": 0, "隻": 0, "條": 1, "張": 1, "本": 0, "坨": 0, "邊": 0, "界": 2, "非量詞": 3, "粒": 0})

    def test_weighted_uni_decides_not_the_raw_count(self):
        """張：原始次數 < 1,000（維基 300＋口語 200），加權後 uni = 1,300 ≥ 1,000，所以不動；門檻上 1,000 不動、999 搬。"""
        self.run_main()
        got = self.cls_of(os.path.join(self.d, "new.npz"))
        self.assertEqual((got["張"], got["界"], got["邊"]), (1, 2, 0))

    def test_report_lists_moved_words_with_old_class_and_uni_and_skips_the_missing(self):
        out = self.run_main()
        lines = out.splitlines()
        self.assertEqual([l for l in lines if l.startswith("moved ")],
                         ["moved 隻\told_class=1\tuni=500", "moved 坨\told_class=-1\tuni=5", "moved 邊\told_class=2\tuni=999", "moved 粒\told_class=4\tuni=40"])
        self.assertEqual([l for l in lines if l.startswith("skipped")], ["skipped (not in vocabulary): 艘"])
        sha = hashlib.sha256(open(os.path.join(self.d, "new.npz"), "rb").read()).hexdigest()
        self.assertTrue(lines[0].startswith(f"sha256={sha} moved=4 skipped=1 "), lines[0])

    def test_output_keeps_everything_else_and_never_touches_the_input(self):
        src = os.path.join(self.d, "cls-10-5.npz")
        before = open(src, "rb").read()
        self.run_main()
        old, new = np.load(src), np.load(os.path.join(self.d, "new.npz"))
        self.assertEqual(sorted(new.files), sorted(old.files))
        for k in ("K", "N", "mi"):
            self.assertEqual(new[k].tolist(), old[k].tolist())
        self.assertEqual(open(src, "rb").read(), before)

    def test_refuses_to_overwrite_an_existing_file_including_the_input(self):
        src = os.path.join(self.d, "cls-10-5.npz")
        before = open(src, "rb").read()
        with self.assertRaises(SystemExit):
            self.run_main(out="cls-10-5.npz")
        self.assertEqual(open(src, "rb").read(), before)

    def test_missing_anchor_stops_and_writes_nothing(self):
        make(self.d, [w for w in WORDS if w[0] != "個"])
        with self.assertRaises(SystemExit) as cm:
            self.run_main()
        self.assertIn("not in the vocabulary", str(cm.exception))
        self.assertFalse(os.path.exists(os.path.join(self.d, "new.npz")))

    def test_anchor_without_a_class_stops(self):
        make(self.d, [("個", 90000, -1)] + WORDS[1:])
        with self.assertRaises(SystemExit) as cm:
            self.run_main()
        self.assertIn("no class", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
