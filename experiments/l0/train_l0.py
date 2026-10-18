"""L0 training (torch, Colab GPU): M's weights + two small heads, listwise cross entropy over the n-gram's top-8 candidates.

  python train_l0.py --npz tinylm-M.npz --data wiki.txt wiki.dump pool.txt pool.dump --w 1 3 --out DIR \
         [--seed 20261018 --batch 64 --lr 1e-4 --warmup 100 --val-frac 0.02 --max-steps N --device cuda]

--data is rows-file / dump pairs (rows `prefix|clause|reading`; dump = the Rust CLI --dump of those rows). Option lists are built once
(l0core), a seeded fraction of the trainable rows is held out (curve only, never used to choose anything), the rest is shuffled once with
the seed; every --w cell trains one epoch from the same M weights and the same head initialisation over the very same batches in the same
order. AdamW (0.9, 0.95), weight decay 0.1 on ndim >= 2 only, clip 1.0, linear warm-up then cosine to 0 (S7a's lr_at), bf16 autocast on
CUDA only. Writes DIR/l0-w<w>.pt and DIR/train-w<w>.json. Exit code 2 = diverged (nan / inf, or a logged loss after warm-up above the
first logged value).
"""
import argparse
import collections
import hashlib
import json
import math
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import torch

import l0core
import score                    # experiments/s7a-tinylm/score.py: load_rows, load_dump
import train as s7a_train       # lr_at (S7a's schedule)
import torch_l0
from torch_model import from_npz

LOG_EVERY, VAL_EVERY = 100, 500


def build_data(pairs, vocab):
    """-> (packs of the trainable rows, counts). Untrainable: n<2, too_long, no_match (l0core.untrainable_reason); they are only counted."""
    packs = []
    counts = {"rows_read": 0, "trainable": 0, "untrainable": {"n<2": 0, "too_long": 0, "no_match": 0}, "per_file": []}
    for rows_path, dump_path in pairs:
        rows = score.load_rows(rows=rows_path)
        with open(dump_path, encoding="utf-8") as f:
            cands = score.load_dump(f, len(rows))
        before = len(packs)
        for (gold, _syl, prefix), cs in zip(rows, cands):
            surf = [s for s, _ in cs]
            ok = l0core.ok_flags(gold, surf) if len(cs) >= 2 and not l0core.too_long(surf) else []
            why = l0core.untrainable_reason(ok, surf)
            if why:
                counts["untrainable"][why] += 1
            else:
                packs.append(l0core.pack_row(vocab, prefix, cs, 64, l0core.target_of(ok)))
        counts["rows_read"] += len(rows)
        counts["per_file"].append({"rows": len(rows), "trainable": len(packs) - before})
    counts["trainable"] = len(packs)
    return packs, counts


def split(n, val_frac, batch, seed, max_steps):
    """-> (val indices, list of train batches). One seeded permutation: the first val_frac go to validation, the rest are the epoch's order."""
    order = np.random.default_rng(seed).permutation(n)
    nv = max(1, round(val_frac * n)) if val_frac > 0 and n > 1 else 0
    tr = order[nv:]
    batches = [tr[i:i + batch] for i in range(0, len(tr), batch)]
    return order[:nv], (batches[:max_steps] if max_steps else batches)


def diverged(loss, first, step, warmup):
    return (not math.isfinite(loss)) or (first is not None and step > warmup and loss > first)


@torch.no_grad()
def evaluate(model, packs, idx, device, cuda, bs=256):
    """(mean unweighted cross entropy in nats, list accuracy) over the held-out rows."""
    model.eval()
    ce = acc = 0.0
    for s in range(0, len(idx), bs):
        t = torch_l0.to_tensors(l0core.collate([packs[i] for i in idx[s:s + bs]]), device)
        with torch.autocast(device_type="cuda", dtype=torch.bfloat16, enabled=cuda):
            sc = model(t["ids"], t["last"], t["feat"], t["opt"])
        _, c, a = torch_l0.list_loss(sc, t["target"], 1)
        n = len(t["target"])
        ce += float(c) * n
        acc += float(a) * n
    model.train()
    return ce / max(1, len(idx)), acc / max(1, len(idx))


def prefetch(packs, batches, ahead=4):
    """collate() on a worker thread, `ahead` batches in front of the GPU."""
    with ThreadPoolExecutor(1) as ex:
        q = collections.deque(ex.submit(l0core.collate, [packs[i] for i in b]) for b in batches[:ahead])
        for k in range(len(batches)):
            batch = q.popleft().result()
            if k + ahead < len(batches):
                q.append(ex.submit(l0core.collate, [packs[i] for i in batches[k + ahead]]))
            yield batch


def train_cell(a, w, packs, val_idx, batches, counts, order_sha, input_sha):
    cuda = a.device.startswith("cuda")
    torch.manual_seed(a.seed)                      # same head initialisation in every cell
    gpt, vocab = from_npz(a.npz, a.device)         # the same M weights in every cell
    model = torch_l0.L0Model(gpt).to(a.device)
    params = list(model.parameters())
    opt = torch.optim.AdamW([{"params": [p for p in params if p.ndim >= 2], "weight_decay": 0.1},
                             {"params": [p for p in params if p.ndim < 2], "weight_decay": 0.0}],
                            lr=a.lr, betas=(0.9, 0.95), fused=cuda)
    total = len(batches)
    print(f"w {w}: {sum(p.numel() for p in params)} parameters, {len(packs)} trainable rows, {total} steps, device {a.device}", flush=True)
    t0, acc, curve, val_curve, first, rows_done = time.time(), torch.zeros((), device=a.device), [], [], None, 0
    model.train()
    for step, batch in enumerate(prefetch(packs, batches)):
        t = torch_l0.to_tensors(batch, a.device)
        for g in opt.param_groups:
            g["lr"] = s7a_train.lr_at(step, total, peak=a.lr, warmup=a.warmup)
        with torch.autocast(device_type="cuda", dtype=torch.bfloat16, enabled=cuda):
            sc = model(t["ids"], t["last"], t["feat"], t["opt"])
        loss, _, _ = torch_l0.list_loss(sc, t["target"], w)
        opt.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(params, 1.0)
        opt.step()
        acc += loss.detach()
        rows_done += len(t["target"])
        end = step + 1
        if end % a.log_every == 0 or end == total:
            m = float(acc) / (end % a.log_every or a.log_every)
            acc.zero_()
            curve.append((end, m))
            lr = s7a_train.lr_at(step, total, peak=a.lr, warmup=a.warmup)
            print(f"w {w} step {end}/{total} loss {m:.4f} lr {lr:.2e} rows/s {rows_done / (time.time() - t0):.0f}", flush=True)
            if diverged(m, first, end, a.warmup):
                print("DIVERGED", flush=True)
                sys.exit(2)
            first = m if first is None else first
        if len(val_idx) and (end % a.val_every == 0 or end == total):
            v, va = evaluate(model, packs, val_idx, a.device, cuda)
            val_curve.append((end, v, va))
            print(f"w {w} step {end} val loss {v:.4f} list accuracy {va:.4f} ({len(val_idx)} rows)", flush=True)
    secs = time.time() - t0
    torch_l0.save_l0(model, vocab.chars, input_sha["npz"], os.path.join(a.out, f"l0-w{w}.pt"), {"w": w, "seed": a.seed, "steps": total})
    json.dump({"w": w, "seed": a.seed, "batch": a.batch, "lr": a.lr, "warmup": a.warmup, "val_frac": a.val_frac, "max_steps": a.max_steps,
               "steps": total, "counts": counts, "val_rows": int(len(val_idx)), "train_order_sha256": order_sha,
               "curve": curve, "val_curve": val_curve, "seconds": secs, "input_sha256": input_sha},
              open(os.path.join(a.out, f"train-w{w}.json"), "w"), indent=1)
    print(f"done: w {w}, {total} steps, {secs:.0f}s", flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--npz", required=True)
    ap.add_argument("--data", nargs="+", required=True, help="rows1.txt dump1 [rows2.txt dump2 ...]")
    ap.add_argument("--w", nargs="+", type=int, required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=20261018); ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--lr", type=float, default=1e-4); ap.add_argument("--warmup", type=int, default=100)
    ap.add_argument("--val-frac", type=float, default=0.02); ap.add_argument("--max-steps", type=int, default=0)
    ap.add_argument("--log-every", type=int, default=LOG_EVERY); ap.add_argument("--val-every", type=int, default=VAL_EVERY)
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    a = ap.parse_args(argv)
    if len(a.data) % 2:
        ap.error("--data takes rows / dump pairs")
    os.makedirs(a.out, exist_ok=True)
    pairs = list(zip(a.data[::2], a.data[1::2]))
    _, vocab = from_npz(a.npz, "cpu")
    packs, counts = build_data(pairs, vocab)
    if not packs:
        sys.exit("train_l0: no trainable rows")
    val_idx, batches = split(len(packs), a.val_frac, a.batch, a.seed, a.max_steps)
    order_sha = hashlib.sha256(np.concatenate(batches).tobytes()).hexdigest()
    input_sha = {"npz": torch_l0.sha256_file(a.npz), "files": [[os.path.basename(p), torch_l0.sha256_file(p)] for pr in pairs for p in pr]}
    print(f"rows read {counts['rows_read']}, trainable {counts['trainable']}, untrainable {counts['untrainable']}, "
          f"val {len(val_idx)}, steps {len(batches)}, train order sha256 {order_sha[:16]}", flush=True)
    for w in a.w:
        train_cell(a, w, packs, val_idx, batches, counts, order_sha, input_sha)


if __name__ == "__main__":
    main()
