"""S7c-1: tinylm .npz (`tinylm.save_params`) -> SJTL0001 / 0002 / 0003 / 0004, the model file core/src/tinylm.rs reads. Written only
here, read only there.

  python export_sjtl.py --npz tinylm-M.npz --out tinylm-M8.sjtl --type int8 --ngram-model data/lm/bigram.sjlm \
                        --chat 0.7 2 --formal 0.7 2                 # version 1 (no delta: the S7a rule)
                        --chat 0.2 1 0.25 --formal 0.7 1 2          # version 2 (S7c-2c: with the S7e delta of each profile)
                        --chat 0.3 2 0.25 --formal 0.7 1 2 \
                        --levels 1 2 3  0.3 2 0.25  0.5 1 1.5  0.5 1 0.25 \
                        [--kn-side data/lm/kn.sjkn]                 # version 4 (rerank strength, Kneser-Ney side file hash)

Both DELTAs or neither (one alone is an error: it would silently be 0); a delta must lie in [0, 1000]. --levels takes four
ALPHA TAU DELTA sets in this order: chat conservative, chat aggressive, formal conservative, formal aggressive; it needs both DELTAs.
Every set (the standard ones and the four) passes the same check as the Rust reader: alpha finite, tau not NaN, delta in [0, 1000].
Version rule of the command line (docs/contracts/model-v6.md sections 2.7 and 7, P3-5): --levels writes version 4, with the SHA-256
of the --kn-side file the parameters were tuned with (Kneser-Ney on), or 32 zero bytes without --kn-side (tuned with KN off);
without --levels the output is version 1 or 2 as before; --kn-side without --levels is a usage error. The side file must be an
SJKN0002 file recording the SHA-256 of --ngram-model (checked before anything is written). Version 3 is written only by `export`
with `levels` and no `kn_sha` (tools/gen_tinylm_tiny.py's golden); the command line no longer writes it.

Layout (little-endian; docs/contracts/s7c-core-tinylm.md section 2.1, version 2 in docs/contracts/s7c2c-delta.md section 2.1):
  header (100 bytes)  magic "SJTL0001" | u32 version (1) | u32 weight type (0 f32, 1 int8) | u32 n_layer, n_head, d, ctx, V
                      | f64 chat alpha, chat tau, formal alpha, formal tau | 32 bytes SHA-256 of the n-gram model alpha / tau were tuned with
  version 2 (116)     the same 100 bytes with magic "SJTL0002" and version 2, then f64 chat delta, f64 formal delta (offsets 100, 108);
                      everything after the header is the same in both versions
  version 3 (212)     the same 116 bytes with magic "SJTL0003" and version 3, then f64 alpha, tau, delta of chat conservative (116,
                      124, 132), chat aggressive (140, 148, 156), formal conservative (164, 172, 180), formal aggressive (188, 196,
                      204) (docs/contracts/rerank-strength.md section 2.1); everything after the header is the same in all versions
  version 4 (244)     the same 212 bytes with magic "SJTL0004" and version 4, then the 32-byte SHA-256 of the whole kn.sjkn the
                      parameters were tuned with, all zero = tuned without one (offset 212; docs/contracts/model-v6.md section 2.7);
                      versions 1 to 3 count as all zero
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
MAGIC_V4, VERSION_V4 = b"SJTL0004", 4
NO_KN = bytes(32)                 # version 4: the side file hash right after LEVELS; all zero = tuned without Kneser-Ney
KN_MAGIC = b"SJKN0002"            # tools/kn_cont.py: magic, u32 V, the model's SHA-256 (offset 12), ...
MAX_DELTA = 1000.0                # the range core/src/tinylm.rs accepts
F32, INT8 = 0, 1
HEADER = struct.Struct("<8s7I4d32s")
HEADER_V4 = HEADER.size + DELTAS.size + LEVELS.size + 32   # 244, core/src/tinylm.rs HEADER_V4
HEADER_V5 = HEADER_V4 + 4                                  # 248, core HEADER_V5: version 4 plus a u32, the declared left-context length (context-return.md); `export` writes none yet


def tensor_shapes(n_layer, d, ctx, V):
    """[(name, shape)] in file order."""
    out = [("emb", (V, d)), ("pos", (ctx, d)), ("lnf_w", (d,)), ("lnf_b", (d,))]
    layer = {"ln1_w": (d,), "ln1_b": (d,), "qkv_w": (d, 3 * d), "qkv_b": (3 * d,), "proj_w": (d, d), "proj_b": (d,),
             "ln2_w": (d,), "ln2_b": (d,), "fc1_w": (d, 4 * d), "fc1_b": (4 * d,), "fc2_w": (4 * d, d), "fc2_b": (d,)}
    for i in range(n_layer):
        out += [(f"l{i}.{k}", layer[k]) for k in tinylm.LAYER_KEYS]
    return out


def sjtl_size(n_layer, d, ctx, V, wtype="int8", header=HEADER_V4):
    """Bytes of the file `export` writes: header + 4 (V - 3) vocabulary + the tensors of `tensor_shapes` (a 2-D matrix is r*c bytes + 4r scale
    bytes in int8, 4rc in f32; a vector 4n). The default header is version 4's HEADER_V4 (244 bytes); an SJTL0005 file (context-return) has HEADER_V5 (248).
    Core's MAX_FILE is 64 << 20 = 67,108,864 (core/src/tinylm.rs); it computes the same length (`want` in `parse`)."""
    n = header + 4 * (V - 3)
    for _, shape in tensor_shapes(n_layer, d, ctx, V):
        r, c = shape if len(shape) == 2 else (None, None)
        n += (r * c + 4 * r if wtype == "int8" else 4 * r * c) if r else 4 * shape[0]
    return n


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


def check_kn_side(side, ngram_sha):
    """Raise ValueError unless `side` (the bytes of a kn.sjkn) is an SJKN0002 file recording `ngram_sha` as its model; returns
    its SHA-256 (32 bytes), the value version 4 records and core/src/lm.rs `Lm::kn_sha256` computes."""
    if side[:8] != KN_MAGIC or side[12:44] != bytes(ngram_sha):
        raise ValueError("--kn-side: not an SJKN0002 side file of the --ngram-model")
    return hashlib.sha256(side).digest()


def export(npz, out, wtype, chat, formal, ngram_sha, levels=None, kn_sha=None):
    """Write `out` from the .npz at `npz`. wtype "f32" | "int8"; chat / formal = (alpha, tau) for version 1, or both
    (alpha, tau, delta) for version 2; with `levels` (12 floats: chat conservative, chat aggressive, formal conservative, formal
    aggressive, each alpha, tau, delta) version 3, or version 4 when `kn_sha` (32 bytes, NO_KN for tuned without Kneser-Ney) is
    given too; ngram_sha = 32 bytes. Returns the version written. Bad parameters raise before anything is written."""
    check_params(chat, formal, levels)
    if kn_sha is not None and (levels is None or len(kn_sha) != 32):
        raise ValueError("a side file hash needs levels (version 4) and 32 bytes")
    v2 = len(chat) == 3
    if kn_sha is not None:
        version, magic = VERSION_V4, MAGIC_V4
    else:
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
    if kn_sha is not None:
        parts.append(bytes(kn_sha))
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
    versions 3 and 4, None before; head["kn_sha"] is version 4's 32 bytes, NO_KN before. Python only; the Rust loader is
    core/src/tinylm.rs."""
    b = open(path, "rb").read()
    magic, version, wt, n_layer, n_head, d, ctx, V, ca, ct, fa, ft, sha = HEADER.unpack_from(b, 0)
    known = ((MAGIC, VERSION), (MAGIC_V2, VERSION_V2), (MAGIC_V3, VERSION_V3), (MAGIC_V4, VERSION_V4))
    assert (magic, version) in known and wt in (F32, INT8)
    at = HEADER.size
    delta, levels, kn_sha = (0.0, 0.0), None, NO_KN
    if version >= VERSION_V2:
        delta = DELTAS.unpack_from(b, at)
        at += DELTAS.size
    if version >= VERSION_V3:
        levels = LEVELS.unpack_from(b, at)
        at += LEVELS.size
    if version == VERSION_V4:
        kn_sha = b[at:at + 32]
        at += 32
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
                levels=levels, ngram_sha=sha, kn_sha=kn_sha)
    return head, chars, params


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--npz", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--type", choices=["f32", "int8"], required=True)
    ap.add_argument("--ngram-model", required=True, help="the bigram.sjlm alpha / tau were tuned with; its SHA-256 goes into the header")
    ap.add_argument("--chat", type=float, nargs="+", metavar="ALPHA TAU [DELTA]", required=True)
    ap.add_argument("--formal", type=float, nargs="+", metavar="ALPHA TAU [DELTA]", required=True)
    ap.add_argument("--levels", type=float, nargs=12, metavar="X",
                    help="version 4: ALPHA TAU DELTA of chat conservative, chat aggressive, formal conservative, formal aggressive")
    ap.add_argument("--kn-side", metavar="KN.SJKN",
                    help="with --levels: the Kneser-Ney side file the parameters were tuned with; its SHA-256 goes into the header")
    a = ap.parse_args(argv)
    if a.kn_side is not None and a.levels is None:
        ap.error("--kn-side needs --levels (only version 4 records the side file)")
    try:
        check_params(a.chat, a.formal, a.levels)      # argument mistakes are usage errors; a bad model file keeps its traceback
    except ValueError as e:
        ap.error(str(e))
    with open(a.ngram_model, "rb") as f:
        sha = hashlib.sha256(f.read()).digest()
    kn_sha = None
    if a.levels is not None:
        kn_sha = NO_KN
        if a.kn_side is not None:
            with open(a.kn_side, "rb") as f:
                side = f.read()
            try:
                kn_sha = check_kn_side(side, sha)
            except ValueError as e:
                ap.error(str(e))
    version = export(a.npz, a.out, a.type, a.chat, a.formal, sha, a.levels, kn_sha)
    deltas = f"delta chat {a.chat[2]!r} formal {a.formal[2]!r}" if version >= VERSION_V2 else "no delta"
    if version >= VERSION_V3:
        deltas += f", levels {' '.join(repr(x) for x in a.levels)}"
    if version == VERSION_V4:
        deltas += f", kn.sjkn sha256 {kn_sha.hex() if kn_sha != NO_KN else 'none (tuned without Kneser-Ney)'}"
    print(f"wrote {a.out} ({os.path.getsize(a.out)} bytes, {a.type}, version {version}, {deltas}); "
          f"sjtl sha256 {hashlib.sha256(open(a.out, 'rb').read()).hexdigest()}")


if __name__ == "__main__":
    main()
