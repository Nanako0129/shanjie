"""S7c-1: tinylm .npz (`tinylm.save_params`) -> SJTL0001 / SJTL0002 / SJTL0003, the model file core/src/tinylm.rs reads. Written only
here, read only there.

  python export_sjtl.py --npz tinylm-M.npz --out tinylm-M8.sjtl --type int8 --ngram-model data/lm/bigram.sjlm \
                        --chat 0.7 2 --formal 0.7 2                 # version 1 (no delta: the S7a rule)
                        --chat 0.2 1 0.25 --formal 0.7 1 2          # version 2 (S7c-2c: with the S7e delta of each profile)
                        --chat 0.3 2 0.25 --formal 0.7 1 2 \
                        --levels 1 2 3  0.3 2 0.25  0.5 1 1.5  0.5 1 0.25   # version 3 (rerank strength)

Both DELTAs or neither (one alone is an error: it would silently be 0); a delta must lie in [0, 1000]. --levels takes four
ALPHA TAU DELTA sets in this order: chat conservative, chat aggressive, formal conservative, formal aggressive; it needs both DELTAs.
Every set (the standard ones and the four) passes the same check as the Rust reader: alpha finite, tau not NaN, delta in [0, 1000].

Layout (little-endian; docs/contracts/s7c-core-tinylm.md section 2.1, version 2 in docs/contracts/s7c2c-delta.md section 2.1):
  header (100 bytes)  magic "SJTL0001" | u32 version (1) | u32 weight type (0 f32, 1 int8) | u32 n_layer, n_head, d, ctx, V
                      | f64 chat alpha, chat tau, formal alpha, formal tau | 32 bytes SHA-256 of the n-gram model alpha / tau were tuned with
  version 2 (116)     the same 100 bytes with magic "SJTL0002" and version 2, then f64 chat delta, f64 formal delta (offsets 100, 108);
                      everything after the header is the same in both versions
  version 3 (212)     the same 116 bytes with magic "SJTL0003" and version 3, then f64 alpha, tau, delta of chat conservative (116,
                      124, 132), chat aggressive (140, 148, 156), formal conservative (164, 172, 180), formal aggressive (188, 196,
                      204) (docs/contracts/rerank-strength.md section 2.1); everything after the header is the same in all versions
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
import math
import os
import struct
import tempfile

import numpy as np

import tinylm
from latency import quantize_int8

MAGIC = b"SJTL0001"
VERSION = 1
MAGIC_V2, VERSION_V2 = b"SJTL0002", 2
DELTAS = struct.Struct("<2d")     # version 2: chat delta, formal delta, right after HEADER
MAGIC_V3, VERSION_V3 = b"SJTL0003", 3
LEVELS = struct.Struct("<12d")    # version 3: four (alpha, tau, delta), right after DELTAS
MAX_DELTA = 1000.0                # the range core/src/tinylm.rs accepts
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


def check_params(chat, formal, levels=None):
    """Raise ValueError unless the header's fusion floats are ones the Rust reader loads (core/src/tinylm.rs `valid_params`: alpha
    finite, tau not NaN, delta in [0, MAX_DELTA]), the same check for every set, the four of `levels` included; a file it refuses
    would make reranking silently do nothing."""
    if {len(chat), len(formal)} not in ({2}, {3}):
        raise ValueError("give a delta for both profiles or for neither")
    sets = [chat, formal]
    if levels is not None:
        if len(chat) != 3 or len(levels) != 12:
            raise ValueError("levels need a delta for both profiles and 12 values (4 sets of ALPHA TAU DELTA)")
        sets += [levels[i:i + 3] for i in range(0, 12, 3)]
    for p in sets:
        if not (math.isfinite(p[0]) and not math.isnan(p[1])):
            raise ValueError(f"alpha must be finite and tau not NaN: {list(p[:2])}")
        if len(p) == 3 and not 0 <= p[2] <= MAX_DELTA:      # also false for NaN
            raise ValueError(f"delta must lie in [0, {MAX_DELTA:g}]: {p[2]}")


def export(npz, out, wtype, chat, formal, ngram_sha, levels=None):
    """Write `out` from the .npz at `npz`. wtype "f32" | "int8"; chat / formal = (alpha, tau) for version 1, or both
    (alpha, tau, delta) for version 2; with `levels` (12 floats: chat conservative, chat aggressive, formal conservative, formal
    aggressive, each alpha, tau, delta) version 3; ngram_sha = 32 bytes. Returns the version written. Bad parameters raise before
    anything is written."""
    check_params(chat, formal, levels)
    v2 = len(chat) == 3
    version, magic = (VERSION_V3, MAGIC_V3) if levels is not None else (VERSION_V2, MAGIC_V2) if v2 else (VERSION, MAGIC)
    z = np.load(npz, allow_pickle=False)
    n_layer, n_head, ctx = (int(x) for x in z["cfg"])
    chars = [str(c) for c in z["chars"]]
    if len(set(chars)) != len(chars) or any(len(c) != 1 for c in chars):
        raise ValueError("vocabulary: duplicate or multi-character entries")
    d = z["emb"].shape[1]
    V = len(chars) + 3
    if d % n_head or len(ngram_sha) != 32 or wtype not in ("f32", "int8"):
        raise ValueError(f"bad arguments or config: d {d}, n_head {n_head}, sha {len(ngram_sha)} bytes, type {wtype}")
    shapes = tensor_shapes(n_layer, d, ctx, V)
    if sorted(n for n, _ in shapes) != sorted(k for k in z.files if k not in ("cfg", "chars")):
        raise ValueError("npz keys differ from the model layout")
    if wtype == "int8":
        with tempfile.TemporaryDirectory() as tmp:
            quantize_int8(npz, os.path.join(tmp, "q.npz"))    # the one quantizer (S7b section 2.3); never reimplemented here
            z = dict(np.load(os.path.join(tmp, "q.npz"), allow_pickle=False))
    parts = [HEADER.pack(magic, version, F32 if wtype == "f32" else INT8, n_layer, n_head,
                         d, ctx, V, chat[0], chat[1], formal[0], formal[1], bytes(ngram_sha))]
    if v2:
        parts.append(DELTAS.pack(chat[2], formal[2]))
    if levels is not None:
        parts.append(LEVELS.pack(*levels))
    parts.append(struct.pack(f"<{V - 3}I", *(ord(c) for c in chars)))
    for name, shape in shapes:
        # ValueError, not assert: under python -O a transposed tensor of the right size would export silently wrong weights
        if z[name].shape != shape:
            raise ValueError(f"{name}: shape {z[name].shape} != {shape}")
        if len(shape) == 2 and wtype == "int8":
            if z[name].dtype != np.int8 or z[name + ".scale"].shape != (shape[0], 1):
                raise ValueError(f"{name}: not an int8 matrix with one scale per row")
            parts += [z[name].tobytes(), z[name + ".scale"].astype("<f4").tobytes()]
        else:
            parts.append(z[name].astype("<f4").tobytes())
    with open(out, "wb") as f:
        f.write(b"".join(parts))
    return version


def read_sjtl(path):
    """The reader the tests and the golden generator use: (header dict, chars, params) with params as float32 arrays, int8 matrices
    restored as q.astype(float32) * scale; head["delta"] is (chat, formal), (0, 0) in version 1; head["levels"] is the 12 floats of
    version 3, None before. Python only; the Rust loader is core/src/tinylm.rs."""
    b = open(path, "rb").read()
    magic, version, wt, n_layer, n_head, d, ctx, V, ca, ct, fa, ft, sha = HEADER.unpack_from(b, 0)
    assert (magic, version) in ((MAGIC, VERSION), (MAGIC_V2, VERSION_V2), (MAGIC_V3, VERSION_V3)) and wt in (F32, INT8)
    at = HEADER.size
    delta, levels = (0.0, 0.0), None
    if version >= VERSION_V2:
        delta = DELTAS.unpack_from(b, at)
        at += DELTAS.size
    if version == VERSION_V3:
        levels = LEVELS.unpack_from(b, at)
        at += LEVELS.size
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
    head = dict(version=version, wtype=wt, n_layer=n_layer, n_head=n_head, d=d, ctx=ctx, V=V, chat=(ca, ct), formal=(fa, ft), delta=delta,
                levels=levels, ngram_sha=sha)
    return head, chars, params


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--npz", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--type", choices=["f32", "int8"], required=True)
    ap.add_argument("--ngram-model", required=True, help="the bigram.sjlm alpha / tau were tuned with; its SHA-256 goes into the header")
    ap.add_argument("--chat", type=float, nargs="+", metavar="ALPHA TAU [DELTA]", required=True)
    ap.add_argument("--formal", type=float, nargs="+", metavar="ALPHA TAU [DELTA]", required=True)
    ap.add_argument("--levels", type=float, nargs=12, metavar="X",
                    help="version 3: ALPHA TAU DELTA of chat conservative, chat aggressive, formal conservative, formal aggressive")
    a = ap.parse_args(argv)
    try:
        check_params(a.chat, a.formal, a.levels)      # argument mistakes are usage errors; a bad model file keeps its traceback
    except ValueError as e:
        ap.error(str(e))
    with open(a.ngram_model, "rb") as f:
        sha = hashlib.sha256(f.read()).digest()
    version = export(a.npz, a.out, a.type, a.chat, a.formal, sha, a.levels)
    deltas = f"delta chat {a.chat[2]!r} formal {a.formal[2]!r}" if version >= VERSION_V2 else "no delta"
    if version == VERSION_V3:
        deltas += f", levels {' '.join(repr(x) for x in a.levels)}"
    print(f"wrote {a.out} ({os.path.getsize(a.out)} bytes, {a.type}, version {version}, {deltas}); "
          f"sjtl sha256 {hashlib.sha256(open(a.out, 'rb').read()).hexdigest()}")


if __name__ == "__main__":
    main()
