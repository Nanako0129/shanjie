"""colloquial_ref.py against core/tests/colloquial.rs and core/src/colloquial.rs: the toy table of that test is written here byte for byte,
lc and k are recomputed for the same word lists and must equal the numbers the Rust test hard-codes (read from its source text), and the
constants must equal those of the Rust module. Run: python3 -W ignore -m unittest reference/proto/test_colloquial_ref.py"""
import os
import re
import struct
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import colloquial_ref as C  # noqa: E402


def tiny():
    """tiny() of core/tests/colloquial.rs: vocabulary `<s> </s> a b`, N 10, eos total 4, D 0.75; unigrams a 6, b 4; context `<s>` (a 3, b 2),
    context `a` (`</s>` 2)."""
    b = b"SJLM0001" + struct.pack("<IQQd", 4, 10, 4, 0.75)
    for w in ["<s>", "</s>", "a", "b"]:
        b += struct.pack("<H", len(w.encode())) + w.encode()
    b += struct.pack("<4Q", 0, 0, 6, 4)
    b += struct.pack("<I", 2) + struct.pack("<2I", 0, 2) + struct.pack("<2Q", 5, 4)
    b += struct.pack("<3I", 0, 2, 3) + struct.pack("<I", 3) + struct.pack("<3I", 2, 3, 1) + struct.pack("<3I", 3, 2, 2)
    return b


def rust_test():
    with open(os.path.join(ROOT, "core", "tests", "colloquial.rs"), encoding="utf-8") as f:
        return f.read()


class Aligned(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        path = os.path.join(cls.tmp.name, "tiny.sjlm")
        with open(path, "wb") as f:
            f.write(tiny())
        cls.table = C.Table(path)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_lc_and_k_equal_the_numbers_the_rust_test_hard_codes(self):
        words = [["a", "b"], ["a"], ["zzz"], ["zzz", "a"], ["b", "a", "a"]]   # the lists of aligned_with_p4_py_committed
        src = rust_test()
        want_lc = [float(x) for x in re.search(r"let lc = \[([^\]]*)\]", src).group(1).split(",")]
        want_k = [int(x) for x in re.search(r"\.collect::<Vec<_>>\(\), \[([0-9, ]*)\]\)", src).group(1).split(",")]
        self.assertEqual(len(want_lc), 5)
        self.assertEqual([self.table.lc(w) for w in words], want_lc)   # the Rust numbers are the Python ones, printed with repr
        self.assertEqual([C.k_of(w, {"a", "b", "zzz"}, {"b"}) for w in words], want_k)
        self.assertEqual(want_k, [1, 1, 1, 2, 2])

    def test_lc_by_hand(self):
        """The hand computation of lc_by_hand in the Rust test."""
        import math
        back_s, back_a, pe = 1.0 - ((3.0 - 0.75) + (2.0 - 0.75)) / 5.0, 1.0 - (2.0 - 0.75) / 4.0, 0.4
        ab = math.log10((3.0 - 0.75) / 5.0 + back_s * (7.0 / 15.0)) + math.log10(back_a * (5.0 / 15.0)) + math.log10(pe)
        self.assertAlmostEqual(self.table.lc(["a", "b"]), ab, places=9)
        z = math.log10(back_s * (1.0 / 15.0)) + math.log10(pe)
        self.assertAlmostEqual(self.table.lc(["zzz"]), z, places=9)

    def test_col(self):
        ovl, base = {"dup", "both"}, {"both", "a"}
        w = ["dup", "a", "dup"]
        self.assertEqual(C.k_of(w, ovl, base), 2)
        self.assertAlmostEqual(C.col(self.table, w, ovl, base), self.table.lc(w) - 0.5 * 2.0, places=12)


class Sets(unittest.TestCase):
    def test_sets_from_the_texts(self):
        """sets_from_the_texts of the Rust test."""
        base = "# comment\n_ctl x y\nㄋㄧˇ-ㄏㄠˇ 你好 -3.0\nㄋㄧˇ 壞詞 -1.0\n"
        overlay = "ㄋㄧˇ-ㄏㄠˇ\t你好\t-3.0\t0\nㄔㄨㄥˊ-ㄉㄧㄝˊ\t重疊\t-4.0\t0\nthree\tfields\tonly\n"
        b, o = C.word_sets(base, overlay)
        self.assertEqual(b, {"你好"})
        self.assertEqual(C.k_of(["你好", "重疊", "壞詞", "三"], o, b), 1)
        self.assertEqual(C.k_of(["重疊", "重疊"], o, b), 2)
        with self.assertRaises(ValueError):
            C.word_sets("ㄋㄧˇ-ㄏㄠˇ 你好 not-a-number\n", overlay)

    def test_pack_rows(self):
        b, o = C.word_sets("ㄅ-ㄅ 共有 -3.0\nㄅ-ㄅ 包共 -3.0\n", "ㄅ-ㄅ\t共有\t-3.0\t0\nㄅ-ㄅ\t只有疊加\t-3.0\t0\n", "ㄅ-ㄅ\t包共\t-3.0\tacg\nㄅ-ㄅ\t只有詞包\t-3.0\tacg\n")
        self.assertEqual(C.k_of(["共有", "包共"], o, b), 0)
        self.assertEqual(C.k_of(["只有疊加", "只有詞包"], o, b), 2)


class Constants(unittest.TestCase):
    def test_constants_equal_the_rust_module(self):
        with open(os.path.join(ROOT, "core", "src", "colloquial.rs"), encoding="utf-8") as f:
            src = f.read()
        for name in ("MU", "GATE_T", "GAMMA_PER_MU"):
            self.assertEqual(float(re.search(r"pub const %s: f64 = ([0-9.]+);" % name, src).group(1)), getattr(C, name), name)

    def test_fuse_is_relative_to_the_anchor(self):
        """fuse_is_relative_to_the_anchor_and_gated_by_ll_0 of core/src/colloquial.rs."""
        ll, cols = [-10.0, -9.0, -12.0], [-5.0, -4.0, -3.0]
        k = C.MU / 1.1
        eff = C.fuse(ll, cols, 1.1)
        self.assertAlmostEqual(eff[0], -10.0, places=12)
        self.assertAlmostEqual(eff[1], -9.0 + C.sigmoid(1.0) * k * 1.0, places=12)
        self.assertAlmostEqual(eff[2], -12.0 + C.sigmoid(-2.0) * k * 2.0, places=12)
        self.assertEqual(C.sigmoid(2000.0), 1.0)
        self.assertEqual(C.sigmoid(-2000.0), 0.0)


if __name__ == "__main__":
    unittest.main()
