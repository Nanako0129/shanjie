"""Tests for build_t2.py. Run: python3 -m unittest test_build_t2 (in this directory)."""
import collections, contextlib, io, math, os, shutil, sys, tempfile, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_t2 as T  # noqa: E402

T0 = [("甲", "a", 0.0), ("甲", "b", 0.0), ("乙", "x", 0.0), ("乙", "y", -1.0), ("丙", "p", 0.0), ("丙", "q", 0.0), ("一", "ㄧ", 0.0), ("一", "ㄧˊ", 0.0)]
CLASSES = {"甲": "tone", "乙": "seg", "丙": "seg", "一": "tone"}
PRIMARY = {"甲": "a", "乙": "x", "丙": "p", "一": "ㄧ"}


def build(counts, usable=("tone", "seg"), t0=T0):
    rows, changed, flips = T.build(t0, {c: collections.Counter(v) for c, v in counts.items()}, CLASSES, set(usable), PRIMARY)
    return {(c, r): t for c, r, t in rows}, changed, flips, rows


class Rule(unittest.TestCase):
    def test_smoothing_with_k_5_and_the_prior_from_t0(self):
        t, changed, _, _ = build({"乙": {"x": 10, "y": 10}})
        z = 1 + 0.1
        px, py = (10 + 5 * 1 / z) / 25, (10 + 5 * 0.1 / z) / 25
        self.assertEqual(t["乙", "x"], 0.0)
        self.assertAlmostEqual(t["乙", "y"], math.log10(py / px), places=12)
        self.assertEqual(changed, ["乙"])

    def test_flip_guard_primary_stays_zero_and_the_flip_is_reported(self):
        t, _, flips, _ = build({"甲": {"a": 1, "b": 14}})    # the labeller says b by a mile
        self.assertEqual(t["甲", "a"], 0.0)                  # r* does not move
        self.assertEqual(t["甲", "b"], 0.0)                  # min(0, log10(P(b)/P(a))) with P(b) > P(a)
        self.assertEqual(flips, [("甲", "a", "b")])
        t, _, flips, _ = build({"甲": {"a": 14, "b": 1}})
        self.assertLess(t["甲", "b"], 0.0)
        self.assertEqual(flips, [])

    def test_fewer_than_10_positions_or_unusable_class_keep_t0(self):
        _, changed, _, rows = build({"乙": {"x": 4, "y": 5}, "甲": {"a": 20}}, usable=("seg",))
        self.assertEqual(changed, [])                         # 乙 has 9; 甲 is a tone character and only seg is usable
        self.assertEqual(rows, T0)
        _, changed, _, _ = build({"乙": {"x": 5, "y": 5}}, usable=("seg",))
        self.assertEqual(changed, ["乙"])                     # exactly 10

    def test_sandhi_pairs_and_untouched_characters_stay_byte_for_byte(self):
        t, changed, _, rows = build({"一": {"ㄧ": 20, "ㄧˊ": 1}, "乙": {"x": 5, "y": 5}})
        self.assertEqual(t["一", "ㄧˊ"], 0.0)                 # sandhi stays at T0's value
        self.assertEqual([r for r in rows if r[0] in "甲丙"], [r for r in T0 if r[0] in "甲丙"])
        self.assertEqual([(c, r) for c, r, _ in rows], [(c, r) for c, r, _ in T0])   # T0's row order
        self.assertIs(T.SANDHI, T.B1.SANDHI_PAIRS)   # build_t1's pairs, not a copy


class Files(unittest.TestCase):
    def test_main_end_to_end_is_reproducible_and_refuses_a_foreign_t0(self):
        d = tempfile.mkdtemp()
        try:
            chars = os.path.join(d, "chars.txt")
            with open(chars, "w", encoding="utf-8") as f:
                f.write("乙\n")
            t0 = os.path.join(d, "t0.tsv")
            with open(t0, "w", encoding="utf-8") as f:
                f.write("乙\tx\t0.0\n乙\ty\t-1.0\n")
            lab = os.path.join(d, "lab.tsv")
            with open(lab, "w", encoding="utf-8") as f:
                f.writelines(f"wiki-4e59-{k}\t乙\t{'x' if k < 6 else 'y'}\n" for k in range(12))
            patches = [mock.patch.object(T.S, "load_merged", return_value=({"乙": ["x", "y"]}, {})), mock.patch.object(T.S, "single_pairs", return_value={("x", "乙"), ("y", "乙")}),
                       mock.patch.object(T.S, "primary_readings", return_value={"乙": "x"})]
            for p in patches:
                p.start()
                self.addCleanup(p.stop)
            outs = []
            for k in (1, 2):
                out = os.path.join(d, f"t2.{k}")
                with contextlib.redirect_stdout(io.StringIO()) as so:
                    self.assertEqual(T.main(["--t0", t0, "--labels", lab, "--usable", "seg", "--chars", chars, "--out", out]), 0)
                with open(out, encoding="utf-8") as f:
                    outs.append(f.read())
            self.assertEqual(outs[0], outs[1])
            self.assertIn("changed characters 1", so.getvalue())
            self.assertIn("T2 sha256", so.getvalue())
            self.assertTrue(outs[0].startswith("乙\tx\t0.0\n乙\ty\t-"))
            with mock.patch.object(T.S, "single_pairs", return_value={("x", "乙")}), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    T.main(["--t0", t0, "--labels", lab, "--usable", "seg", "--chars", chars, "--out", os.path.join(d, "t2.3")])
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):   # --out exists
                    T.main(["--t0", t0, "--labels", lab, "--usable", "seg", "--chars", chars, "--out", os.path.join(d, "t2.1")])
            for bad_id in ("user-reported-3-1", "typing76-1-0", "dev302-5-2", "i99"):   # calibration positions never feed n_r
                with open(lab + "." + bad_id, "w", encoding="utf-8") as f:
                    f.write(f"{bad_id}\t乙\tx\n")
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    T.main(["--t0", t0, "--labels", lab + "." + bad_id, "--usable", "seg", "--chars", chars, "--out", os.path.join(d, "t2." + bad_id)])
            with open(lab, "a", encoding="utf-8") as f:
                f.write("wiki-4e59-0\t乙\ty\n")   # a repeated id
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    T.main(["--t0", t0, "--labels", lab, "--usable", "seg", "--chars", chars, "--out", os.path.join(d, "t2.5")])
            with open(lab, "w", encoding="utf-8") as f:
                f.write("wiki-4e59-0\t乙\tx\n")
            with open(lab, "a", encoding="utf-8") as f:
                f.write("wiki-4e59-1\t乙\tz\n")
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):   # a label that is not a reading of the character
                    T.main(["--t0", t0, "--labels", lab, "--usable", "seg", "--chars", chars, "--out", os.path.join(d, "t2.4")])
        finally:
            shutil.rmtree(d)


if __name__ == "__main__":
    unittest.main()
