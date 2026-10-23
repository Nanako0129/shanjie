"""Tests for calib.py. Run: python3 -m unittest test_calib (in this directory)."""
import contextlib, io, os, shutil, sys, tempfile, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import calib as C  # noqa: E402

CLASSES = {"甲": "tone", "乙": "seg"}
PRIMARY = {"甲": "a", "乙": "x"}


def rows(char, n, human, tool, start=0):
    return [(f"{char}{start + i}", "s", "句子", 0, char, human, tool) for i in range(n)]


def labels(rs, lab):
    return {r[0]: (r[4], lab) for r in rs}


class Agree(unittest.TestCase):
    def test_usable_needs_20_positions_and_labeller_not_worse_than_tool(self):
        tone = rows("甲", 20, "a", "a")                      # 20 positions
        seg = rows("乙", 19, "x", "x")                       # 19: too few
        st, _ = C.agree(tone + seg, {**labels(tone, "a"), **labels(seg, "x")}, CLASSES, PRIMARY)
        self.assertEqual((st["tone"]["n"], st["tone"]["usable"], st["seg"]["usable"]), (20, True, False))   # n = 19 fails even at 100%
        # labeller worse than the tool by one position: unusable; equal: usable
        t2 = rows("甲", 20, "a", "a")
        lab = labels(t2, "a")
        lab[t2[0][0]] = ("甲", "b")
        self.assertFalse(C.agree(t2, lab, CLASSES, PRIMARY)[0]["tone"]["usable"])
        t3 = rows("甲", 10, "a", "a") + rows("甲", 10, "a", "b", 10)   # tool 10/20; labeller 10/20 by the same positions
        lab = {r[0]: ("甲", "a" if r[6] == "a" else "b") for r in t3}
        s = C.agree(t3, lab, CLASSES, PRIMARY)[0]["tone"]
        self.assertEqual((s["lab"], s["tool"], s["usable"]), (10, 10, True))

    def test_invalid_labels_leave_both_counts(self):
        r = rows("甲", 25, "a", "b")
        lab = labels(r, "a")
        for k in r[:6]:
            lab[k[0]] = ("甲", "")
        s = C.agree(r, lab, CLASSES, PRIMARY)[0]["tone"]
        self.assertEqual((s["n"], s["lab"], s["tool"]), (19, 19, 0))

    def test_confusion_counts_only_non_primary_human_readings(self):
        r = rows("乙", 3, "y", "x") + rows("乙", 2, "x", "x", 3)
        _, conf = C.agree(r, labels(r, "x"), CLASSES, PRIMARY)
        self.assertEqual(dict(conf), {("seg", "乙", "y", "x"): 3})


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)

    def write(self, name, text):
        p = os.path.join(self.d, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        return p

    def run_cli(self, argv):
        with contextlib.redirect_stdout(io.StringIO()) as so, contextlib.redirect_stderr(io.StringIO()):
            rc = C.main(argv)
        return rc, so.getvalue()

    def test_agree_prints_both_unusable_and_exits_0(self):
        human = "".join("h%d\ts\t句子\t0\t甲\ta\ta\n" % i for i in range(5))
        lab = "".join("h%d\t甲\ta\n" % i for i in range(5))
        with mock.patch.object(C.S, "load_chars", return_value=["甲", "乙"]), mock.patch.object(C.S, "load_merged", return_value=({"甲": ["a", "a˙"], "乙": ["x", "y"]}, {})):
            rc, out = self.run_cli(["agree", self.write("h.tsv", human), self.write("l.tsv", lab)])
        self.assertEqual(rc, 0)
        self.assertIn("BOTH UNUSABLE: stop", out)
        self.assertIn("usable: \n", out)
        self.assertIn("tone\tpositions 5\tlabeller 5/5 = 1.0000\ttool 5/5 = 1.0000\tUNUSABLE", out)

    def sample_files(self):
        positions = "".join(f"p{i}\tsrc\t我有第{i}個乙乙\t{i % 3}\t{'乙' if i % 2 else '甲'}\n" for i in range(40))
        lab = "".join(f"p{i}\t{'乙' if i % 2 else '甲'}\t{'y' if i % 7 else ''}\n" for i in range(40))
        return self.write("pos.tsv", positions), self.write("lab.tsv", lab)

    def test_manual_sample_is_seg_only_valid_only_deterministic_and_eight_columns(self):
        pos, lab = self.sample_files()
        outs = []
        with mock.patch.object(C.S, "load_chars", return_value=["甲", "乙"]), mock.patch.object(C.S, "load_merged", return_value=({"甲": ["a", "a˙"], "乙": ["x", "y"]}, {})):
            for k in (1, 2):
                out = os.path.join(self.d, f"j{k}.tsv")
                self.run_cli(["manual-sample", pos, lab, "--n", "8", "--out", out])
                with open(out, encoding="utf-8") as f:
                    outs.append(f.read())
        self.assertEqual(outs[0], outs[1])
        rows = [l.split("\t") for l in outs[0].splitlines()]
        self.assertEqual(len(rows), 8)
        self.assertTrue(all(len(r) == 8 and r[4] == "乙" and r[5] == "y" and r[7] == "" for r in rows))
        self.assertTrue(all(r[0] not in ("p0", "p14", "p28") for r in rows))   # p14, p28 have an empty label (and are 甲 anyway)
        self.assertIn("[乙]", rows[0][6])

    def test_manual_rate_exit_codes(self):
        line = "i\ts\t句\t0\t乙\ty\t[乙]\t%s\n"
        ok = self.write("ok.tsv", "".join(line % "ok" for _ in range(135)) + "".join(line % "x" for _ in range(15)))     # 10.0%: not above
        bad = self.write("bad.tsv", "".join(line % "ok" for _ in range(134)) + "".join(line % "x" for _ in range(16)))   # 10.67%
        blank = self.write("blank.tsv", line % "")
        self.assertEqual(self.run_cli(["manual-rate", ok])[0], 0)
        self.assertEqual(self.run_cli(["manual-rate", bad])[0], 3)
        self.assertEqual(self.run_cli(["manual-rate", blank])[0], 1)


if __name__ == "__main__":
    unittest.main()
