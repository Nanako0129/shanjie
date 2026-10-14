"""S7c-1 unit tests for export_sjtl.py (numpy only): header, vocabulary and tensors round trip for both weight types, the int8 restore
equals `q.astype(float32) * scale` of latency.quantize_int8, and the committed golden of tools/gen_tinylm_tiny.py still reads back.
S7c-2c (docs/contracts/s7c2c-delta.md section 3, item 2): without delta the output is byte for byte the committed version 1 golden;
two deltas write version 2; one delta or a bad one is an error and writes nothing.
Rerank strength (docs/contracts/rerank-strength.md sections 2.1 and 9): --levels writes version 3, a 212-byte header whose four extra
sets sit at 116 / 140 / 164 / 188 with the rest equal to version 2; every set is checked like the standard ones.
Run: /opt/homebrew/bin/python3 -m unittest discover -s experiments/s7a-tinylm -p 'test_*.py'
"""
import contextlib
import hashlib
import io
import os
import struct
import sys
import tempfile
import unittest

import numpy as np

import export_sjtl as ex
import latency
import tinylm

sys.path.insert(0, os.path.join(tinylm.ROOT, "tools"))
import gen_tinylm_tiny  # noqa: E402

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

    def test_without_delta_the_output_is_the_committed_golden(self):
        # build() exports every golden file again (version 1 with no delta, version 2, pick.tsv): each equals the committed bytes
        d = os.path.join(tinylm.ROOT, "eval", "golden", "tinylm-tiny")
        files = gen_tinylm_tiny.build()
        self.assertEqual(sorted(files), sorted(n for n in os.listdir(d) if not n.startswith(".")))   # .DS_Store and the like
        for name, b in files.items():
            self.assertEqual(b, open(os.path.join(d, name), "rb").read(), name)
        self.assertEqual(files["tiny-int8.sjtl"][:8], b"SJTL0001")

    def test_two_deltas_write_version_2(self):
        v1 = self.export("int8")
        out = os.path.join(self.td.name, "v2.sjtl")
        self.assertEqual(ex.export(self.npz, out, "int8", (0.7, 2.0, 0.25), (0.5, 1.0, 2.0), SHA), 2)
        raw, raw1 = open(out, "rb").read(), open(v1, "rb").read()
        self.assertEqual((raw[:8], struct.unpack_from("<I", raw, 8)[0]), (b"SJTL0002", 2))
        self.assertEqual(struct.unpack_from("<2d", raw, 100), (0.25, 2.0))
        self.assertEqual((raw[12:100], raw[116:]), (raw1[12:100], raw1[100:]), "the rest is version 1's")
        head, chars, params = ex.read_sjtl(out)
        self.assertEqual((head["version"], head["chat"], head["formal"], head["delta"]), (2, (0.7, 2.0), (0.5, 1.0), (0.25, 2.0)))
        self.assertEqual(ex.read_sjtl(v1)[0]["delta"], (0.0, 0.0))
        _h1, chars1, params1 = ex.read_sjtl(v1)
        self.assertEqual(chars, chars1)
        for k in params:
            np.testing.assert_array_equal(params[k], params1[k], err_msg=k)

    def test_one_delta_or_a_bad_delta_writes_nothing(self):
        out = os.path.join(self.td.name, "bad.sjtl")
        cases = [((0.7, 2.0, 0.25), (0.5, 1.0)), ((0.7, 2.0), (0.5, 1.0, 2.0))]
        for bad in (float("nan"), float("inf"), float("-inf"), -0.25, 1000.5):
            cases += [((0.7, 2.0, bad), (0.5, 1.0, 2.0)), ((0.7, 2.0, 0.25), (0.5, 1.0, bad))]
        for bad in (float("nan"), float("inf")):    # alpha must be finite, tau must not be NaN (both versions)
            cases += [((bad, 2.0), (0.5, 1.0)), ((0.7, 2.0, 0.25), (bad, 1.0, 2.0))]
        cases += [((0.7, float("nan")), (0.5, 1.0)), ((0.7, 2.0, 0.25), (0.5, float("nan"), 2.0))]
        for chat, formal in cases:
            with self.assertRaises(ValueError, msg=(chat, formal)):
                ex.export(self.npz, out, "int8", chat, formal, SHA)
            self.assertFalse(os.path.exists(out), (chat, formal))
        # the command line: one DELTA alone is an error exit, nothing written
        lm = os.path.join(self.td.name, "lm.bin")
        open(lm, "wb").write(b"ngram model")
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            ex.main(["--npz", self.npz, "--out", out, "--type", "int8", "--ngram-model", lm, "--chat", "0.2", "1", "0.25", "--formal", "0.7", "1"])
        self.assertFalse(os.path.exists(out))
        with contextlib.redirect_stdout(io.StringIO()) as so:
            ex.main(["--npz", self.npz, "--out", out, "--type", "int8", "--ngram-model", lm, "--chat", "0.2", "1", "0.25", "--formal", "0.7", "1", "2"])
        self.assertIn("version 2, delta chat 0.25 formal 2.0", so.getvalue())


    LEVELS = (1.125, 3.0, 0.375, 0.875, 1.5, 0.0625, 0.3, 0.75, 1.25, 1.75, 2.25, 0.625)   # 12 distinct values

    def test_levels_write_version_3(self):
        v2 = os.path.join(self.td.name, "v2.sjtl")
        ex.export(self.npz, v2, "int8", (0.7, 2.0, 0.25), (0.5, 1.0, 2.0), SHA)
        out = os.path.join(self.td.name, "v3.sjtl")
        self.assertEqual(ex.export(self.npz, out, "int8", (0.7, 2.0, 0.25), (0.5, 1.0, 2.0), SHA, self.LEVELS), 3)
        raw, raw2 = open(out, "rb").read(), open(v2, "rb").read()
        self.assertEqual((raw[:8], struct.unpack_from("<I", raw, 8)[0]), (b"SJTL0003", 3))
        self.assertEqual(len(raw), len(raw2) + 96)
        # the documented offsets, field by field: chat conservative 116, chat aggressive 140, formal conservative 164, formal aggressive 188
        for i, at in enumerate(range(116, 212, 8)):
            self.assertEqual(struct.unpack_from("<d", raw, at)[0], self.LEVELS[i], at)
        self.assertEqual((raw[12:116], raw[212:]), (raw2[12:116], raw2[116:]), "the rest is version 2's")
        head, chars, params = ex.read_sjtl(out)
        self.assertEqual((head["version"], head["chat"], head["formal"], head["delta"], head["levels"]),
                         (3, (0.7, 2.0), (0.5, 1.0), (0.25, 2.0), self.LEVELS))
        self.assertIsNone(ex.read_sjtl(v2)[0]["levels"])
        _h2, chars2, params2 = ex.read_sjtl(v2)
        self.assertEqual(chars, chars2)
        for k in params:
            np.testing.assert_array_equal(params[k], params2[k], err_msg=k)

    def test_bad_levels_write_nothing(self):
        out = os.path.join(self.td.name, "bad.sjtl")
        std = ((0.7, 2.0, 0.25), (0.5, 1.0, 2.0))
        bad = {0: (float("nan"), float("inf"), float("-inf")), 1: (float("nan"),),
               2: (float("nan"), float("inf"), float("-inf"), -0.25, 1000.5)}
        for i in range(12):
            for x in bad[i % 3]:
                levels = list(self.LEVELS)
                levels[i] = x
                with self.assertRaises(ValueError, msg=(i, x)):
                    ex.export(self.npz, out, "int8", *std, SHA, levels)
                self.assertFalse(os.path.exists(out), (i, x))
        for levels in (self.LEVELS[:11], self.LEVELS + (1.0,)):
            with self.assertRaises(ValueError):
                ex.export(self.npz, out, "int8", *std, SHA, levels)
        with self.assertRaises(ValueError, msg="levels need both deltas"):
            ex.export(self.npz, out, "int8", (0.7, 2.0), (0.5, 1.0), SHA, self.LEVELS)
        self.assertFalse(os.path.exists(out))
        # accepted, as by the Rust reader: -0.0 anywhere, delta 1000, an infinite tau
        for i, x in ((0, -0.0), (2, -0.0), (2, 1000.0), (1, float("inf")), (10, float("inf")), (11, 1000.0)):
            levels = list(self.LEVELS)
            levels[i] = x
            self.assertEqual(ex.export(self.npz, out, "int8", *std, SHA, levels), 3, (i, x))
            os.remove(out)

    def test_levels_on_the_command_line(self):
        out = os.path.join(self.td.name, "cli.sjtl")
        lm = os.path.join(self.td.name, "lm.bin")
        open(lm, "wb").write(b"ngram model")
        args = ["--npz", self.npz, "--out", out, "--type", "int8", "--ngram-model", lm]
        levels = [repr(x) for x in self.LEVELS]
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            ex.main(args + ["--chat", "0.3", "2", "--formal", "0.7", "1", "--levels", *levels])     # no deltas
        bad = list(levels)
        bad[5] = "nan"
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            ex.main(args + ["--chat", "0.3", "2", "0.25", "--formal", "0.7", "1", "2", "--levels", *bad])
        self.assertFalse(os.path.exists(out))
        with contextlib.redirect_stdout(io.StringIO()) as so:
            ex.main(args + ["--chat", "0.3", "2", "0.25", "--formal", "0.7", "1", "2", "--levels", *levels])
        self.assertIn("version 3, delta chat 0.25 formal 2.0, levels 1.125 3.0 0.375", so.getvalue())
        self.assertEqual(ex.read_sjtl(out)[0]["levels"], self.LEVELS)


if __name__ == "__main__":
    unittest.main()
