"""S7a training (torch, GPU on 188). One epoch, AdamW, cosine schedule, peak lr 1e-3, fixed seed, context 64, no hyper-parameter search.

  python train.py --size S --work DIR --out DIR      # DIR has vocab.txt, ids-wiki.npy, ids-coll.npy from prep.py
  python train.py --size T --work DIR --out DIR --max-steps 20   # smoke test (tiny model)

Epoch = every wiki piece once + every colloquial piece COLL_WEIGHT (5) times, shuffled; batches hold one sequence length each
(batching.py). Writes DIR/tinylm-<size>.npz (the only artefact the scoring code reads) and DIR/train-<size>.json.
Exit code 2 = diverged (nan, or loss above its starting level after warm-up): the contract's stop condition.
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

import batching
import prep
from torch_model import CONFIGS, GPT, export_npz

PEAK_LR = 1e-3
WARMUP = 200          # linear warm-up steps before the cosine decay (to 0). The contract names AdamW + cosine only; this warm-up is a deviation chosen without a run showing it is needed (not measured)
WEIGHT_DECAY = 0.1    # on matrices only
LOG_EVERY = 100


def build_epoch(work):
    wiki, coll = np.load(os.path.join(work, "ids-wiki.npy")), np.load(os.path.join(work, "ids-coll.npy"))
    ids = np.concatenate([wiki, coll])
    ws, wl = batching.pieces(wiki)
    cs, cl = batching.pieces(coll)
    starts = np.concatenate([ws] + [cs + len(wiki)] * prep.COLL_WEIGHT)
    lengths = np.concatenate([wl] + [cl] * prep.COLL_WEIGHT)
    return ids, starts, lengths


def lr_at(step, total, peak=PEAK_LR, warmup=WARMUP):
    if step < warmup:
        return peak * (step + 1) / warmup
    return peak * 0.5 * (1 + math.cos(math.pi * (step - warmup) / max(1, total - warmup)))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", choices=sorted(CONFIGS), required=True)
    ap.add_argument("--work", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=20261010)
    ap.add_argument("--tokens-per-batch", type=int, default=32768)
    ap.add_argument("--warmup", type=int, default=WARMUP, help="linear warm-up steps (the real runs keep the default)")
    ap.add_argument("--max-steps", type=int, default=0, help="smoke tests only: stop (and finish the schedule) after this many steps")
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)

    torch.manual_seed(a.seed)
    rng = np.random.default_rng(a.seed)
    chars = prep.load_vocab(a.work)
    ids, starts, lengths = build_epoch(a.work)
    batches = batching.make_batches(lengths, a.tokens_per_batch, rng)
    if a.max_steps:
        batches = batches[:a.max_steps]
    total = len(batches)
    cfg = CONFIGS[a.size]
    model = GPT(len(chars) + 3, **cfg).to(a.device)
    n_params = sum(p.numel() for p in model.parameters())
    decay = [p for p in model.parameters() if p.ndim >= 2]
    plain = [p for p in model.parameters() if p.ndim < 2]
    opt = torch.optim.AdamW([{"params": decay, "weight_decay": WEIGHT_DECAY}, {"params": plain, "weight_decay": 0.0}],
                            lr=PEAK_LR, betas=(0.9, 0.95), fused=a.device.startswith("cuda"))
    cuda = a.device.startswith("cuda")
    print(f"size {a.size} {cfg} V={len(chars) + 3} params={n_params} pieces={len(lengths)} steps={total} device={a.device}", flush=True)

    t0, acc, curve, tokens = time.time(), torch.zeros((), device=a.device), [], 0
    ln_v = math.log(len(chars) + 3)
    model.train()
    for step, idx in enumerate(batches):
        l = int(lengths[idx[0]])
        x = torch.from_numpy(ids[starts[idx][:, None] + np.arange(l)].astype(np.int64)).to(a.device)
        for g in opt.param_groups:
            g["lr"] = lr_at(step, total, warmup=a.warmup)
        with torch.autocast(device_type="cuda", dtype=torch.bfloat16, enabled=cuda):
            logits = model(x[:, :-1])
        loss = F.cross_entropy(logits.float().reshape(-1, logits.shape[-1]), x[:, 1:].reshape(-1))
        opt.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        opt.step()
        acc += loss.detach()
        tokens += x[:, 1:].numel()
        if (step + 1) % LOG_EVERY == 0 or step + 1 == total:
            n = (step + 1) % LOG_EVERY or LOG_EVERY
            m = float(acc) / n
            acc.zero_()
            curve.append((step + 1, m))
            print(f"step {step + 1}/{total} loss {m:.4f} lr {lr_at(step, total, warmup=a.warmup):.2e} tokens {tokens} {time.time() - t0:.0f}s", flush=True)
            if not math.isfinite(m) or (step + 1 >= 3 * LOG_EVERY and m > ln_v + 1.0):
                print("DIVERGED", flush=True)
                sys.exit(2)
    final = float(np.mean([m for _, m in curve[-2:]]))
    export_npz(model, chars, os.path.join(a.out, f"tinylm-{a.size}.npz"))
    json.dump({"size": a.size, "config": cfg, "vocab": len(chars) + 3, "params": n_params, "pieces": int(len(lengths)), "steps": total,
               "tokens": tokens, "final_loss_nats_per_token": final, "seed": a.seed, "tokens_per_batch": a.tokens_per_batch,
               "peak_lr": PEAK_LR, "warmup": a.warmup, "seconds": time.time() - t0, "curve": curve},
              open(os.path.join(a.out, f"train-{a.size}.json"), "w"), indent=1)
    print(f"done: final loss {final:.4f} nats/token, {n_params} params")


if __name__ == "__main__":
    main()
