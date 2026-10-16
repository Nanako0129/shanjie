"""Rebind an SJT30001 trigram table from one bigram model (SRC) to another (DST) by word string (contract trigram-core.md, slice 0).

python3 remap_tri.py --src-lm B0.sjlm --tri IN.sjt3 --dst-lm V6.sjlm --out OUT.sjt3

Every context word (u, v) and next word w goes SRC id -> word string -> DST id; ctx_key = u * V_dst + v is recomputed, contexts are re-sorted by
key and next ids by id inside a context. Counts and pre-prune totals are copied. The header gets V_dst and SHA-256 of the DST file (the same
binding the loader checks: hashlib.sha256(<.sjlm bytes>).digest()). A SRC word absent from DST stops with a non-zero exit.
"""
import argparse
import hashlib
import json
import struct

import numpy as np

import sjlm_io


def vocab_of(b):
    raw, p, out = sjlm_io.read_sjlm(b)["vocab_raw"], 0, []
    while p < len(raw):
        (n,) = struct.unpack_from("<H", raw, p)
        out.append(raw[p + 2:p + 2 + n].decode("utf-8"))
        p += 2 + n
    return out


def remap(src_lm, tri, dst_lm):
    """bytes of SRC .sjlm, SRC-bound .sjt3 and DST .sjlm -> bytes of the DST-bound .sjt3."""
    t = sjlm_io.read_tri(tri)
    sv, dv = vocab_of(src_lm), vocab_of(dst_lm)
    if t["V"] != len(sv) or t["model_sha"] != hashlib.sha256(src_lm).digest():
        raise ValueError("trigram table is not bound to the SRC model")
    if len(set(sv)) != len(sv) or len(set(dv)) != len(dv):
        raise ValueError("duplicate word in a vocabulary")
    dids = {w: i for i, w in enumerate(dv)}
    missing = [i for i, w in enumerate(sv) if w not in dids]
    if missing:
        raise SystemExit(f"{len(missing)} SRC words missing from DST, first SRC id {missing[0]} ({sv[missing[0]]!r})")
    m = np.array([dids[w] for w in sv], dtype=np.uint64)
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
    return sjlm_io.write_tri({"V": len(dv), "D": t["D"], "model_sha": hashlib.sha256(dst_lm).digest(), "key": key, "tot": t["tot"][order],
                              "off": off, "nxt": nxt[o].astype(np.uint32), "cnt": cnt[o]}), len(sv), len(dv)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    for a in ("src-lm", "tri", "dst-lm", "out"):
        ap.add_argument("--" + a, required=True)
    a = ap.parse_args(argv)
    rd = lambda p: open(p, "rb").read()
    out, vs, vd = remap(rd(a.src_lm), rd(a.tri), rd(a.dst_lm))
    with open(a.out, "wb") as f:
        f.write(out)
    t = sjlm_io.read_tri(out)
    print(json.dumps({"contexts": len(t["key"]), "entries": len(t["nxt"]), "V_src": vs, "V_dst": vd, "out_sha256": hashlib.sha256(out).hexdigest()}))


if __name__ == "__main__":
    main()
