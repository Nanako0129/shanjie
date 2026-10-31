#!/usr/bin/env python3
"""L5-0 check 3 (research log 2026-10-31, registration "L5-0 探索片登記", amendment 1: on the Mac): the reranker margin of LOSS-0
layer-5 word-swap rows, split into the differing span and the shared suffix.

  python3 experiments/l5/split3.py ROWS      ROWS: the TSV of experiments/loss0/loss0.py --rows (private cache)

For each word-swap row (layer 5, salvageable per check 1, no compound or segmentation tag): the chosen candidate c and the best
gold candidate g (equal length, same context key) are scored position by position with the shipping reranker (data/lm/tinylm.sjtl,
the file HOMO-0 used, pinned in loss0.HOMO0_MODELS) through the Python reference (experiments/s7a-tinylm/tinylm.py). The margin
(gold minus chosen, log10: the core's reranker ll is log10 base) splits into
  span    positions from the first to the last differing character
  suffix  the positions after it (the same characters, conditioned on different prefixes)
The positions before the span are identical with identical prefixes and must add nothing. Parity, a STOP when it fails: for every
row the two parts add up to the margin the core recorded (HOMO-0's score record) within 1e-4 (log10). Prints the four quadrants
(span favours gold or wrong x suffix favours gold or wrong) and the rows with no suffix, per profile and set group; counts only.
"""
import os
import re
import sys
import tempfile
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(REPO, "experiments", "s7a-tinylm"))
sys.path.insert(0, os.path.join(REPO, "experiments", "loss0"))
sys.path.insert(0, os.path.join(REPO, "tools"))
import numpy as np  # noqa: E402
import export_sjtl  # noqa: E402
import tinylm as T  # noqa: E402
import loss0  # noqa: E402
import bench  # noqa: E402
import l5  # noqa: E402

H = os.path.expanduser("~/.cache/shanjie/triage/homo0")


def load_model():
    path = f"{REPO}/data/lm/tinylm.sjtl"
    want = loss0.HOMO0_MODELS["tinylm.sjtl"]
    if bench.sha_file(path) != want:
        sys.exit(f"STOP: data/lm/tinylm.sjtl is not the file HOMO-0 used ({want[:12]}...)")
    head, chars, params = export_sjtl.read_sjtl(path)
    with tempfile.TemporaryDirectory() as d:
        npz = f"{d}/m.npz"
        T.save_params(npz, params, chars, head["n_layer"], head["n_head"], head["ctx"])
        return T.NumpyLM(npz)


def per_position(model, key, cands):
    """[(per-position log10 P of each character of c given [BOS] + key + c_<i)] for each c, as ll_many builds the sequence."""
    voc, out = model.vocab, []
    seqs = []
    for c in cands:
        k = key[max(0, len(key) - (T.CTX - 1 - len(c))):]
        seqs.append(([T.BOS] + voc.enc(k) + voc.enc(c), 1 + len(k)))
    L = max(len(q) for q, _ in seqs)
    ids = np.full((len(seqs), L), T.PAD, dtype=np.int64)
    for b, (q, _) in enumerate(seqs):
        ids[b, :len(q)] = q
    lp = model.logp10(ids)
    for b, (q, st) in enumerate(seqs):
        out.append(lp[b, np.arange(st - 1, len(q) - 1), q[st:]])
    return out


def main():
    rows = [l.rstrip("\n").split("\t") for l in open(sys.argv[1], encoding="utf-8")]
    legal = l5.legal_pairs()
    latin = re.compile(r"[A-Za-z0-9０-９Ａ-Ｚａ-ｚ]")
    want = defaultdict(list)   # (set, prof) -> [(original row, margin, cand, gold)]
    for t in rows:
        s, p, orig, layer, _, _, lab, tags, margin, cand, gold, _ = t
        if layer != "5":
            continue
        sp = l5.spans(cand, gold)
        if l5.covered(sp, legal) or lab == "undecidable" or any(latin.search(a + b) for a, b in sp):
            continue
        if set(tags.split(",")) & {"compound", "segmentation"}:
            continue
        want[(s, p)].append((orig, float(margin), cand, gold))
    model = load_model()
    Q = defaultdict(Counter)
    worst = 0.0
    n = 0
    for (s, p), items in sorted(want.items()):
        base = f"{H}/{s}-{p}"
        mp = [l.rstrip("\n").split("\t")[0] for l in open(f"{base}.map")]
        txt = open(f"{base}.txt", encoding="utf-8").read().splitlines()
        ctx_of = {o: txt[i].split("|")[0] for i, o in enumerate(mp)}
        grp = "small" if s in l5.SMALL else s
        for orig, margin, cand, gold in items:
            key = T.row_key(ctx_of[orig])
            lc, lg = per_position(model, key, [cand, gold])
            d = lg - lc
            diff = [i for i in range(len(gold)) if cand[i] != gold[i]]
            lo, hi = diff[0], diff[-1]
            pre, span, suf = d[:lo].sum(), d[lo:hi + 1].sum(), d[hi + 1:].sum()
            err = abs(pre + span + suf - margin)
            worst = max(worst, err)
            n += 1
            if err > 1e-4 or abs(pre) > 1e-6:
                sys.exit(f"STOP: {s} {p} row {orig}: the split does not add up to the core's margin (error {err:.2e}, prefix {pre:.2e})")
            if hi + 1 == len(gold):
                Q[(p, grp)]["no suffix"] += 1
            else:
                Q[(p, grp)][f"span {'gold' if span > 0 else 'wrong'} / suffix {'gold' if suf > 0 else 'wrong'}"] += 1
    print(f"parity: {n} rows, largest |span + suffix - core margin| {worst:.2e} (log10), prefix contributions 0")
    for key in sorted(Q):
        c = Q[key]
        tot = sum(c.values())
        print(f"\n== {key[0]} {key[1]}: {tot} word-swap rows")
        for k in ["span wrong / suffix gold", "span wrong / suffix wrong", "span gold / suffix wrong", "span gold / suffix gold", "no suffix"]:
            print(f"  {k:28s} {c[k]:4d}")


if __name__ == "__main__":
    main()
