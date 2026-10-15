"""E2 training (torch, Colab G4): experiments/s7a-tinylm/train.py with one more input. Same epoch, same batches (batching.plan with the
same seed and tokens-per-batch, so the same steps as M), same optimiser, schedule and size; only the model (GPTR) and the reading
channel differ.

  python train_r.py --size M --work DIR --out DIR --split-probs P0 P1 P2     # DIR: S7a work dir + rd-*.npy, rdvocab.txt (label_readings.py)
  python train_r.py --size T --work DIR --out DIR --max-steps 20 --split-probs 1 0 0     # smoke test

Each sample (piece of the epoch) is cut at a random point s (context length, 0..2 characters, drawn with --split-probs): the BOS and
the first s characters are the context and get NOREAD, the rest carries readings (tinylm_r.mask_context). The reading input of a position is the
reading of the character it predicts (tinylm_r.target_aligned). Writes DIR/tinylm-<size>r.npz and DIR/train-<size>r.json.
Exit code 2 = diverged, as train.py.
"""
import argparse
import json
import math
import os
import sys
import time

import numpy as np
import torch
import torch.nn.functional as F

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tinylm_r  # noqa: E402,F401
import batching  # noqa: E402
import prep  # noqa: E402
import train as s7a_train  # noqa: E402  (PEAK_LR, WARMUP, WEIGHT_DECAY, LOG_EVERY, lr_at: the very constants and schedule of M)
from torch_model import CONFIGS  # noqa: E402
from tinylm_r import RVocab, mask_context, sample_splits, target_aligned  # noqa: E402
from torch_r import GPTR, export_npz_r  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", choices=sorted(CONFIGS), required=True)
    ap.add_argument("--work", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--split-probs", nargs="+", type=float, required=True, metavar="P",
                    help="P(context length s = 0, 1, 2, ...): the left-context length distribution of the evaluation rows (split_dist.py)")
    ap.add_argument("--seed", type=int, default=20261010)
    ap.add_argument("--tokens-per-batch", type=int, default=32768)
    ap.add_argument("--warmup", type=int, default=s7a_train.WARMUP)
    ap.add_argument("--max-steps", type=int, default=0, help="smoke tests only; the real run uses all batches, like M")
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)

    torch.manual_seed(a.seed)
    chars = prep.load_vocab(a.work)
    rsyl = open(os.path.join(a.work, "rdvocab.txt"), encoding="utf-8", newline="\n").read().split("\n")[:-1]
    rvocab = RVocab(rsyl)
    ids, starts, lengths, batches = batching.plan(a.work, None, a.tokens_per_batch, a.seed, a.max_steps)
    rd = np.concatenate([np.load(os.path.join(a.work, f"rd-{n}.npy")) for n in ("wiki", "coll")])   # same layout as build_epoch's ids
    if len(rd) != len(ids):
        sys.exit(f"train_r: STOP, rd-*.npy ({len(rd)}) is not aligned with ids-*.npy ({len(ids)})")
    total = len(batches)
    cfg = CONFIGS[a.size]
    model = GPTR(len(chars) + 3, n_read=rvocab.size, **cfg).to(a.device)
    n_params = sum(p.numel() for p in model.parameters())
    decay = [p for p in model.parameters() if p.ndim >= 2]
    plain = [p for p in model.parameters() if p.ndim < 2]
    opt = torch.optim.AdamW([{"params": decay, "weight_decay": s7a_train.WEIGHT_DECAY}, {"params": plain, "weight_decay": 0.0}],
                            lr=s7a_train.PEAK_LR, betas=(0.9, 0.95), fused=a.device.startswith("cuda"))
    cuda = a.device.startswith("cuda")
    rng = np.random.default_rng(a.seed + 1)      # the split points; the batch order keeps train.py's rng
    print(f"size {a.size} {cfg} V={len(chars) + 3} R={rvocab.size} params={n_params} pieces={len(lengths)} steps={total} device={a.device}", flush=True)

    t0, acc, curve, tokens = time.time(), torch.zeros((), device=a.device), [], 0
    ln_v = math.log(len(chars) + 3)
    LOG = s7a_train.LOG_EVERY
    model.train()
    for step, idx in enumerate(batches):
        l = int(lengths[idx[0]])
        pos = starts[idx][:, None] + np.arange(l)
        x = torch.from_numpy(ids[pos].astype(np.int64)).to(a.device)
        r = mask_context(rd[pos].astype(np.int64), sample_splits(rng, len(idx), a.split_probs))
        r = torch.from_numpy(target_aligned(r)[:, :-1]).to(a.device)      # the reading of the character each input position predicts
        for g in opt.param_groups:
            g["lr"] = s7a_train.lr_at(step, total, warmup=a.warmup)
        with torch.autocast(device_type="cuda", dtype=torch.bfloat16, enabled=cuda):
            logits = model(x[:, :-1], r)
        loss = F.cross_entropy(logits.float().reshape(-1, logits.shape[-1]), x[:, 1:].reshape(-1))
        opt.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        opt.step()
        acc += loss.detach()
        tokens += x[:, 1:].numel()
        if (step + 1) % LOG == 0 or step + 1 == total:
            n = (step + 1) % LOG or LOG
            m = float(acc) / n
            acc.zero_()
            curve.append((step + 1, m))
            print(f"step {step + 1}/{total} loss {m:.4f} lr {s7a_train.lr_at(step, total, warmup=a.warmup):.2e} tokens {tokens} {time.time() - t0:.0f}s", flush=True)
            if not math.isfinite(m) or (step + 1 >= 3 * LOG and m > ln_v + 1.0):
                print("DIVERGED", flush=True)
                sys.exit(2)
    final = float(np.mean([m for _, m in curve[-2:]]))
    export_npz_r(model, chars, rsyl, os.path.join(a.out, f"tinylm-{a.size}r.npz"))
    json.dump({"size": a.size, "config": cfg, "vocab": len(chars) + 3, "readings": rvocab.size, "params": n_params, "pieces": int(len(lengths)),
               "steps": total, "tokens": tokens, "final_loss_nats_per_token": final, "seed": a.seed, "split_probs": a.split_probs,
               "tokens_per_batch": a.tokens_per_batch, "peak_lr": s7a_train.PEAK_LR, "warmup": a.warmup, "seconds": time.time() - t0,
               "curve": curve}, open(os.path.join(a.out, f"train-{a.size}r.json"), "w"), indent=1)
    print(f"done: final loss {final:.4f} nats/token, {n_params} params")


if __name__ == "__main__":
    main()
