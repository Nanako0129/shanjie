"""0c slice A (docs/contracts/oc-experimental.md section 2, A2): the SJOC0001 model file (read and write) and a numpy forward of the 0c
model, written from the graph (tools/convert_oc.py reads the graph; every op below is one the graph has, in the same order).

  python3 tools/oc_ref.py --compare MODEL_DIR FILE.sjoc --dump D --dev 302 [--rust SCORES.jsonl]   # A2 / A4: vs ONNX Runtime (and Rust)
  python3 tools/oc_ref.py --export ITEMS.tsv --dump D --dev 302 [--pad-context 64]                   # rows for core/examples/oc_score.rs

`Session(load(path))` has the ONNX Runtime session's `run(names, feeds)`, so experiments/x0c/score_0c.py's `Zuin`, `score` and
`feeds_for` drive it unchanged (`zuin(path)`): the inputs are built by the same code for both. Computation is float32, as ORT's, or
float64 (`Session(model, np.float64)`: the cross-language golden's reference, as tools/gen_tinylm_tiny.py's).

Layout (little-endian):
  header (120 bytes)  magic "SJOC0001" | u32 version (1), weight type (0 = f32, the only one), n_layer, n_head, d, ffn, n_tok, n_out, n_base,
                      n_tone, n_type, n_rope | 32 bytes SHA-256 of the source model.onnx | 32 bytes SHA-256 of the source vocab.json
  vocab               (n_tok - 8) x u32 code points: token ids 8 on (0-7 special, 4 = UNK; vocab.json "characters" in order)
                      u32 byte length + UTF-8 of the n_base - 2 bases joined by "\\n" (base ids 2 on; vocab.json "bases" in order),
                      then zero bytes up to a multiple of 4
                      u32 byte length + UTF-8 of manifest.json "neutral_tolerant_last" joined by "\\n", zero-padded the same way
                      n_out x u32 output token ids, ascending (the graph's `out_ids`: every id but 0-3, 5, 7); the log-softmax runs over
                      these columns, and a candidate character's column is its position here (character id - 6)
  tensors (`tensors()` order), f32 row-major, each starting at a multiple of 4 bytes so a reader can use a read-only mapping of the
          file as f32 slices without copying (core/src/trigram.rs `Mapped`); M = a 2-D matrix stored as the forward multiplies it, x @ M
"""
import argparse
import json
import math
import os
import struct
import sys

import numpy as np

MAGIC, VERSION = b"SJOC0001", 1
F32 = 0
HEADER = struct.Struct("<8s12I32s32s")
EPS = 1e-5            # every LayerNormalization in the graph (weight only, no bias)
NEG = np.float32(-3.4028234663852886e38)   # the graph's masked_fill value (val_21)


def tensors(n_layer, d, ffn, n_tok, n_base, n_tone, n_type, n_rope):
    """[(name, shape, kind)] in file order; kind "M" (matrix) or "V" (vector). Layer 0 has no attention LayerNorm (the graph's)."""
    out = [("dec", (n_tok, d), "M"), ("dec_b", (n_tok,), "V"), ("base", (n_base, d), "M"), ("tone", (n_tone, d), "M"),
           ("type", (n_type, d), "M"), ("emb_norm", (d,), "V"), ("inv_freq", (n_rope,), "V")]
    for i in range(n_layer):
        if i:
            out.append((f"l{i}.attn_norm", (d,), "V"))
        out += [(f"l{i}.{k}", (d, d), "M") for k in ("wq", "wk", "wv", "wo")]
        out += [(f"l{i}.mlp_norm", (d,), "V"), (f"l{i}.w1", (d, 2 * ffn), "M"), (f"l{i}.w2", (ffn, d), "M")]
    return out + [("final_norm", (d,), "V"), ("head_w", (d, d), "M"), ("head_norm", (d,), "V")]


def pad4(b):
    return b + bytes(-len(b) % 4)


def write(path, cfg, T, chars, bases, neutral, out_ids, src_sha):
    """cfg: dict n_layer n_head d ffn n_tok n_out n_base n_tone n_type n_rope; T: name -> float32 array; chars: n_tok - 8 strings of
    one code point; bases: n_base - 2 strings; neutral: base strings; out_ids: n_out ascending token ids, each character id c at
    position c - 6; src_sha: (onnx, vocab) 32 bytes each."""
    c = cfg
    layout = tensors(c["n_layer"], c["d"], c["ffn"], c["n_tok"], c["n_base"], c["n_tone"], c["n_type"], c["n_rope"])
    if sorted(T) != sorted(n for n, _, _ in layout):
        raise ValueError("tensor names differ from the layout")
    if len(chars) != c["n_tok"] - 8 or any(len(ch) != 1 for ch in chars) or len(bases) != c["n_base"] - 2:
        raise ValueError("vocabulary sizes differ from the header")
    if c["d"] % c["n_head"] or 2 * c["n_rope"] != c["d"] // c["n_head"]:
        raise ValueError(f"bad config {c}")
    o = np.asarray(out_ids)
    if (len(o) != c["n_out"] or (np.diff(o) <= 0).any() or o.min() < 0 or o.max() >= c["n_tok"]
            or c["n_out"] < c["n_tok"] - 6 or not (o[np.arange(8, c["n_tok"]) - 6] == np.arange(8, c["n_tok"])).all()):
        raise ValueError("out_ids: not ascending ids with every character id c at position c - 6")
    parts = [HEADER.pack(MAGIC, VERSION, F32, *(c[k] for k in (
        "n_layer", "n_head", "d", "ffn", "n_tok", "n_out", "n_base", "n_tone", "n_type", "n_rope")), *src_sha),
        struct.pack(f"<{len(chars)}I", *(ord(ch) for ch in chars))]
    for words in (bases, neutral):
        b = "\n".join(words).encode("utf-8")
        parts += [struct.pack("<I", len(b)), pad4(b)]
    parts.append(struct.pack(f"<{len(o)}I", *o.tolist()))
    for name, shape, kind in layout:
        a = np.asarray(T[name], dtype=np.float32)
        if a.shape != shape or not np.isfinite(a).all():
            raise ValueError(f"{name}: shape {a.shape}, expected {shape}, or not finite")
        parts.append(a.tobytes())
    with open(path, "wb") as f:
        f.write(b"".join(parts))


def load(path):
    """-> dict: cfg, T (float32 arrays), chars, bases, neutral, out_ids, src_sha."""
    with open(path, "rb") as f:
        b = f.read()
    h = HEADER.unpack_from(b)
    if h[0] != MAGIC or h[1] != VERSION or h[2] != F32:
        raise ValueError("not an SJOC0001 file")
    keys = ("n_layer", "n_head", "d", "ffn", "n_tok", "n_out", "n_base", "n_tone", "n_type", "n_rope")
    cfg = dict(zip(keys, h[3:13]))
    at = HEADER.size
    n = cfg["n_tok"] - 8
    chars = [chr(x) for x in struct.unpack_from(f"<{n}I", b, at)]
    at += 4 * n
    words = []
    for _ in range(2):
        (k,), at = struct.unpack_from("<I", b, at), at + 4
        words.append(b[at:at + k].decode("utf-8").split("\n") if k else [])
        at += k + -k % 4
    out_ids = np.frombuffer(b, np.uint32, cfg["n_out"], at).astype(np.int64)
    at += 4 * cfg["n_out"]
    T = {}
    for name, shape, kind in tensors(*(cfg[k] for k in ("n_layer", "d", "ffn", "n_tok", "n_base", "n_tone", "n_type", "n_rope"))):
        if at % 4:
            raise ValueError(f"{name} starts at {at}, not a multiple of 4")
        size = int(np.prod(shape))
        T[name], at = np.frombuffer(b, np.float32, size, at).reshape(shape), at + 4 * size
    if at != len(b):
        raise ValueError(f"file is {len(b)} bytes, the layout ends at {at}")
    return {"cfg": cfg, "T": T, "chars": chars, "bases": words[0], "neutral": words[1], "out_ids": out_ids,
            "src_sha": h[13:15]}


def layer_norm(x, w):
    m = x.mean(-1, keepdims=True)
    v = ((x - m) ** 2).mean(-1, keepdims=True)
    return (x - m) / np.sqrt(v + x.dtype.type(EPS)) * w


def gelu(x):
    """Exact (erf) GELU, as the graph: x * 0.5 * (1 + erf(x / sqrt 2))."""
    from math import erf
    return x * 0.5 * (1 + np.vectorize(erf, otypes=[x.dtype])(x / math.sqrt(2.0))).astype(x.dtype)


class Session:
    """ONNX Runtime's `run(names, feeds)` over `load()`'s model: batch 1, feeds as experiments/x0c/score_0c.py `feeds_for` builds them."""

    def __init__(self, model, dtype=np.float32):
        self.m, self.dt = model, dtype
        self.T = {k: v.astype(dtype) for k, v in model["T"].items()}

    def run(self, names, feeds):
        c, T, dt = self.m["cfg"], self.T, self.dt
        ids, bases, tones, types, pos = (feeds[k][0] for k in ("ids", "bases", "tones", "types", "positions"))
        allowed = feeds["mask"][0, 0]
        H, d = c["n_head"], c["d"]
        hd = d // H
        x = np.where((types == 1)[:, None], T["base"][bases] + T["tone"][tones], T["dec"][ids]) + T["type"][types]
        x = layer_norm(x, T["emb_norm"])
        f = pos.astype(dt)[:, None] * T["inv_freq"][None, :]                  # (L, n_rope)
        cos, sin = np.cos(np.concatenate([f, f], 1)), np.sin(np.concatenate([f, f], 1))
        bias = np.where(allowed, dt(0), dt(NEG))

        def rope(t):                                                          # (H, L, hd), rotate-half
            r = np.concatenate([-t[..., hd // 2:], t[..., :hd // 2]], -1)
            return t * cos + r * sin

        for i in range(c["n_layer"]):
            h = layer_norm(x, T[f"l{i}.attn_norm"]) if i else x
            q, k, v = ((h @ T[f"l{i}.{w}"]).reshape(-1, H, hd).transpose(1, 0, 2) for w in ("wq", "wk", "wv"))
            s = rope(q) @ rope(k).transpose(0, 2, 1) * dt(1 / math.sqrt(hd)) + bias
            s = np.exp(s - s.max(-1, keepdims=True))
            a = (s / s.sum(-1, keepdims=True)) @ v                            # (H, L, hd)
            x = x + a.transpose(1, 0, 2).reshape(-1, d) @ T[f"l{i}.wo"]
            u = layer_norm(x, T[f"l{i}.mlp_norm"]) @ T[f"l{i}.w1"]
            x = x + (gelu(u[:, :c["ffn"]]) * u[:, c["ffn"]:]) @ T[f"l{i}.w2"]
        h = layer_norm(x, T["final_norm"])[feeds["proj_pos"]]
        h = layer_norm(gelu(h @ T["head_w"]), T["head_norm"])
        o = self.m["out_ids"]
        z = h @ T["dec"][o].T + T["dec_b"][o]
        z = z - z.max(-1, keepdims=True)
        lp = z - np.log(np.exp(z).sum(-1, keepdims=True))
        return [lp[feeds["target_node"], feeds["target_ids"]].sum(-1)]


def zuin(path, x0c_dir=None, dtype=np.float32):
    """(score_0c module, its Zuin over this file's model): `score_0c.score(z, context, reading, cands)` as for the ORT model."""
    sys.path.insert(0, x0c_dir or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "experiments", "x0c"))
    import score_0c
    m = load(path)
    return score_0c, score_0c.Zuin(Session(m, dtype), {"characters": m["chars"], "bases": m["bases"]}, {"neutral_tolerant_last": m["neutral"]})


def items(dump, dev, x0c_dir=None):
    """[(row number, prefix, syllables, candidates)] of the first `dev` dev rows with n >= 2 candidates in `dump` (score_0c's readers)."""
    sys.path.insert(0, x0c_dir or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "experiments", "x0c"))
    import score_0c
    rows = score_0c.read_rows(dev=dev)
    return [(i, prefix, syl, cs) for i, ((gold, syl, prefix), cs) in enumerate(zip(rows, score_0c.read_dump(dump, len(rows))), 1)
            if len(cs) >= 2]


def pad_text(n):
    """The first n characters of eval/dev/existing.txt's sentence fields joined in file order: a fixed public text that gives every
    timed row exactly n characters of context (the latency gate's 64-character condition; dev302's own prefixes are mostly empty)."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "eval", "dev", "existing.txt")
    text = "".join(p[1] for p in (l.rstrip("\n").split("|") for l in open(path, encoding="utf-8")) if len(p) == 3)
    if len(text) < n:
        raise SystemExit(f"oc_ref: existing.txt has only {len(text)} characters")
    return text[:n]


def export(dump, dev, out, pad=0):
    """ITEMS.tsv for core/examples/oc_score.rs: row<TAB>prefix<TAB>syllables (space-separated)<TAB>candidates (|-separated). With
    pad = n, every prefix becomes the last n characters of pad_text(n) + the row's own prefix."""
    head = pad_text(pad) if pad else ""
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        for i, prefix, syl, cs in items(dump, dev):
            if pad:
                prefix = (head + prefix)[-pad:]
            if any(c in x for x in [prefix, *syl, *cs] for c in "\t\n|"):
                raise SystemExit(f"oc_ref: row {i} has a tab, newline or | in a field")
            f.write(f"{i}\t{prefix}\t{' '.join(syl)}\t{'|'.join(cs)}\n")


def stats(name, x, y):
    d = np.sort(np.abs(np.concatenate([a - b for a, b in zip(x, y)])))
    same = sum(int(np.argmax(a) == np.argmax(b)) for a, b in zip(x, y))
    print(f"{name}: rows {len(x)}, candidates {len(d)}; log10 |diff| max {d.max():.3g}, p99 {d[int(len(d) * 0.99)]:.3g}, "
          f"median {np.median(d):.3g}; same best candidate {same}/{len(x)} ({same / len(x):.2%})")


def compare(model_dir, path, dump, dev, rust=None):
    """A2 / A4: log10 score() of this forward (the SJOC file) against ONNX Runtime (model_dir, pinned files), per candidate, over the
    scorable rows with n >= 2 of the first `dev` rows; with `rust` (oc_score's SCORES.jsonl) also Rust against both. A row Rust scores
    and score_0c does not (or the reverse) is a stop."""
    s0, ref = zuin(path)
    ort = s0.load_model(model_dir, 1)
    r = {}
    if rust:
        for line in open(rust, encoding="utf-8"):
            if line.strip():
                rec = json.loads(line)
                r[rec["i"]] = rec["ll"]
    A, B, R = [], [], []
    for i, prefix, syl, cs in items(dump, dev):
        try:
            s0.check_row(ort, syl, cs)
        except s0.Unscorable:
            if rust and r.get(i) is not None:
                raise SystemExit(f"oc_ref: Rust scored row {i}, score_0c does not")
            continue
        A.append(np.array(s0.score(ort, prefix, syl, cs)) / math.log(10))
        B.append(np.array(s0.score(ref, prefix, syl, cs)) / math.log(10))
        if rust:
            if r.get(i) is None or len(r[i]) != len(cs):
                raise SystemExit(f"oc_ref: row {i} is scorable but Rust gave {r.get(i)!r}")
            R.append(np.array(r[i]))
    stats(f"{os.path.basename(path)} numpy vs ORT", B, A)
    if rust:
        stats("Rust vs numpy", R, B)
        stats("Rust vs ORT", R, A)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--compare", nargs=2, metavar=("MODEL_DIR", "SJOC"))
    ap.add_argument("--rust", metavar="SCORES", help="with --compare: core/examples/oc_score.rs scores to check against both")
    ap.add_argument("--export", metavar="ITEMS", help="write ITEMS.tsv for core/examples/oc_score.rs")
    ap.add_argument("--pad-context", type=int, default=0, metavar="N", help="with --export: every prefix exactly N characters (pad_text)")
    ap.add_argument("--dump", required=True)
    ap.add_argument("--dev", type=int, default=302)
    a = ap.parse_args()
    if bool(a.compare) == bool(a.export) or (a.rust and not a.compare):
        ap.error("exactly one of --compare / --export; --rust only with --compare")
    if a.export:
        export(a.dump, a.dev, a.export, a.pad_context)
    else:
        compare(*a.compare, a.dump, a.dev, a.rust)
