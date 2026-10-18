"""tools/oc_ref.py on a tiny random model: the file round trip, int8 restore, and the forward's mask / positions logic (one run per row
as a prefix tree must equal one run per candidate, through experiments/x0c/score_0c.py's own feeds)."""
import os
import tempfile
import unittest

import numpy as np

import oc_ref

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
        self.paths = {}
        for w in ("f32", "int8"):
            self.paths[w] = os.path.join(self.td.name, f"t.{w}.sjoc")
            oc_ref.write(self.paths[w], CFG, self.T, CHARS, BASES, NEUTRAL, OUT, w, (bytes(32), b"\x01" * 32))

    def tearDown(self):
        self.td.cleanup()

    def test_round_trip(self):
        m = oc_ref.load(self.paths["f32"])
        self.assertEqual((m["cfg"], m["chars"], m["bases"], m["neutral"], m["out_ids"].tolist(), m["int8"]), (CFG, CHARS, BASES, NEUTRAL, OUT, False))
        self.assertEqual(m["src_sha"], (bytes(32), b"\x01" * 32))
        for k, a in self.T.items():
            np.testing.assert_array_equal(m["T"][k], a)
        q = oc_ref.load(self.paths["int8"])
        self.assertTrue(q["int8"])
        for k, a in self.T.items():
            tol = np.abs(a).max(axis=-1, keepdims=True) / 254 + 1e-7 if a.ndim == 2 else 0
            self.assertTrue((np.abs(q["T"][k] - a) <= tol).all(), k)

    def test_bad_layout_is_refused(self):
        T = dict(self.T)
        T["l1.wq"] = T["l1.wq"][:, :-1]
        with self.assertRaises(ValueError):
            oc_ref.write(os.path.join(self.td.name, "x"), CFG, T, CHARS, BASES, NEUTRAL, OUT, "f32", (bytes(32), bytes(32)))
        with open(self.paths["f32"], "ab") as f:
            f.write(b"\0")
        with self.assertRaises(ValueError):
            oc_ref.load(self.paths["f32"])

    def test_prefix_tree_equals_one_run_per_candidate(self):
        s0, z = oc_ref.zuin(self.paths["f32"])
        cands = ["天氣", "天好", "很好", "真的"]
        for ctx in ("", "天氣很好真的" * 12):                                  # empty and longer than the 64-character window
            for reading in (["ㄊㄧㄢ", "ㄏㄠˇ"], ["ㄊㄧㄢ", "ㄇㄚ"]):              # the second one takes the neutral retry
                a = s0.score(z, ctx, reading, cands, packed=True)
                b = s0.score(z, ctx, reading, cands, packed=False)
                np.testing.assert_allclose(a, b, rtol=0, atol=1e-4)
                self.assertTrue(all(np.isfinite(a)) and all(x < 0 for x in a))


if __name__ == "__main__":
    unittest.main()
