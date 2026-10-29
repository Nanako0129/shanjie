"""S7c-1 unit tests for export_sjtl.py (numpy only): header, vocabulary and tensors round trip for both weight types, the int8 restore
equals `q.astype(float32) * scale` of latency.quantize_int8, and the committed golden of tools/gen_tinylm_tiny.py still reads back.
S7c-2c (docs/contracts/s7c2c-delta.md section 3, item 2): without delta the output is byte for byte the committed version 1 golden;
two deltas write version 2; one delta or a bad one is an error and writes nothing.
Rerank strength (docs/contracts/rerank-strength.md sections 2.1 and 9): `export` with levels writes version 3, a 212-byte header whose
four extra sets sit at 116 / 140 / 164 / 188 with the rest equal to version 2; every set is checked like the standard ones.
model-v6 (docs/contracts/model-v6.md sections 2.7 and 7, P3-5): on the command line --levels writes version 4 (version 3 plus, at
212, the SHA-256 of the --kn-side file, or 32 zero bytes without it); without --levels version 1 / 2 as before; --kn-side alone is a
usage error; a side file that is not SJKN0002 or records another model is refused before anything is written; read_sjtl reads v4.
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

    def test_a_nul_in_the_vocabulary_keeps_its_id(self):
        # numpy's <U1 reads a saved U+0000 back as "": the export restores it instead of refusing the vocabulary (CTX64's raw text)
        chars = "甲\x00丙丁戊己庚辛"
        npz = os.path.join(self.td.name, "nul.npz")
        tinylm.save_params(npz, tinylm.random_params(list(chars), n_layer=2, d=8, n_head=2, seed=3), chars, n_layer=2, n_head=2)
        self.assertEqual(str(np.load(npz)["chars"][1]), "")
        out = os.path.join(self.td.name, "nul.sjtl")
        ex.export(npz, out, "f32", (0.7, 2.0), (0.5, 1.0), SHA)
        self.assertEqual("".join(ex.read_sjtl(out)[1]), chars)

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
        self.assertIn("version 4, delta chat 0.25 formal 2.0, levels 1.125 3.0 0.375", so.getvalue())
        self.assertEqual(ex.read_sjtl(out)[0]["levels"], self.LEVELS)

    # ---- model-v6 section 7, P3-5: --kn-side and the version rule of the command line ----

    def cli(self, out, *extra, levels=True, deltas=True):
        """main() with this test's model; returns stdout. `lm.bin` is the n-gram model (its SHA-256 is SHA)."""
        lm = os.path.join(self.td.name, "lm.bin")
        open(lm, "wb").write(b"ngram model")
        args = ["--npz", self.npz, "--out", out, "--type", "int8", "--ngram-model", lm,
                "--chat", "0.3", "2", *(["0.25"] if deltas else []), "--formal", "0.7", "1", *(["2"] if deltas else [])]
        if levels:
            args += ["--levels", *(repr(x) for x in self.LEVELS)]
        with contextlib.redirect_stdout(io.StringIO()) as so:
            ex.main(args + list(extra))
        return so.getvalue()

    def side(self, magic=b"SJKN0002", model_sha=SHA, name="kn.sjkn"):
        """A synthetic side file: only the magic and the recorded model hash (offset 12) are what export_sjtl checks."""
        path = os.path.join(self.td.name, name)
        open(path, "wb").write(magic + struct.pack("<I", 11) + model_sha + b"rest of the side file")
        return path

    def v3_bytes(self):
        path = os.path.join(self.td.name, "ref-v3.sjtl")
        ex.export(self.npz, path, "int8", (0.3, 2.0, 0.25), (0.7, 1.0, 2.0), SHA, self.LEVELS)
        return open(path, "rb").read()

    def test_levels_with_kn_side_write_version_4_with_its_hash(self):
        out, side = os.path.join(self.td.name, "v4.sjtl"), self.side()
        so = self.cli(out, "--kn-side", side)
        raw, v3 = open(out, "rb").read(), self.v3_bytes()
        want = hashlib.sha256(open(side, "rb").read()).digest()
        self.assertEqual((raw[:8], struct.unpack_from("<I", raw, 8)[0]), (b"SJTL0004", 4))
        self.assertEqual(raw[212:244], want, "bytes 212..244 are the side file's SHA-256")
        self.assertEqual((len(raw), raw[12:212], raw[244:]), (len(v3) + 32, v3[12:212], v3[212:]), "the rest is version 3's")
        self.assertIn(f"version 4, delta chat 0.25 formal 2.0, levels 1.125", so)
        self.assertIn(f"kn.sjkn sha256 {want.hex()}", so)
        head, chars, params = ex.read_sjtl(out)
        self.assertEqual((head["version"], head["levels"], head["kn_sha"], head["ngram_sha"]), (4, self.LEVELS, want, SHA))
        _h3, chars3, params3 = ex.read_sjtl(os.path.join(self.td.name, "ref-v3.sjtl"))
        self.assertEqual(chars, chars3)
        for k in params:
            np.testing.assert_array_equal(params[k], params3[k], err_msg=k)

    def test_levels_without_kn_side_write_version_4_all_zero(self):
        out = os.path.join(self.td.name, "v4.sjtl")
        self.assertIn("none (tuned without Kneser-Ney)", self.cli(out))
        raw = open(out, "rb").read()
        self.assertEqual((raw[:8], raw[212:244]), (b"SJTL0004", bytes(32)))
        self.assertEqual(raw[244:], self.v3_bytes()[212:])
        self.assertEqual(ex.read_sjtl(out)[0]["kn_sha"], bytes(32))
        self.assertEqual(ex.read_sjtl(os.path.join(self.td.name, "ref-v3.sjtl"))[0]["kn_sha"], bytes(32), "version 3: none")

    def test_without_levels_the_output_is_version_1_or_2_as_before(self):
        for deltas, version in ((False, 1), (True, 2)):
            out, ref = os.path.join(self.td.name, f"cli-{version}.sjtl"), os.path.join(self.td.name, f"ref-{version}.sjtl")
            self.assertIn(f"version {version}", self.cli(out, levels=False, deltas=deltas))
            chat, formal = ((0.3, 2.0, 0.25), (0.7, 1.0, 2.0)) if deltas else ((0.3, 2.0), (0.7, 1.0))
            self.assertEqual(ex.export(self.npz, ref, "int8", chat, formal, SHA), version)
            self.assertEqual(open(out, "rb").read(), open(ref, "rb").read())

    def test_kn_side_alone_and_bad_side_files_write_nothing(self):
        out = os.path.join(self.td.name, "bad.sjtl")
        cases = [(dict(levels=False), self.side()),                                      # --kn-side without --levels
                 (dict(levels=False, deltas=False), self.side()),
                 ({}, self.side(magic=b"SJKN0001", name="old.sjkn")),                   # not SJKN0002
                 ({}, self.side(model_sha=hashlib.sha256(b"other").digest(), name="other.sjkn"))]   # another model's side file
        for kw, side in cases:
            with self.assertRaises(SystemExit, msg=(kw, side)), contextlib.redirect_stderr(io.StringIO()):
                self.cli(out, "--kn-side", side, **kw)
            self.assertFalse(os.path.exists(out), (kw, side))
        with self.assertRaises(ValueError):            # the library refuses a hash without levels too
            ex.export(self.npz, out, "int8", (0.3, 2.0, 0.25), (0.7, 1.0, 2.0), SHA, None, bytes(32))
        self.assertFalse(os.path.exists(out))

    def test_committed_v4_golden_records_hashlib_of_kn_tiny(self):
        g = os.path.join(tinylm.ROOT, "eval", "golden")
        raw = open(os.path.join(g, "tinylm-tiny", "tiny-int8-v4.sjtl"), "rb").read()
        side = open(os.path.join(g, "kn-tiny", "kn.sjkn"), "rb").read()
        self.assertEqual(raw[212:244], hashlib.sha256(side).digest())
        self.assertEqual(ex.read_sjtl(os.path.join(g, "tinylm-tiny", "tiny-int8-v4.sjtl"))[0]["kn_sha"], hashlib.sha256(side).digest())

    def test_left_len_writes_version_5_like_the_rust_reader_expects(self):
        """core/tests/tinylm_tiny.rs `v5`: the version 4 file with magic SJTL0005 and version 5, then L (u32) at 244, then the rest."""
        v4, v5 = os.path.join(self.td.name, "v4.sjtl"), os.path.join(self.td.name, "v5.sjtl")
        self.cli(v4)
        so = self.cli(v5, "--left-len", "8")
        b4, b5 = open(v4, "rb").read(), open(v5, "rb").read()
        self.assertEqual(b5, b"SJTL0005" + struct.pack("<I", 5) + b4[12:244] + struct.pack("<I", 8) + b4[244:])
        self.assertEqual(struct.unpack_from("<I", b5, 244)[0], 8)
        self.assertIn("version 5", so)
        self.assertIn("left length 8", so)
        head, chars, params = ex.read_sjtl(v5)
        self.assertEqual((head["version"], head["left_len"], head["levels"]), (5, 8, self.LEVELS))
        self.assertIsNone(ex.read_sjtl(v4)[0]["left_len"])
        for k, v in ex.read_sjtl(v4)[2].items():
            np.testing.assert_array_equal(params[k], v, err_msg=k)

    def test_left_len_bounds_and_requirements_write_nothing(self):
        out = os.path.join(self.td.name, "bad.sjtl")
        ctx = tinylm.CTX
        for l in (1, ctx - 2):
            self.cli(out, "--left-len", str(l))
            self.assertEqual(ex.read_sjtl(out)[0]["left_len"], l)
            os.remove(out)
        for l in (0, ctx - 1, ctx, -1):                 # the Rust reader refuses L == 0 and L > ctx - 2
            with self.assertRaises(ValueError, msg=l):
                self.cli(out, "--left-len", str(l))
            self.assertFalse(os.path.exists(out), l)
        self.cli(out, "--left-len", "8")                  # read_sjtl refuses what the Rust reader refuses
        good = open(out, "rb").read()
        os.remove(out)
        for l in (0, ctx - 1, 2 ** 32 - 1):
            open(out, "wb").write(good[:244] + struct.pack("<I", l) + good[248:])
            with self.assertRaises(ValueError, msg=l):
                ex.read_sjtl(out)
        os.remove(out)
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):       # needs --levels
            self.cli(out, "--left-len", "8", levels=False)
        with self.assertRaises(ValueError):            # the library: needs the version 4 hash too
            ex.export(self.npz, out, "int8", (0.3, 2.0, 0.25), (0.7, 1.0, 2.0), SHA, self.LEVELS, None, 8)
        self.assertFalse(os.path.exists(out))


if __name__ == "__main__":
    unittest.main()
