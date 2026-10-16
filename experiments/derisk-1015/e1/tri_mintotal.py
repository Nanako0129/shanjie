"""Drop the contexts of an SJT30001 table whose pre-prune total is below T (trigram-core research, 2026-10-16).

python3 tri_mintotal.py --tri IN.sjt3 --min-total T --out OUT.sjt3

A dropped context scores as the bigram model (TriLM: context not stored -> P2), the same as a context no entry survived pruning for. The kept
contexts are copied byte for byte (key, total, entries), and the header keeps V, D and the model binding, so the loaders need no change.
"""
import argparse

import numpy as np

import sjlm_io


def min_total(tri, T):
    t = sjlm_io.read_tri(tri)
    keep = t["tot"] >= T
    n = np.diff(t["off"].astype(np.int64))
    ent = np.repeat(keep, n)
    off = np.concatenate(([0], np.cumsum(n[keep]))).astype(np.uint32)
    return sjlm_io.write_tri({**t, "key": t["key"][keep], "tot": t["tot"][keep], "off": off, "nxt": t["nxt"][ent], "cnt": t["cnt"][ent]})


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tri", required=True); ap.add_argument("--min-total", type=int, required=True); ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out = min_total(open(a.tri, "rb").read(), a.min_total)
    with open(a.out, "xb") as f:
        f.write(out)
    d = sjlm_io.read_tri(out)
    print(f"{a.out}: {len(d['key'])} contexts, {len(d['nxt'])} entries, {len(out)} bytes")
