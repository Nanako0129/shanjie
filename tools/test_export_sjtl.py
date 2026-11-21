"""S7c-1 unit tests for export_sjtl.py (numpy only): header, vocabulary and tensors round trip for both weight types, the int8 restore
equals `q.astype(float32) * scale` of latency.quantize_int8, and the committed golden of tools/gen_tinylm_tiny.py still reads back.
S7c-2c (docs/spec/semantic-rerank.md §10): without delta the output is byte for byte the committed version 1 golden;
two deltas write version 2; one delta or a bad one is an error and writes nothing.
Rerank strength (docs/spec/semantic-rerank.md §2.1, §10): `export` with levels writes version 3, a 212-byte header whose
four extra sets sit at 116 / 140 / 164 / 188 with the rest equal to version 2; every set is checked like the standard ones.
model-v6 (docs/spec/models.md §3.5, §8.1): on the command line --levels writes version 4 (version 3 plus, at
212, the SHA-256 of the --kn-side file, or 32 zero bytes without it); without --levels version 1 / 2 as before; --kn-side alone is a
usage error; a side file that is not SJKN0002 or records another model is refused before anything is written; read_sjtl reads v4.
Run: /opt/homebrew/bin/python3 -m unittest tools/test_export_sjtl.py
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

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
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
        self.assertIn("version 4, delta chat 0.25 formal 2.0, levels 1.125 3.0 0.375", so.getvalue())
        self.assertEqual(ex.read_sjtl(out)[0]["levels"], self.LEVELS)

    # ---- docs/spec/models.md §3.5, §8.1: --kn-side and the version rule of the command line ----

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


LV = (0.3, 2.0, 0.25, 0.5, 1.0, 1.5, 0.5, 1.0, 0.25, 0.7, 2.0, 2.0)   # --levels values (12): any valid ones


class TestBoundaryVersions(unittest.TestCase):
    """sep-longkey: versions 5 and 6 (docs/spec/semantic-rerank.md §2.1)."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.plain = os.path.join(self.td.name, "plain.npz")
        self.sep = os.path.join(self.td.name, "sep.npz")
        tinylm.save_params(self.plain, tinylm.random_params(list(CHARS), n_layer=2, d=8, n_head=2, seed=3), CHARS, n_layer=2, n_head=2)
        tinylm.save_params(self.sep, tinylm.random_params(list(CHARS), n_layer=2, d=8, n_head=2, seed=3, n_extra=1), CHARS, n_layer=2,
                           n_head=2, extra_tokens=["[SEP]"])

    def tearDown(self):
        self.td.cleanup()

    def write(self, npz, name, **kw):
        out = os.path.join(self.td.name, name)
        ex.export(npz, out, "int8", (0.7, 2.0, 0.25), (0.5, 1.0, 2.0), SHA, LV, bytes(32), **kw)
        return out

    def test_version_5_and_6_round_trip(self):
        v5 = self.write(self.plain, "v5.sjtl", left_len=62)
        h, chars, p5 = ex.read_sjtl(v5)
        self.assertEqual((h["version"], h["left_len"], h["flags"], h["boundary_k"], "".join(chars), h["V"]), (5, 62, 0, None, CHARS, 11))
        self.assertEqual(open(v5, "rb").read()[:8], b"SJTL0005")
        v6 = self.write(self.sep, "v6.sjtl", left_len=62, boundary_k=4)
        h, chars, p6 = ex.read_sjtl(v6)
        self.assertEqual((h["version"], h["left_len"], h["flags"], h["boundary_k"], "".join(chars), h["V"]), (6, 62, 1, 4, CHARS, 12))
        raw = open(v6, "rb").read()
        self.assertEqual(struct.unpack_from("<4I", raw, 244), (62, 1, 4, ord(CHARS[0])))      # L, flags, K, then the first character
        self.assertEqual(p6["emb"].shape, (12, 8))
        self.assertEqual(h["levels"], LV)
        # the weights read back are the shared int8 quantizer's restore of the npz
        q = os.path.join(self.td.name, "q.npz")
        latency.quantize_int8(self.sep, q)
        z = np.load(q)
        np.testing.assert_array_equal(p6["emb"], z["emb"].astype(np.float32) * z["emb.scale"])

    def test_usage_and_range_errors_write_nothing(self):
        out = os.path.join(self.td.name, "bad.sjtl")
        base = ((0.7, 2.0, 0.25), (0.5, 1.0, 2.0), SHA)
        bad = [
            (self.plain, dict(levels=None, kn_sha=None, left_len=62)),                  # no --levels
            (self.plain, dict(levels=LV, kn_sha=bytes(32), left_len=0)),
            (self.plain, dict(levels=LV, kn_sha=bytes(32), left_len=63)),               # ctx - 2 = 62
            (self.sep, dict(levels=LV, kn_sha=bytes(32), left_len=62, boundary_k=0)),
            (self.sep, dict(levels=LV, kn_sha=bytes(32), left_len=62, boundary_k=63)),
            (self.sep, dict(levels=LV, kn_sha=bytes(32), boundary_k=4)),                # --boundary without --left-len
            (self.plain, dict(levels=LV, kn_sha=bytes(32), left_len=62, boundary_k=4)),  # no extra_tokens in the npz
            (self.sep, dict(levels=LV, kn_sha=bytes(32), left_len=62)),                 # extra_tokens without --boundary
        ]
        for npz, kw in bad:
            with self.assertRaises(ValueError, msg=kw):
                ex.export(npz, out, "int8", *base[:2], base[2], **kw)
            self.assertFalse(os.path.exists(out), kw)
        wrong = os.path.join(self.td.name, "wrong.npz")
        tinylm.save_params(wrong, tinylm.random_params(list(CHARS), n_layer=2, d=8, n_head=2, seed=3, n_extra=1), CHARS, n_layer=2, n_head=2,
                           extra_tokens=["[RD]"])
        with self.assertRaises(ValueError):
            ex.export(wrong, out, "int8", *base[:2], base[2], levels=LV, kn_sha=bytes(32), left_len=62, boundary_k=4)
        self.assertFalse(os.path.exists(out))

    def test_command_line(self):
        ng = os.path.join(self.td.name, "bigram.sjlm")
        open(ng, "wb").write(b"ngram model")
        out = os.path.join(self.td.name, "cli.sjtl")
        argv = ["--npz", self.sep, "--out", out, "--type", "int8", "--ngram-model", ng, "--chat", "0.7", "2", "0.25", "--formal", "0.5", "1", "2",
                "--levels", *map(str, LV)]
        with contextlib.redirect_stdout(io.StringIO()) as so:
            ex.main(argv + ["--left-len", "62", "--boundary", "4"])
        self.assertIn("version 6", so.getvalue())
        self.assertIn("boundary K 4", so.getvalue())
        self.assertEqual(ex.read_sjtl(out)[0]["boundary_k"], 4)
        for extra in (["--boundary", "4"], ["--left-len", "62", "--boundary", "99"]):
            if os.path.exists(out):
                os.remove(out)
            with self.assertRaises((SystemExit, ValueError)), contextlib.redirect_stderr(io.StringIO()):
                ex.main(argv + extra)
            self.assertFalse(os.path.exists(out), extra)
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):       # --left-len without --levels
            ex.main(argv[:argv.index("--levels")] + ["--left-len", "62"])


class TestSepKeys(unittest.TestCase):
    """The numpy reference reads [SEP]; the committed goldens (items-v6.tsv, sep-keys/keys.tsv) are what the generators write again."""

    def test_committed_goldens_are_what_the_generators_write(self):
        import gen_sep_keys
        g = os.path.join(tinylm.ROOT, "eval", "golden")
        for name, b in gen_tinylm_tiny.build().items():
            if name.endswith("v6.tsv") or name.endswith("v6.sjtl"):
                self.assertEqual(b, open(os.path.join(g, "tinylm-tiny", name), "rb").read(), name)
        for name, b in gen_sep_keys.build().items():
            self.assertEqual(b, open(os.path.join(g, "sep-keys", name), "rb").read(), name)

    def test_key_b_definitions(self):
        han = lambda c: c in "好熱今天真"   # noqa: E731
        key = lambda b, n=1, L=62, K=1: tinylm.key_b(b, n, L, K, han).replace("\0", "|")   # noqa: E731
        self.assertEqual(key("今天好熱。"), "今天好熱|")
        self.assertEqual(key("好熱，今天真", K=1), "好熱|今天真")
        self.assertEqual(key("，，A1 好"), "好")
        self.assertEqual(key("「」"), "")
        self.assertEqual(key("好熱，今天真", K=9), "天真")                  # the shipped key: the last 2 Han
        self.assertEqual(key("好熱，今天真，", K=9), "")                    # ... empty after punctuation
        self.assertEqual(key("好，" * 31, n=2), "好|" * 30)                  # 61 tokens kept, a leading | dropped
        self.assertEqual(key("好熱", n=63), "")
        self.assertEqual(key("好好好好好A好熱", L=3), "好熱")                 # only the last L characters count: "A好熱" -> the leading | dropped
        self.assertEqual(tinylm.key_b("好\0熱", 1, 62, 1, han), "好\0熱")       # NUL in the text is a boundary, one
        # the 64-token window: [BOS] + key + candidate never exceeds the context for n = len(candidate)
        for n in (1, 5, 40, 62):
            self.assertLessEqual(1 + len(tinylm.key_b("好熱，" * 40, n, 62, 1, han)) + n, 64)

    def test_ll_key_b_reads_nul_as_sep_only_in_the_key(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        npz = os.path.join(td.name, "m.npz")
        tinylm.save_params(npz, tinylm.random_params(list(CHARS), 1, 8, 2, seed=5, n_extra=1), CHARS, 1, 2, extra_tokens=["[SEP]"])
        m = tinylm.NumpyLM(npz)
        self.assertEqual(m.vocab.sep_id, len(CHARS) + 3)
        a = tinylm.ll_key_b(m, "甲\0乙", "丙")
        self.assertNotEqual(a, tinylm.ll_many(m, [("甲\0乙", "丙")])[0])     # without sep_keys a NUL in the key is UNK
        self.assertEqual(tinylm.ll_many(m, [("", "丙\0")], sep_keys=True)[0], tinylm.ll_many(m, [("", "丙\0")])[0])   # a candidate's NUL: UNK


if __name__ == "__main__":
    unittest.main()
