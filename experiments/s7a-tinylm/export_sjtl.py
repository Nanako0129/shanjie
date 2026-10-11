"""S7c-1: tinylm .npz (`tinylm.save_params`) -> SJTL0001, the model file core/src/tinylm.rs reads. Written only here, read only there.

  python export_sjtl.py --npz tinylm-M.npz --out tinylm-M8.sjtl --type int8 --ngram-model data/lm/bigram.sjlm \
                        --chat 0.7 2 --formal 0.7 2

Layout (little-endian; docs/contracts/s7c-core-tinylm.md section 2.1):
  header (100 bytes)  magic "SJTL0001" | u32 version (1) | u32 weight type (0 f32, 1 int8) | u32 n_layer, n_head, d, ctx, V
                      | f64 chat alpha, chat tau, formal alpha, formal tau | 32 bytes SHA-256 of the n-gram model alpha / tau were tuned with
  vocab               (V - 3) x u32 code points, ids 3 on (ids 0 / 1 / 2 are PAD / UNK / BOS, as in tinylm.Vocab)
  tensors, in this order (no per-tensor header; the shapes come from the header; keep this list identical to core/src/tinylm.rs):
                      emb (V, d), pos (ctx, d), lnf_w (d), lnf_b (d), then for each layer 0 .. n_layer - 1, tinylm.LAYER_KEYS:
                      ln1_w (d), ln1_b (d), qkv_w (d, 3d), qkv_b (3d), proj_w (d, d), proj_b (d), ln2_w (d), ln2_b (d),
                      fc1_w (d, 4d), fc1_b (4d), fc2_w (4d, d), fc2_b (d)
  a 2-D matrix is f32 row-major (type f32), or int8 row-major followed by one f32 scale per row (type int8; latency.quantize_int8:
  per-row symmetric, scale = max(|row|, 1e-12) / 127, restored as q.astype(float32) * scale); a 1-D vector is always f32.
"""
import argparse
import hashlib
import os
import struct
import tempfile

import numpy as np

import tinylm
from latency import quantize_int8

MAGIC = b"SJTL0001"
VERSION = 1
F32, INT8 = 0, 1
HEADER = struct.Struct("<8s7I4d32s")


def tensor_shapes(n_layer, d, ctx, V):
    """[(name, shape)] in file order."""
    out = [("emb", (V, d)), ("pos", (ctx, d)), ("lnf_w", (d,)), ("lnf_b", (d,))]
    layer = {"ln1_w": (d,), "ln1_b": (d,), "qkv_w": (d, 3 * d), "qkv_b": (3 * d,), "proj_w": (d, d), "proj_b": (d,),
             "ln2_w": (d,), "ln2_b": (d,), "fc1_w": (d, 4 * d), "fc1_b": (4 * d,), "fc2_w": (4 * d, d), "fc2_b": (d,)}
    for i in range(n_layer):
        out += [(f"l{i}.{k}", layer[k]) for k in tinylm.LAYER_KEYS]
    return out


def export(npz, out, wtype, chat, formal, ngram_sha):
    """Write `out` from the .npz at `npz`. wtype "f32" | "int8"; chat / formal = (alpha, tau); ngram_sha = 32 bytes."""
    z = np.load(npz, allow_pickle=False)
    n_layer, n_head, ctx = (int(x) for x in z["cfg"])
    chars = [str(c) for c in z["chars"]]
    tinylm.Vocab(chars)                                   # no duplicates, one code point each
    d = z["emb"].shape[1]
    V = len(chars) + 3
    assert d % n_head == 0 and len(ngram_sha) == 32 and wtype in ("f32", "int8")
    shapes = tensor_shapes(n_layer, d, ctx, V)
    assert sorted(n for n, _ in shapes) == sorted(k for k in z.files if k not in ("cfg", "chars")), "npz keys differ from the model layout"
    if wtype == "int8":
        with tempfile.TemporaryDirectory() as tmp:
            quantize_int8(npz, os.path.join(tmp, "q.npz"))    # the one quantizer (S7b section 2.3); never reimplemented here
            z = dict(np.load(os.path.join(tmp, "q.npz"), allow_pickle=False))
    parts = [HEADER.pack(MAGIC, VERSION, F32 if wtype == "f32" else INT8, n_layer, n_head, d, ctx, V,
                         chat[0], chat[1], formal[0], formal[1], bytes(ngram_sha)),
             struct.pack(f"<{V - 3}I", *(ord(c) for c in chars))]
    for name, shape in shapes:
        assert z[name].shape == shape, f"{name}: shape {z[name].shape} != {shape}"
        if len(shape) == 2 and wtype == "int8":
            assert z[name].dtype == np.int8 and z[name + ".scale"].shape == (shape[0], 1)
            parts += [z[name].tobytes(), z[name + ".scale"].astype("<f4").tobytes()]
        else:
            parts.append(z[name].astype("<f4").tobytes())
    with open(out, "wb") as f:
        f.write(b"".join(parts))


def read_sjtl(path):
    """The reader the tests and the golden generator use: (header dict, chars, params) with params as float32 arrays, int8 matrices
    restored as q.astype(float32) * scale. Python only; the Rust loader is core/src/tinylm.rs."""
    b = open(path, "rb").read()
    magic, version, wt, n_layer, n_head, d, ctx, V, ca, ct, fa, ft, sha = HEADER.unpack_from(b, 0)
    assert magic == MAGIC and version == VERSION and wt in (F32, INT8)
    at = HEADER.size
    chars = [chr(c) for c in struct.unpack_from(f"<{V - 3}I", b, at)]
    at += 4 * (V - 3)
    params = {}
    for name, shape in tensor_shapes(n_layer, d, ctx, V):
        n = int(np.prod(shape))
        if len(shape) == 2 and wt == INT8:
            q = np.frombuffer(b, np.int8, n, at).reshape(shape)
            at += n
            scale = np.frombuffer(b, "<f4", shape[0], at).reshape(shape[0], 1)
            at += 4 * shape[0]
            params[name] = q.astype(np.float32) * scale
        else:
            params[name] = np.frombuffer(b, "<f4", n, at).reshape(shape).astype(np.float32)
            at += 4 * n
    assert at == len(b), "trailing bytes"
    head = dict(version=version, wtype=wt, n_layer=n_layer, n_head=n_head, d=d, ctx=ctx, V=V, chat=(ca, ct), formal=(fa, ft), ngram_sha=sha)
    return head, chars, params


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--npz", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--type", choices=["f32", "int8"], required=True)
    ap.add_argument("--ngram-model", required=True, help="the bigram.sjlm alpha / tau were tuned with; its SHA-256 goes into the header")
    ap.add_argument("--chat", type=float, nargs=2, metavar=("ALPHA", "TAU"), required=True)
    ap.add_argument("--formal", type=float, nargs=2, metavar=("ALPHA", "TAU"), required=True)
    a = ap.parse_args(argv)
    with open(a.ngram_model, "rb") as f:
        sha = hashlib.sha256(f.read()).digest()
    export(a.npz, a.out, a.type, a.chat, a.formal, sha)
    print(f"wrote {a.out} ({os.path.getsize(a.out)} bytes, {a.type}); sjtl sha256 {hashlib.sha256(open(a.out, 'rb').read()).hexdigest()}")


if __name__ == "__main__":
    main()
