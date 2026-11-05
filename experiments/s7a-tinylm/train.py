"""S7a training (torch, GPU on 188). One epoch, AdamW, cosine schedule, peak lr 1e-3, fixed seed, context 64, no hyper-parameter search.

  python train.py --size S --work DIR --out DIR      # DIR has vocab.txt, ids-wiki.npy, ids-coll.npy from prep.py
  python train.py --size T --work DIR --out DIR --max-steps 20   # smoke test (tiny model)

Epoch = every wiki piece once + every colloquial piece COLL_WEIGHT (5) times (+ every --extra PATH[:WEIGHT] piece WEIGHT times, default 1;
SJ-SR-7: --extra ids-g0v.npy:5 --extra ids-pttbody.npy:1 ...), shuffled; batches hold one sequence length each (batching.build_epoch / plan).
Training is ONE epoch with one step per batch (batching.make_batches: pieces of each exact length are packed into batches of
max(1, tokens_per_batch // length) pieces, so each length ends in a partial batch). The step count therefore grows with the total
(weighted) token count but is not exactly proportional to it; how close it is has not been measured. --max-steps only cuts the batch list
(smoke tests, S7f's fixed budget). Every --extra ids file and ids-wiki/coll must have been encoded with the vocab.txt in --work
(prep.check_vocab, checked before training).
--init NPZ --peak-lr X (SR-TW-W): start from an exported model with the same size and vocabulary (prep.py encode --vocab-from NPZ) at peak lr X.
SR-TW-V options (all off by default; a run without them is the run before they existed):
--weight-decay W: AdamW weight decay on the matrices (default 0.1, V4 uses 0).
--kl-teacher NPZ --kl-lambda L: a frozen teacher (same vocabulary and shape as the student, torch_model.from_npz, eval mode, no grad). Per token
  loss = CE + L * KL(student || teacher), the KL only on tokens of runs whose --changed-flags bit (prep.py) is 0; tokens of changed runs get CE
  only. Both are averaged over every target token of the batch (masked tokens add 0 to the KL sum), and the logged loss is that total.
  With a teacher, flags-<name>.u8 must sit next to every ids file (wiki, coll, each --extra). L = 0 (the default) is the plain loss exactly.
--changed-weight R: batches are drawn so that runs whose flag is 1 make up the fraction R (0 < R < 1) of the target tokens of the epoch, the
  other runs keep their source proportions (batching.reweight_changed). The epoch grows or shrinks with it; --max-steps cuts it as usual.
The train json records weight_decay, kl_teacher (path, sha256), kl_lambda and changed_weight.
Writes DIR/tinylm-<size>.npz (the only artefact the scoring code reads) and DIR/train-<size>.json.
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
WEIGHT_DECAY = 0.1    # on matrices only (default of --weight-decay)
LOG_EVERY = 100


def lr_at(step, total, peak=PEAK_LR, warmup=WARMUP):
    if step < warmup:
        return peak * (step + 1) / warmup
    return peak * 0.5 * (1 + math.cos(math.pi * (step - warmup) / max(1, total - warmup)))


def kl_student_teacher(logits, t_logits):
    """Per-token KL(student || teacher) in float32, shape logits.shape[:-1]: sum_v p_s (log p_s - log p_t)."""
    ls, lt = F.log_softmax(logits.float(), dim=-1), F.log_softmax(t_logits.float(), dim=-1)
    return (ls.exp() * (ls - lt)).sum(-1)


def step_loss(logits, tgt, t_logits=None, keep=None, lam=0.0):
    """CE over every target token; with a teacher also + lam * (KL * keep).sum() / keep.numel(), keep = 1.0 where the token's run is unchanged."""
    ce = F.cross_entropy(logits.float().reshape(-1, logits.shape[-1]), tgt.reshape(-1))
    if t_logits is None:
        return ce
    return ce + lam * (kl_student_teacher(logits, t_logits) * keep).sum() / keep.numel()


def extra_arg(arg):
    try:
        return batching.parse_extra(arg)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", choices=sorted(CONFIGS), required=True)
    ap.add_argument("--work", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=20261010)
    ap.add_argument("--tokens-per-batch", type=int, default=32768)
    ap.add_argument("--warmup", type=int, default=WARMUP, help="linear warm-up steps (the real runs keep the default)")
    ap.add_argument("--max-steps", type=int, default=0,
                    help="use only the first N shuffled batches (the cosine schedule ends there): smoke tests, and S7f's fixed training "
                         "budget (A0', A1, A2 all run --max-steps 8836)")
    ap.add_argument("--extra", action="append", default=[], metavar="PATH[:WEIGHT]", type=extra_arg,
                    help="an ids-<name>.npy (prep.py encode --extra) added to the epoch WEIGHT times (ASCII integer >= 1, bare PATH = 1, S7f); "
                         "repeatable; without it the epoch is S7a's. The weight is whatever follows the last ':', so a path that itself "
                         "ends in ':something' is not supported")
    ap.add_argument("--init", metavar="NPZ", help="start from this exported model (torch_model.from_npz) instead of a fresh one (SR-TW-W warm start): "
                    "its size must be --size's and its `chars` the vocab.txt in --work (prep.py encode --vocab-from NPZ)")
    ap.add_argument("--peak-lr", type=float, default=PEAK_LR, help="peak learning rate of the warm-up and cosine schedule (the real runs keep 1e-3)")
    ap.add_argument("--weight-decay", type=float, default=WEIGHT_DECAY, help="AdamW weight decay on the matrices (the real runs keep 0.1; SR-TW-V V4 uses 0)")
    ap.add_argument("--kl-teacher", metavar="NPZ", help="frozen teacher for the KL term (same vocabulary and shape as the student); needs flags-<name>.u8 files (prep.py --changed-flags)")
    ap.add_argument("--kl-lambda", type=float, help="weight of KL(student || teacher) on the tokens of unchanged runs; required with --kl-teacher "
                    "(review P3-c: a teacher without it would train with no anchor); 0 is allowed explicitly")
    ap.add_argument("--changed-weight", type=float, metavar="R", help="draw batches so that changed runs are the fraction R of the target tokens (V3 uses 0.1)")
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)

    torch.manual_seed(a.seed)
    chars = prep.load_vocab(a.work)
    extra = a.extra
    prep.check_vocab(a.work, [p for p, _ in extra])
    if a.kl_teacher and a.kl_lambda is None:
        sys.exit("--kl-teacher needs --kl-lambda (give 0 explicitly for a run without the anchor)")
    if a.kl_lambda is None:
        a.kl_lambda = 0.0
    if not a.kl_lambda >= 0 or (a.kl_lambda > 0 and not a.kl_teacher):   # `not >=` also refuses NaN
        sys.exit("--kl-lambda needs --kl-teacher and must be a number >= 0")
    if not a.weight_decay >= 0:
        sys.exit("--weight-decay must be a number >= 0")
    if a.changed_weight is not None and not 0 < a.changed_weight < 1:
        sys.exit("--changed-weight must be in (0, 1)")
    try:
        tokflag = batching.token_flags(a.work, extra) if (a.kl_teacher or a.changed_weight is not None) else None
        ids, starts, lengths, batches = batching.plan(a.work, extra, a.tokens_per_batch, a.seed, a.max_steps, a.changed_weight, tokflag)
    except ValueError as e:
        sys.exit(f"train: {e}")
    total = len(batches)
    cfg = CONFIGS[a.size]
    if a.init:
        from torch_model import from_npz
        model, ivocab = from_npz(a.init, a.device)
        if ivocab.chars != chars:
            sys.exit(f"--init {a.init}: its vocabulary is not the vocab.txt in {a.work} (encode with prep.py encode --vocab-from {a.init})")
        if any(model.cfg[k] != v for k, v in cfg.items()):
            sys.exit(f"--init {a.init}: its config {model.cfg} is not size {a.size} {cfg}")
    else:
        model = GPT(len(chars) + 3, **cfg).to(a.device)
    teacher = None
    if a.kl_teacher:
        from torch_model import from_npz
        teacher, tvocab = from_npz(a.kl_teacher, a.device)
        if tvocab.chars != chars or any(teacher.cfg[k] != v for k, v in model.cfg.items()):
            sys.exit(f"--kl-teacher {a.kl_teacher}: its vocabulary and shape must be the student's ({model.cfg})")
        teacher.eval()
        for p in teacher.parameters():
            p.requires_grad_(False)
    n_params = sum(p.numel() for p in model.parameters())
    decay = [p for p in model.parameters() if p.ndim >= 2]
    plain = [p for p in model.parameters() if p.ndim < 2]
    opt = torch.optim.AdamW([{"params": decay, "weight_decay": a.weight_decay}, {"params": plain, "weight_decay": 0.0}],
                            lr=a.peak_lr, betas=(0.9, 0.95), fused=a.device.startswith("cuda"))
    cuda = a.device.startswith("cuda")
    print(f"size {a.size} {cfg} V={len(chars) + 3} params={n_params} pieces={len(lengths)} steps={total} device={a.device}", flush=True)

    t0, acc, curve, tokens = time.time(), torch.zeros((), device=a.device), [], 0
    ln_v = math.log(len(chars) + 3)
    model.train()
    for step, idx in enumerate(batches):
        l = int(lengths[idx[0]])
        x = torch.from_numpy(ids[starts[idx][:, None] + np.arange(l)].astype(np.int64)).to(a.device)
        for g in opt.param_groups:
            g["lr"] = lr_at(step, total, peak=a.peak_lr, warmup=a.warmup)
        with torch.autocast(device_type="cuda", dtype=torch.bfloat16, enabled=cuda):
            logits = model(x[:, :-1])
            if teacher is not None:
                with torch.no_grad():
                    t_logits = teacher(x[:, :-1])
        if teacher is None:
            loss = step_loss(logits, x[:, 1:])
        else:   # the target at position j is token j + 1: its run's flag is that token's flag
            keep = torch.from_numpy((tokflag[starts[idx][:, None] + np.arange(1, l)] == 0).astype(np.float32)).to(a.device)
            loss = step_loss(logits, x[:, 1:], t_logits, keep, a.kl_lambda)
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
            print(f"step {step + 1}/{total} loss {m:.4f} lr {lr_at(step, total, peak=a.peak_lr, warmup=a.warmup):.2e} tokens {tokens} {time.time() - t0:.0f}s", flush=True)
            if not math.isfinite(m) or (step + 1 >= 3 * LOG_EVERY and m > ln_v + 1.0):
                print("DIVERGED", flush=True)
                sys.exit(2)
    final = float(np.mean([m for _, m in curve[-2:]]))
    export_npz(model, chars, os.path.join(a.out, f"tinylm-{a.size}.npz"))
    json.dump({"size": a.size, "config": cfg, "vocab": len(chars) + 3, "params": n_params, "pieces": int(len(lengths)), "steps": total,
               "tokens": tokens, "final_loss_nats_per_token": final, "seed": a.seed,
               "extra": [{"path": p, "weight": w, "sha256": prep.sha256_file(p)} for p, w in extra] or None, "tokens_per_batch": a.tokens_per_batch,
               "peak_lr": a.peak_lr, "warmup": a.warmup, "weight_decay": a.weight_decay,
               "kl_teacher": {"path": a.kl_teacher, "sha256": prep.sha256_file(a.kl_teacher)} if a.kl_teacher else None, "kl_lambda": a.kl_lambda,
               "changed_weight": a.changed_weight, "init_sha256": prep.sha256_file(a.init) if a.init else None, "seconds": time.time() - t0, "curve": curve},
              open(os.path.join(a.out, f"train-{a.size}.json"), "w"), indent=1)
    print(f"done: final loss {final:.4f} nats/token, {n_params} params")


if __name__ == "__main__":
    main()
