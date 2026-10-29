"""SJT30001 trigram table (docs/contracts/trigram-core.md; layout in reference/proto/trigram.py): rebind a table to another bigram model, or write one from counts.

python3 tools/build_trigram.py --src-lm B0.sjlm --tri IN.sjt3 --dst-lm V6.sjlm --out OUT.sjt3

Remap: every context word (u, v) and next word w goes SRC id -> word string -> DST id; ctx_key = u * V_dst + v is recomputed, contexts are re-sorted by key
and next ids by id inside a context. Counts and pre-prune totals are copied. The header gets V_dst and the SHA-256 of the DST file (the binding the loaders
check: hashlib.sha256(<.sjlm bytes>).digest()). A SRC word absent from DST stops with a non-zero exit.
`from_counts` writes a table from integer trigram counts (tests and the toy golden; the real tables come from the derisk-1015 counting pipeline).
"""
import argparse
import hashlib
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reference", "proto"))
import lm as L  # noqa: E402
import trigram  # noqa: E402


def vocab_of(b):
    """The words of an .sjlm file in id order (reference/proto/lm.py's parser)."""
    return L.read_header(b)[4]


def drop_words(t, keep):
    """The table `t` (read_tri dict) without the context words and next words whose SRC id is False in `keep` (bool array over SRC ids): a context
    with a dropped word goes, an entry with a dropped next word goes, a context left with no entry goes. Pre-prune totals stay (they are still no
    smaller than the kept counts)."""
    Vs = np.uint64(t["V"])
    n_ent = np.diff(t["off"].astype(np.int64))
    ctx_ok = keep[(t["key"] // Vs).astype(np.int64)] & keep[(t["key"] % Vs).astype(np.int64)]
    ent_ok = np.repeat(ctx_ok, n_ent) & keep[t["nxt"].astype(np.int64)]
    ctx_id = np.repeat(np.arange(len(n_ent)), n_ent)
    left = np.bincount(ctx_id[ent_ok], minlength=len(n_ent))
    ctx_ok &= left > 0
    off = np.concatenate(([0], np.cumsum(left[ctx_ok]))).astype(np.uint32)
    return {**t, "key": t["key"][ctx_ok], "tot": t["tot"][ctx_ok], "off": off, "nxt": t["nxt"][ent_ok], "cnt": t["cnt"][ent_ok]}


def remap(src_lm, tri, dst_lm, drop_missing=False):
    """bytes of the SRC .sjlm, the SRC-bound .sjt3 and the DST .sjlm -> (bytes of the DST-bound .sjt3, V_src, V_dst).
    drop_missing: SRC words absent from DST are dropped with drop_words (SJ-LM-7 stage measurements: an old table on a new vocabulary)
    instead of stopping."""
    t = trigram.read_tri(tri)
    sv, dv = vocab_of(src_lm), vocab_of(dst_lm)
    if t["V"] != len(sv) or t["model_sha"] != hashlib.sha256(src_lm).digest():
        raise ValueError("trigram table is not bound to the SRC model")
    if len(set(sv)) != len(sv) or len(set(dv)) != len(dv):
        raise ValueError("duplicate word in a vocabulary")
    dids = {w: i for i, w in enumerate(dv)}
    missing = [i for i, w in enumerate(sv) if w not in dids]
    if missing and drop_missing:
        keep = np.ones(len(sv), bool); keep[missing] = False
        t = drop_words(t, keep)
        print(f"dropped {len(missing)} SRC words absent from DST", file=sys.stderr)
        sv = [w if k else None for w, k in zip(sv, keep)]
    elif missing:
        raise ValueError(f"{len(missing)} SRC words missing from DST, first SRC id {missing[0]} ({sv[missing[0]]!r})")
    m = np.array([dids.get(w, 0) for w in sv], dtype=np.uint64)   # dropped words (None) are never looked up
    Vs, Vd = np.uint64(len(sv)), np.uint64(len(dv))
    C = len(t["key"])
    ctx_u, ctx_v = t["key"] // Vs, t["key"] % Vs
    key = m[ctx_u] * Vd + m[ctx_v]
    order = np.argsort(key, kind="stable")
    key = key[order]
    if (key[1:] == key[:-1]).any():
        raise ValueError("two SRC contexts map to one DST context")
    rank = np.empty(C, np.int64)
    rank[order] = np.arange(C)
    n_ent = np.diff(t["off"].astype(np.int64))
    ent_rank = np.repeat(rank, n_ent)
    nxt, cnt = m[t["nxt"]], t["cnt"]
    o = np.lexsort((nxt, ent_rank))
    if (len(o) > 1) and ((ent_rank[o][1:] == ent_rank[o][:-1]) & (nxt[o][1:] == nxt[o][:-1])).any():
        raise ValueError("entry collision after remapping")
    off = np.concatenate(([0], np.cumsum(n_ent[order]))).astype(np.uint32)
    return trigram.write_tri({"V": len(dv), "D": t["D"], "model_sha": hashlib.sha256(dst_lm).digest(), "key": key, "tot": t["tot"][order],
                              "off": off, "nxt": nxt[o].astype(np.uint32), "cnt": cnt[o]}), len(sv), len(dv)


def from_counts(entries, totals, lm_bytes, D=trigram.D_TRI):
    """A table bound to `lm_bytes` from {(u, v, w): count} (words as strings) and {(u, v): pre-prune total}. Every context needs a total no smaller
    than the sum of its kept counts (the loaders reject a table where it is)."""
    vocab = vocab_of(lm_bytes)
    ids, V = {w: i for i, w in enumerate(vocab)}, len(vocab)
    ctx = {}
    for (u, v, w), c in entries.items():
        ctx.setdefault(ids[u] * V + ids[v], {})[ids[w]] = c
    keys = sorted(ctx)
    tot = [totals[(vocab[k // V], vocab[k % V])] for k in keys]
    for k, t in zip(keys, tot):
        if t < sum(ctx[k].values()):
            raise ValueError("context total below its kept counts")
    off = [0]
    for k in keys:
        off.append(off[-1] + len(ctx[k]))
    return trigram.write_tri({"V": V, "D": D, "model_sha": hashlib.sha256(lm_bytes).digest(), "key": keys, "tot": tot, "off": off,
                              "nxt": [w for k in keys for w in sorted(ctx[k])], "cnt": [ctx[k][w] for k in keys for w in sorted(ctx[k])]})


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    for a in ("src-lm", "tri", "dst-lm", "out"):
        ap.add_argument("--" + a, required=True)
    ap.add_argument("--drop-missing", action="store_true", help="drop SRC words absent from DST (and their contexts and entries) instead of stopping")
    a = ap.parse_args(argv)
    rd = lambda p: open(p, "rb").read()
    try:
        out, vs, vd = remap(rd(a.src_lm), rd(a.tri), rd(a.dst_lm), a.drop_missing)
    except (ValueError, AssertionError) as e:
        sys.exit(f"error: {e}")
    # Temp file + rename: a core process may have the old table mapped, and rewriting it in place would corrupt or SIGBUS that mapping.
    tmp = f"{a.out}.tmp{os.getpid()}"
    with open(tmp, "wb") as f:
        f.write(out)
    os.replace(tmp, a.out)
    t = trigram.read_tri(out)
    print(json.dumps({"contexts": len(t["key"]), "entries": len(t["nxt"]), "V_src": vs, "V_dst": vd, "out_sha256": hashlib.sha256(out).hexdigest()}))


if __name__ == "__main__":
    main()
