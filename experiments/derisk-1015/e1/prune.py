"""E1d: prune a stored SJT30001 table at a higher threshold, without the counts.

  python3 prune.py --tri T1.sjt3 --thr N --out OUT.sjt3 [--bigram bigram.sjlm]

Same rule as trimodel.TriBuild.tables(N): keep entries with count >= N, every kept context keeps its stored (pre-prune) total, contexts
with no kept entry are dropped, header / model binding unchanged. Pruning an unpruned table gives the bytes TriBuild.tables(N) writes;
pruning an already pruned table (N' >= N) gives the same as pruning the unpruned one, because totals never change.
Writes OUT and OUT.json (contexts, entries, tri_bytes, total_bytes with the bigram when --bigram is given, sha256).
"""
import argparse
import mmap

import numpy as np

import common
import sjlm_io


def select(d, keep):
    """d: sjlm_io.read_tri dict, keep: bool mask over its entries -> the same kind of dict with only those entries
    (totals unchanged, contexts without a kept entry dropped, header unchanged)."""
    ck = np.concatenate(([0], np.cumsum(keep, dtype=np.int64)))
    per_ctx = ck[d["off"][1:]] - ck[d["off"][:-1]]
    sel = per_ctx > 0
    off = np.concatenate(([0], np.cumsum(per_ctx[sel]))).astype(np.uint32)
    return {"V": d["V"], "D": d["D"], "model_sha": d["model_sha"], "key": d["key"][sel], "tot": d["tot"][sel], "off": off,
            "nxt": d["nxt"][keep], "cnt": d["cnt"][keep]}


def prune(d, thr):
    """d: sjlm_io.read_tri dict -> the same kind of dict at threshold thr."""
    return select(d, d["cnt"] >= thr)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--tri", required=True)
    ap.add_argument("--thr", type=int, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--bigram", help="bigram.sjlm, only to record total_bytes")
    a = ap.parse_args(argv)
    with open(a.tri, "rb") as f:
        out = prune(sjlm_io.read_tri(mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)), a.thr)
    data = sjlm_io.write_tri(out)
    with open(a.out, "wb") as f:
        f.write(data)
    C, E = len(out["key"]), len(out["nxt"])
    info = {"threshold": a.thr, "contexts": C, "entries": E, "tri_bytes": len(data), "sha256": common.sha256_file(a.out)}
    if a.bigram:
        import os
        info["total_bytes"] = os.path.getsize(a.bigram) + len(data)
    common.write_json(a.out + ".json", info)
    print(info)


if __name__ == "__main__":
    main()
