"""Position-level distillation, arm R (control, reported only; torch, Colab GPU; experiments/p2pos-distill/README.md registration).

  python train_r.py --npz tinylm-M.npz --data wiki.txt wiki.dump8 X0C.wiki.jsonl pool.txt pool.dump8 X0C.pool.jsonl --out DIR \
         [--seed 20261018 --epochs 3 --batch 64 --lr 1e-4 --warmup 100 --val-frac 0.02 --max-steps N --boost 4 --device cuda]

P2 exactly (train_p2.py is imported: data joining, split, 8-candidate KL, schedule, divergence rule), with one change: the loss of a row is
multiplied by --boost (4) when the P2 teacher's top candidate is not candidate 0 (the n-gram first), else by 1. The batch loss is the plain
mean of the weighted row losses (not divided by the weights). The validation numbers are P2's unweighted ones.
Writes DIR/tinylm-R.npz and DIR/train-R.json. Exit code 2 = diverged.
"""
import argparse
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import train_pos as tpos           # noqa: E402  (also imports train_p2 as tp)

tp, torch = tpos.tp, tpos.torch
Mismatch = tp.Mismatch


def row_weights(q, boost):
    """q (rows, TOPK) P2 teacher probabilities (0 in unused slots) -> float32 weights: `boost` where the teacher's first choice
    (first maximum) is not slot 0, else 1."""
    return np.where(np.asarray(q).argmax(1) != 0, float(boost), 1.0).astype(np.float32)


def run(a):
    cuda = a.device.startswith("cuda")
    os.makedirs(a.out, exist_ok=True)
    triples = list(zip(a.data[::3], a.data[1::3], a.data[2::3]))
    model, vocab = tpos.torch_model.from_npz(a.npz, a.device)
    items, counts = [], {"rows_read": 0, "usable": 0, "unusable": {"n<2": 0, "skip": 0, "too_long": 0}, "per_file": []}
    for rp, dp, tpth in triples:
        rows = tpos.score.load_rows(rows=rp)
        try:
            with open(dp, encoding="utf-8") as f:
                cands = tpos.score.load_dump(f, len(rows))
        except ValueError as e:
            raise Mismatch(f"{os.path.basename(rp)} vs its dump: {e}")
        it, c = tp.join_rows(rows, cands, tp.read_teacher(tpth), os.path.basename(rp))
        items += it
        counts["per_file"].append(c)
        for k in ("rows_read", "usable"):
            counts[k] += c[k]
        for k, v in c["unusable"].items():
            counts["unusable"][k] += v
    if not items:
        sys.exit("train_r: no usable rows")
    P = tp.Packed(items, vocab)
    w = row_weights(P.q, a.boost)
    val_idx, batches = tp.plan(len(P), a.val_frac, a.batch, a.epochs, a.seed, a.max_steps)
    counts["boosted"] = int((w > 1).sum())
    print(f"rows read {counts['rows_read']}, usable {counts['usable']}, unusable {counts['unusable']}, boosted x{a.boost}: "
          f"{counts['boosted']}, val {len(val_idx)}, steps {len(batches)}", flush=True)

    def collate(b):
        out = tp.collate(P, b)
        out["w"] = w[b]
        return out

    def loss_fn(m, t):
        with tpos.autocast(cuda):
            S = tp.cand_scores(m, t)
        return (t["w"] * tp.kl_rows(S, t["q"], t["logq"])).mean()

    def evaluate(m):
        kl, agree = tp.evaluate(m, P, val_idx, a.device, cuda)
        return {"cand8_kl": kl, "cand8_top1": agree}

    info = tpos.fit(a, model, vocab, "R", batches, collate, tp.to_tensors, loss_fn, evaluate)
    input_sha = {"npz": tp.sha256_file(a.npz), "files": [[os.path.basename(p), tp.sha256_file(p)] for t in triples for p in t]}
    json.dump({"seed": a.seed, "epochs": a.epochs, "batch": a.batch, "lr": a.lr, "warmup": a.warmup, "val_frac": a.val_frac,
               "max_steps": a.max_steps, "boost": a.boost, "counts": counts, "val_rows": int(len(val_idx)),
               "input_sha256": input_sha, **info}, open(os.path.join(a.out, "train-R.json"), "w"), indent=1)
    print(f"done: {info['steps']} steps, {info['seconds']:.0f}s", flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser()
    tpos.add_common_args(ap)
    ap.add_argument("--data", nargs="+", required=True, help="ROWS DUMP TEACHER [ROWS DUMP TEACHER ...]")
    ap.add_argument("--boost", type=float, default=4.0)
    a = ap.parse_args(argv)
    if len(a.data) % 3:
        ap.error("--data takes ROWS DUMP TEACHER triples")
    if torch is None:
        sys.exit("train_r: torch is not installed")
    a.device = a.device or ("cuda" if torch.cuda.is_available() else "cpu")
    try:
        run(a)
    except Mismatch as e:
        sys.exit(f"train_r: STOP, {e}")


if __name__ == "__main__":
    main()
