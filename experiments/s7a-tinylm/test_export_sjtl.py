"""S7c-1 unit tests for export_sjtl.py (numpy only): header, vocabulary and tensors round trip for both weight types, the int8 restore
equals `q.astype(float32) * scale` of latency.quantize_int8, and the committed golden of tools/gen_tinylm_tiny.py still reads back.
Run: /opt/homebrew/bin/python3 -m unittest discover -s experiments/s7a-tinylm -p 'test_*.py'
"""
import hashlib
import os
import struct
import tempfile
import unittest

import numpy as np

import export_sjtl as ex
import latency
import tinylm

CHARS = "甲乙丙丁戊己庚辛"
SHA = hashlib.sha256(b"ngram model").digest()


class TestExport(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.npz = os.path.join(self.td.name, "m.npz")
        self.params = tinylm.random_params(list(CHARS), n_layer=2, d=8, n_head=2, seed=3)
        tinylm.save_params(self.npz, self.params, CHARS, n_layer=2, n_head=2)

    def tearDown(self):
        self.td.cleanup()

    def export(self, wtype):
        out = os.path.join(self.td.name, f"m-{wtype}.sjtl")
        ex.export(self.npz, out, wtype, (0.7, 2.0), (0.5, 1.0), SHA)
        return out

    def test_header_vocab_and_f32_tensors_round_trip(self):
        out = self.export("f32")
        raw = open(out, "rb").read()
        self.assertEqual(raw[:8], b"SJTL0001")
        self.assertEqual(struct.unpack_from("<7I", raw, 8), (1, 0, 2, 2, 8, tinylm.CTX, len(CHARS) + 3))
        head, chars, params = ex.read_sjtl(out)
        self.assertEqual((head["chat"], head["formal"], head["ngram_sha"]), ((0.7, 2.0), (0.5, 1.0), SHA))
        self.assertEqual("".join(chars), CHARS)
        z = np.load(self.npz)
        self.assertEqual(sorted(params), sorted(k for k in z.files if k not in ("cfg", "chars")))
        for k, v in params.items():
            np.testing.assert_array_equal(v, z[k], err_msg=k)

    def test_int8_restore_is_q_times_scale_of_the_shared_quantizer(self):
        out = self.export("int8")
        q = os.path.join(self.td.name, "q.npz")
        latency.quantize_int8(self.npz, q)
        z, orig = np.load(q), np.load(self.npz)
        head, _chars, params = ex.read_sjtl(out)
        self.assertEqual(head["wtype"], ex.INT8)
        n2 = 0
        for k, v in params.items():
            if orig[k].ndim == 2:
                n2 += 1
                np.testing.assert_array_equal(v, z[k].astype(np.float32) * z[k + ".scale"], err_msg=k)
                self.assertTrue(np.abs(v - orig[k]).max() <= np.abs(orig[k]).max(axis=1).max() / 127 / 2 + 1e-7)
            else:
                np.testing.assert_array_equal(v, orig[k], err_msg=k)
        self.assertEqual(n2, 2 + 2 * 4)                                  # emb, pos, and qkv / proj / fc1 / fc2 of two layers
        self.assertLess(os.path.getsize(out), os.path.getsize(self.export("f32")))

    def test_tensor_order_is_the_documented_one(self):
        names = [n for n, _ in ex.tensor_shapes(2, 8, 64, 11)]
        self.assertEqual(names[:4], ["emb", "pos", "lnf_w", "lnf_b"])
        self.assertEqual(names[4:16], [f"l0.{k}" for k in tinylm.LAYER_KEYS])
        self.assertEqual(names[16:], [f"l1.{k}" for k in tinylm.LAYER_KEYS])

    def test_wrong_shapes_and_keys_are_refused(self):
        z = dict(np.load(self.npz))
        z["emb"] = z["emb"][:-1]
        bad = os.path.join(self.td.name, "bad.npz")
        np.savez(bad, **z)
        with self.assertRaises(ValueError):
            ex.export(bad, os.path.join(self.td.name, "x.sjtl"), "f32", (0.7, 2.0), (0.7, 2.0), SHA)

    def test_committed_golden_reads_back(self):
        d = os.path.join(tinylm.ROOT, "eval", "golden", "tinylm-tiny")
        rows = [l.split("\t") for l in open(os.path.join(d, "items.tsv"), encoding="utf-8").read().split("\n") if l]
        for col, wtype in ((2, "f32"), (3, "int8")):
            path = os.path.join(d, f"tiny-{wtype}.sjtl")
            _h, chars, params = ex.read_sjtl(path)
            npz = os.path.join(self.td.name, f"g-{wtype}.npz")
            tinylm.save_params(npz, params, chars, n_layer=2, n_head=2)
            got = tinylm.ll_many(tinylm.NumpyLM(npz), [(r[0], r[1]) for r in rows])
            np.testing.assert_allclose(got, [float(r[col]) for r in rows], rtol=0, atol=1e-9)


if __name__ == "__main__":
    unittest.main()
