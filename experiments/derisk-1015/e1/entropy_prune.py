"""E1e: relative-entropy pruning of a stored SJT30001 table (Stolcke-style, first-order).

  python3 entropy_prune.py --tri T1.sjt3 --lm B0/bigram.sjlm [--kn B0/kn.sjkn] --keep K[,K2,...] --out-dir DIR [--labels H15,H3,H2]

Every entry (u, v, w) of the (unpruned) table T1 gets the score
    s = c(u,v,w) * ln( P3(w|u,v) / (back3(u,v) * P2(w|v)) ),   P3 = (c - D)/t3 + back3 * P2   (trimodel.py's interpolation)
i.e. count times the log-gain of keeping the entry over letting it fall back to the lower order. The top K entries by s are kept;
ties are broken by (u, v, w) id order (= entry order in the file), so the result is reproducible. Then prune.select's rule: every kept
context keeps its stored total t3, contexts with no kept entry are dropped, header and model binding are unchanged. Because back3 is
recomputed from the kept entries and the stored total, each kept context's distribution still sums to 1 (test_e1 checks it).

FIRST-ORDER APPROXIMATION: removing an entry also raises back3(u,v) (its (c - D)/t3 mass moves to the back-off) and so changes P3 of
every other word in that context; Stolcke's exact criterion includes that term, this score ignores it (it is scored with the back3 of
the unpruned table). One score pass, all K from the same ranking.

P2(w|v) is B0's own probability with KN at beta 1 and no classes, computed from the files alone (no lexicon, so independent of lambda):
    pb(w) = N'(w) / sum N'   (kn.sjkn, id >= 2),   pb(</s>) = eos_total / N,   pb(w) = uni(w) / N when --kn is not given (approximation)
    P2 = (k - D)/t + back2 * pb  if the bigram (v, w) is stored (count k), else back2 * pb  (back2 = 1 when v has no bigram context).
A kn.sjkn with beta != 1 is refused (its pb mixes in the lexicon's score, which these files do not have).
Scores of all entries are computed in chunks of whole contexts (numpy; T1 is memory-mapped), then ranked once.
"""
import argparse
import hashlib
import mmap
import os
import struct

import numpy as np

import common
import prune
import sjlm_io

CHUNK = 1 << 22   # entries per scoring chunk (about 0.5 GB of temporaries)


def load_pb(lm_bytes, d, kn_path=None):
    """(bigram dict, pb array over the vocabulary) for the lower order the table sits on."""
    bg = sjlm_io.read_sjlm(lm_bytes)
    if hashlib.sha256(lm_bytes).digest() != bytes(d["model_sha"]):
        raise ValueError("trigram table was built for another bigram model")
    V = bg["V"]
    if V != d["V"]:
        raise ValueError("vocabulary size differs")
    if kn_path:
        b = open(kn_path, "rb").read()
        if b[:8] != b"SJKN0002" or len(b) != 64 + 4 * V or b[12:44] != hashlib.sha256(lm_bytes).digest():
            raise ValueError("kn side file is not an SJKN0002 file of this model")
        beta, total = struct.unpack_from("<dQ", b, 48)
        if beta != 1.0:
            raise ValueError("only beta 1 is supported (pb then needs no lexicon score)")
        pb = (np.frombuffer(b, "<u4", V, 64).astype(np.float64) + 1) / float(total)
    else:
        pb = bg["uni"].astype(np.float64) / bg["N"]
    pb[:2] = 0.0
    pb[1] = bg["eos_total"] / bg["N"]   # </s>; <s> is never a next word
    return bg, pb


def _back(off, tot, cnt, D):
    """1 - sum (c - D) / t per context (contexts are non-empty)."""
    return 1.0 - np.add.reduceat(cnt.astype(np.float64) - D, off[:-1].astype(np.int64)) / tot.astype(np.float64)


def scores(d, bg, pb):
    """float64 score of every entry of the table dict d, in entry order."""
    V, D = d["V"], d["D"]
    off3 = d["off"].astype(np.int64)
    if (np.diff(off3) <= 0).any():
        raise ValueError("table has an empty context")
    n2 = np.diff(bg["off"].astype(np.int64))
    bkey = np.repeat(bg["ctx"].astype(np.uint64), n2) * np.uint64(V) + bg["nxt"].astype(np.uint64)
    back2 = _back(bg["off"], bg["tot"], bg["cnt"], bg["D"])
    bcnt, btot, bctx = bg["cnt"], bg["tot"].astype(np.float64), bg["ctx"]
    out = np.empty(len(d["nxt"]), np.float64)
    C = len(d["key"])
    c0 = 0
    while c0 < C:
        c1 = int(np.searchsorted(off3, off3[c0] + CHUNK, side="right")) - 1   # whole contexts, at least one
        c1 = min(max(c1, c0 + 1), C)
        lo, hi = off3[c0], off3[c1]
        n = np.diff(off3[c0:c1 + 1])
        local = off3[c0:c1 + 1] - lo
        cnt = np.asarray(d["cnt"][lo:hi])
        back3 = _back(local, d["tot"][c0:c1], cnt, D)
        t3 = d["tot"][c0:c1].astype(np.float64)
        v = np.repeat((d["key"][c0:c1] % np.uint64(V)), n)
        w = np.asarray(d["nxt"][lo:hi])
        k = np.searchsorted(bkey, v * np.uint64(V) + w.astype(np.uint64))
        k = np.minimum(k, len(bkey) - 1)
        hit = bkey[k] == v * np.uint64(V) + w.astype(np.uint64)
        ci = np.minimum(np.searchsorted(bctx, v.astype(np.uint32)), len(bctx) - 1)
        has = bctx[ci] == v
        b2 = np.where(has, back2[ci], 1.0)
        p2 = b2 * pb[w]
        p2 = np.where(hit, (bcnt[k].astype(np.float64) - bg["D"]) / btot[ci] + p2, p2)
        cf = cnt.astype(np.float64)
        b3, t3e = np.repeat(back3, n), np.repeat(t3, n)
        p3 = (cf - D) / t3e + b3 * p2
        out[lo:hi] = cf * np.log(p3 / (b3 * p2))
        c0 = c1
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--tri", required=True)
    ap.add_argument("--lm", required=True)
    ap.add_argument("--kn")
    ap.add_argument("--keep", required=True, help="comma-separated entry counts K")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--labels", help="comma-separated, one per K (default H<K>)")
    a = ap.parse_args(argv)
    ks = [int(x) for x in a.keep.split(",")]
    labels = a.labels.split(",") if a.labels else [f"H{k}" for k in ks]
    if len(labels) != len(ks):
        ap.error("--labels needs one label per K")
    lm_bytes = open(a.lm, "rb").read()
    with open(a.tri, "rb") as f:
        d = sjlm_io.read_tri(mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ))
        bg, pb = load_pb(lm_bytes, d, a.kn)
        s = scores(d, bg, pb)
        if max(ks) > len(s) or min(ks) < 1:
            raise ValueError(f"K must be in 1..{len(s)}")
        order = np.argsort(-s, kind="stable")   # stable: ties keep (u, v, w) id order
        os.makedirs(a.out_dir, exist_ok=True)
        for k, label in zip(ks, labels):
            keep = np.zeros(len(s), bool)
            keep[order[:k]] = True
            out = prune.select(d, keep)
            data = sjlm_io.write_tri(out)
            path = os.path.join(a.out_dir, f"tri-{label}.sjt3")
            with open(path, "wb") as g:
                g.write(data)
            info = {"label": label, "keep": k, "contexts": len(out["key"]), "entries": len(out["nxt"]), "tri_bytes": len(data),
                    "total_bytes": len(lm_bytes) + len(data), "sha256": common.sha256_file(path), "score_min_kept": float(s[order[k - 1]]),
                    "kn": bool(a.kn)}
            common.write_json(path + ".json", info)
            print(info)


if __name__ == "__main__":
    main()
