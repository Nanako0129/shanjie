"""tools/oc_ref.py on a tiny random model: the file round trip with 4-byte aligned tensors, and the forward's mask / positions logic
(one run per row as a prefix tree must equal one run per candidate, through experiments/x0c/score_0c.py's own feeds)."""
import os
import sys
import tempfile
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import oc_ref  # noqa: E402

CFG = {"n_layer": 2, "n_head": 2, "d": 16, "ffn": 12, "n_tok": 8 + 6, "n_out": 8, "n_base": 2 + 3, "n_tone": 6, "n_type": 3,
       "n_rope": 4}
CHARS, BASES, NEUTRAL = list("天氣很好真的"), ["ㄊㄧㄢ", "ㄏㄠ", "ㄇㄚ"], ["ㄇㄚ"]
OUT = [4, 6] + list(range(8, 14))   # the graph's pattern: every id but 0-3, 5, 7


def tiny(seed=0):
    rng = np.random.default_rng(seed)
    c = CFG
    T = {n: rng.normal(0, 0.5, s).astype(np.float32) for n, s, _ in oc_ref.tensors(*(c[k] for k in (
        "n_layer", "d", "ffn", "n_tok", "n_base", "n_tone", "n_type", "n_rope")))}
    T["inv_freq"] = (1.0 / 10000 ** (np.arange(0, c["n_rope"]) / c["n_rope"])).astype(np.float32)
    return T


class TestOcRef(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.T = tiny()
        self.path = os.path.join(self.td.name, "t.sjoc")
        oc_ref.write(self.path, CFG, self.T, CHARS, BASES, NEUTRAL, OUT, (bytes(32), b"\x01" * 32))

    def tearDown(self):
        self.td.cleanup()

    def test_round_trip(self):
        self.assertNotEqual(len("\n".join(BASES).encode()) % 4, 0)                 # the padding is really exercised
        m = oc_ref.load(self.path)
        self.assertEqual((m["cfg"], m["chars"], m["bases"], m["neutral"], m["out_ids"].tolist()), (CFG, CHARS, BASES, NEUTRAL, OUT))
        self.assertEqual(m["src_sha"], (bytes(32), b"\x01" * 32))
        for k, a in self.T.items():
            np.testing.assert_array_equal(m["T"][k], a)

    def test_bad_layout_is_refused(self):
        T = dict(self.T)
        T["l1.wq"] = T["l1.wq"][:, :-1]
        with self.assertRaises(ValueError):
            oc_ref.write(os.path.join(self.td.name, "x"), CFG, T, CHARS, BASES, NEUTRAL, OUT, (bytes(32), bytes(32)))
        with open(self.path, "ab") as f:
            f.write(b"\0")
        with self.assertRaises(ValueError):
            oc_ref.load(self.path)

    def test_prefix_tree_equals_one_run_per_candidate(self):
        s0, z = oc_ref.zuin(self.path)
        cands = ["天氣", "天好", "很好", "真的"]
        for ctx in ("", "天氣很好真的" * 12):                                  # empty and longer than the 64-character window
            for reading in (["ㄊㄧㄢ", "ㄏㄠˇ"], ["ㄊㄧㄢ", "ㄇㄚ"]):              # the second one takes the neutral retry
                a = s0.score(z, ctx, reading, cands, packed=True)
                b = s0.score(z, ctx, reading, cands, packed=False)
                np.testing.assert_allclose(a, b, rtol=0, atol=1e-4)
                self.assertTrue(all(np.isfinite(a)) and all(x < 0 for x in a))

    def test_export_keeps_rows_with_two_candidates(self):
        dump = os.path.join(self.td.name, "d.tsv")
        with open(dump, "w", encoding="utf-8") as f:                          # row<TAB>rank<TAB>surface<TAB>score, as the CLI writes
            f.write("1\t1\t甲乙\t-1\n1\t2\t丙丁\t-2\n2\t1\t戊己\t-1\n3\t1\t庚辛\t-1\n3\t2\t壬癸\t-2\n3\t9\t子丑\t-3\n")
        out = os.path.join(self.td.name, "items.tsv")
        oc_ref.export(dump, 3, out)
        lines = [l.rstrip("\n").split("\t") for l in open(out, encoding="utf-8")]
        self.assertEqual([l[0] for l in lines], ["1", "3"])                     # row 2 has one candidate; rank 9 is past the top 8
        self.assertEqual([l[3] for l in lines], ["甲乙|丙丁", "庚辛|壬癸"])
        self.assertTrue(all(len(l) == 4 and l[2] for l in lines))
        oc_ref.export(dump, 3, out, pad=64)
        padded = [l.rstrip("\n").split("\t") for l in open(out, encoding="utf-8")]
        self.assertTrue(all(len(l[1]) == 64 for l in padded))
        self.assertEqual([l[1][len(l[1]) - len(o[1]):] for l, o in zip(padded, lines)], [o[1] for o in lines])   # own prefix kept last


if __name__ == "__main__":
    unittest.main()
