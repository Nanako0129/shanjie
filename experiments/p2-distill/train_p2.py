"""P2 training (torch, Colab GPU): distil the 0c candidate distribution into the S7a student (M) by KL(q || p) over the n-gram's top-8.

  python train_p2.py --npz tinylm-M.npz --data wiki.txt wiki.dump wiki.0c.jsonl pool.txt pool.dump pool.0c.jsonl --out DIR \
         [--context 2|64] [--seed 20261018 --epochs 3 --batch 64 --lr 1e-4 --warmup 100 --val-frac 0.02 --max-steps N --device cuda]

--data is ROWS / DUMP / TEACHER triples: rows `prefix|sentence|reading`, the `--dump` of those rows (top 8, n-gram order), and the
score_0c.py JSONL of the same run (one record per row: i, n, skip, and for n >= 2 `ll` = 0c log10 per candidate in the dump's order).
k per candidate c (p3-context/p3ctx.key_for): --context 2 (default, P2) k = tinylm.context_key(prefix), cut from the left to fit CTX - 1 - |c|;
--context 64 (P3) k = the last CTX - 1 - |c| Han characters of the prefix with every non-Han character skipped (across punctuation).
Student score of a candidate = sum_i ln p(c_i | [BOS] k c_<i) with that k and the truncation of tinylm.ll_many
(natural log, differentiable); q = softmax(ll_0c * ln 10), p = softmax(student score), both over the row's own candidates.
Writes DIR/tinylm-S.npz (torch_model.export_npz, scored unchanged by s7a-tinylm/score.py) and DIR/train-S.json.
Exit code 2 = diverged (non-finite loss, or a logged loss after warm-up above the first logged value).
"""
import argparse
import array
import collections
import hashlib
import json
import math
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "s7a-tinylm"))      # S7a is imported, never edited
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "p3-context"))      # p3ctx: the context rule of --context 64
import tinylm                      # noqa: E402
import score                       # noqa: E402  load_rows / load_dump (the very readers of the S7a scorer)
from tinylm import BOS, CTX, LN10  # noqa: E402

try:
    import torch
except ImportError:                # the data-joining half of this file must stay importable without torch
    torch = None
if torch is not None:
    import torch.nn.functional as F
    import torch_model
    import train as s7a_train      # lr_at: S7a's warm-up + cosine schedule

TOPK = 8
NEG = -1e9           # score of a missing candidate slot: finite, so softmax / products never meet inf - inf
LOG_EVERY, VAL_EVERY = 100, 500


class Mismatch(ValueError):
    """rows / dump / teacher disagree: the run must stop, not guess."""


# ------------------------------------------------------------------ data joining (no torch)
def read_teacher(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def join_rows(rows, cands, teacher, name="data"):
    """rows [(gold, syllables, prefix)], cands per row [(surface, ng score)], teacher records -> (items, counts).
    items: [(prefix, [surface], [0c log10 score])] of the usable rows. Unusable (only counted): n<2, skip (0c could not read the row),
    too_long (a candidate that does not fit the context, which tinylm.ll_many would refuse)."""
    if not len(rows) == len(cands) == len(teacher):
        raise Mismatch(f"{name}: {len(rows)} rows, {len(cands)} dump rows, {len(teacher)} teacher records")
    items, why = [], {"n<2": 0, "skip": 0, "too_long": 0}
    for i, ((_g, _s, prefix), cs, t) in enumerate(zip(rows, cands, teacher), 1):
        if t.get("i") != i or t.get("n") != len(cs):
            raise Mismatch(f"{name}: row {i}: teacher i={t.get('i')} n={t.get('n')}, dump n={len(cs)}")
        if len(cs) < 2:
            why["n<2"] += 1
        elif t["skip"]:
            why["skip"] += 1
        elif any(len(s) > CTX - 1 for s, _ in cs):
            why["too_long"] += 1
        else:
            ll = t["ll"]
            if len(ll) != len(cs) or not all(math.isfinite(x) for x in ll):
                raise Mismatch(f"{name}: row {i}: teacher ll has {len(ll)} values for {len(cs)} candidates (or a non-finite one)")
            items.append((prefix, [s for s, _ in cs], list(ll)))
    return items, {"rows_read": len(rows), "usable": len(items), "unusable": why}


def teacher_dist(ll, n):
    """q = softmax(ll * ln 10) over the n candidates -> (q, log q) padded to TOPK with 0 (float64)."""
    z = np.asarray(ll, dtype=np.float64) * LN10
    lq = z - z.max() - np.log(np.exp(z - z.max()).sum())
    q, logq = np.zeros(TOPK), np.zeros(TOPK)
    q[:n], logq[:n] = np.exp(lq), lq
    return q, logq


class Packed:
    """Every usable row pre-tokenised once. Sequence = [BOS] + k + c, start st = 1 + len(k): positions st-1 .. len-2 of the logits predict
    the characters of c. context 2: k = context_key cut from the left to fit CTX - 1 - |c| (as tinylm.ll_many does); context 64: k = the last
    CTX - 1 - |c| Han characters of the prefix, non-Han skipped (p3ctx.key_for; the row's Han string is encoded once and sliced).
    self.klen = len(k) of every sequence."""

    def __init__(self, items, vocab, context=2):
        tok, off, st, rowseq = array.array("i"), [0], [], [0]
        self.q, self.logq, self.n = np.zeros((len(items), TOPK)), np.zeros((len(items), TOPK)), np.zeros(len(items), dtype=np.int64)
        if context == 64:
            import p3ctx                   # only for P3: the --context 2 path imports nothing new
        elif context != 2:
            raise ValueError(f"context must be 2 or 64, got {context!r}")
        for r, (prefix, surfs, ll) in enumerate(items):
            key = tinylm.row_key(prefix, 2)
            if context == 64:
                hid = vocab.enc(p3ctx.han_only(prefix)[-(CTX - 1):])
            for c in surfs:
                if context == 64:
                    kt = hid[-(CTX - 1 - len(c)):] if len(c) < CTX - 1 else []
                else:
                    kt = vocab.enc(key[max(0, len(key) - (CTX - 1 - len(c))):])
                tok.extend([BOS] + kt + vocab.enc(c))
                off.append(len(tok)); st.append(1 + len(kt))
            rowseq.append(len(st))
            self.n[r] = len(surfs)
            self.q[r], self.logq[r] = teacher_dist(ll, len(surfs))
        self.tok, self.off, self.st, self.rowseq = np.frombuffer(tok, dtype=np.int32), np.array(off), np.array(st), np.array(rowseq)
        self.klen = self.st - 1

    def __len__(self):
        return len(self.n)


def collate(P, idx):
    """Rows idx -> numpy batch: ids (N, T) right-padded with PAD over all candidate sequences of the rows; pos_seq / pos_col / tgt = the
    scored positions (sequence, logits column, target token); seq_row / seq_slot = where a sequence's score goes in the (R, TOPK) table."""
    seqs = np.concatenate([np.arange(P.rowseq[r], P.rowseq[r + 1]) for r in idx])
    lens, st = P.off[seqs + 1] - P.off[seqs], P.st[seqs]
    T = int(lens.max())
    ids = np.zeros((len(seqs), T), dtype=np.int64)
    ids[np.arange(T)[None, :] < lens[:, None]] = np.concatenate([P.tok[P.off[s]:P.off[s + 1]] for s in seqs])
    col = np.arange(T)[None, :]
    pos_seq, pos_col = np.nonzero((col >= (st - 1)[:, None]) & (col <= (lens - 2)[:, None]))
    return {"ids": ids, "pos_seq": pos_seq, "pos_col": pos_col, "tgt": ids[pos_seq, pos_col + 1],
            "seq_row": np.repeat(np.arange(len(idx)), P.n[idx]), "seq_slot": seqs - np.repeat(P.rowseq[idx], P.n[idx]),
            "q": P.q[idx], "logq": P.logq[idx]}


def plan(n, val_frac, batch, epochs, seed, max_steps=0):
    """-> (val indices, list of train batches). One seeded permutation holds out the first val_frac of the rows; the rest is
    reshuffled every epoch with default_rng([seed, epoch]). max_steps cuts the list (the schedule then ends there: smoke tests)."""
    order = np.random.default_rng(seed).permutation(n)
    nv = max(1, round(val_frac * n)) if val_frac > 0 and n > 1 else 0
    val, tr = order[:nv], order[nv:]
    batches = []
    for e in range(epochs):
        p = tr[np.random.default_rng([seed, e]).permutation(len(tr))]
        batches += [p[i:i + batch] for i in range(0, len(p), batch)]
    return val, (batches[:max_steps] if max_steps else batches)


def diverged(loss, first, step, warmup):
    return (not math.isfinite(loss)) or (first is not None and step > warmup and loss > first)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


# ------------------------------------------------------------------ torch half
def to_tensors(b, device):
    t = {k: torch.from_numpy(np.ascontiguousarray(v)).to(device) for k, v in b.items()}
    t["q"], t["logq"] = t["q"].float(), t["logq"].float()
    return t


def cand_scores(model, t):
    """(R, TOPK) natural-log candidate scores, NEG in the unused slots. Differentiable; equals ln10 * tinylm.ll_many on the same weights."""
    z = model(t["ids"])
    ce = F.cross_entropy(z[t["pos_seq"], t["pos_col"]].float(), t["tgt"], reduction="none")
    s = torch.zeros(t["ids"].shape[0], device=z.device).index_add(0, t["pos_seq"], -ce)
    return torch.full((t["q"].shape[0], TOPK), NEG, device=z.device).index_put((t["seq_row"], t["seq_slot"]), s)


def kl_rows(S, q, logq):
    """KL(q || softmax(S)) per row; q = 0 and logq = 0 in the unused slots, so they add exactly 0."""
    return (q * (logq - F.log_softmax(S, dim=-1))).sum(-1)


@torch.no_grad() if torch else (lambda f: f)
def evaluate(model, P, idx, device, cuda, bs=256):
    """(mean KL in nats, share of rows whose student top candidate is the teacher's) over the held-out rows."""
    model.eval()
    kl = agree = 0.0
    for s in range(0, len(idx), bs):
        t = to_tensors(collate(P, idx[s:s + bs]), device)
        with torch.autocast(device_type="cuda", dtype=torch.bfloat16, enabled=cuda):
            S = cand_scores(model, t)
        kl += float(kl_rows(S, t["q"], t["logq"]).sum())
        agree += float((S.argmax(-1) == t["q"].argmax(-1)).sum())
    model.train()
    return kl / max(1, len(idx)), agree / max(1, len(idx))


def prefetch(P, batches, ahead=4):
    """collate() on a worker thread, `ahead` batches in front of the GPU."""
    with ThreadPoolExecutor(1) as ex:
        q = collections.deque(ex.submit(collate, P, b) for b in batches[:ahead])
        for k in range(len(batches)):
            batch = q.popleft().result()
            if k + ahead < len(batches):
                q.append(ex.submit(collate, P, batches[k + ahead]))
            yield batch


def run(a):
    cuda = a.device.startswith("cuda")
    if os.path.exists(os.path.join(a.out, "tinylm-S.npz")):
        sys.exit(f"train_p2: STOP, {os.path.join(a.out, 'tinylm-S.npz')} already exists; use a new --out per arm")
    os.makedirs(a.out, exist_ok=True)
    triples = list(zip(a.data[::3], a.data[1::3], a.data[2::3]))
    model, vocab = torch_model.from_npz(a.npz, a.device)
    items, counts = [], {"rows_read": 0, "usable": 0, "unusable": {"n<2": 0, "skip": 0, "too_long": 0}, "per_file": []}
    for rp, dp, tp in triples:
        rows = score.load_rows(rows=rp)
        try:
            with open(dp, encoding="utf-8") as f:
                cands = score.load_dump(f, len(rows))
        except ValueError as e:
            raise Mismatch(f"{os.path.basename(rp)} vs its dump: {e}")
        it, c = join_rows(rows, cands, read_teacher(tp), os.path.basename(rp))
        items += it
        counts["per_file"].append(c)
        for k in ("rows_read", "usable"):
            counts[k] += c[k]
        for k, v in c["unusable"].items():
            counts["unusable"][k] += v
    if not items:
        sys.exit("train_p2: no usable rows")
    P = Packed(items, vocab, a.context)
    val_idx, batches = plan(len(P), a.val_frac, a.batch, a.epochs, a.seed, a.max_steps)
    total = len(batches)
    order_sha = hashlib.sha256(np.concatenate(batches).tobytes()).hexdigest()
    input_sha = {"npz": sha256_file(a.npz), "files": [[os.path.basename(p), sha256_file(p)] for tr in triples for p in tr]}
    print(f"rows read {counts['rows_read']}, usable {counts['usable']}, unusable {counts['unusable']}, val {len(val_idx)}, "
          f"steps {total}, train order sha256 {order_sha[:16]}", flush=True)

    torch.manual_seed(a.seed)
    params = list(model.parameters())
    opt = torch.optim.AdamW([{"params": [p for p in params if p.ndim >= 2], "weight_decay": 0.1},
                             {"params": [p for p in params if p.ndim < 2], "weight_decay": 0.0}],
                            lr=a.lr, betas=(0.9, 0.95), fused=cuda)
    t0, acc, curve, val_curve, first, rows_done = time.time(), torch.zeros((), device=a.device), [], [], None, 0
    if len(val_idx):                      # step 0 = the start (M itself): the baseline of the validation curve
        v, va = evaluate(model, P, val_idx, a.device, cuda)
        val_curve.append((0, v, va))
        print(f"step 0 val KL {v:.4f} top1 agreement {va:.4f} ({len(val_idx)} rows)", flush=True)
    model.train()
    for step, batch in enumerate(prefetch(P, batches)):
        t = to_tensors(batch, a.device)
        for g in opt.param_groups:
            g["lr"] = s7a_train.lr_at(step, total, peak=a.lr, warmup=a.warmup)
        with torch.autocast(device_type="cuda", dtype=torch.bfloat16, enabled=cuda):
            S = cand_scores(model, t)
        loss = kl_rows(S, t["q"], t["logq"]).mean()
        opt.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(params, 1.0)
        opt.step()
        acc += loss.detach()
        rows_done += len(t["q"])
        end = step + 1
        if end % a.log_every == 0 or end == total:
            m = float(acc) / (end % a.log_every or a.log_every)
            acc.zero_()
            curve.append((end, m))
            print(f"step {end}/{total} KL {m:.4f} lr {s7a_train.lr_at(step, total, peak=a.lr, warmup=a.warmup):.2e} "
                  f"rows/s {rows_done / (time.time() - t0):.0f}", flush=True)
            if diverged(m, first, end, a.warmup):
                print("DIVERGED", flush=True)
                sys.exit(2)
            first = m if first is None else first
        if len(val_idx) and (end % a.val_every == 0 or end == total):
            v, va = evaluate(model, P, val_idx, a.device, cuda)
            val_curve.append((end, v, va))
            print(f"step {end} val KL {v:.4f} top1 agreement {va:.4f}", flush=True)
    secs = time.time() - t0
    torch_model.export_npz(model, vocab.chars, os.path.join(a.out, "tinylm-S.npz"))
    kl = P.klen
    key_stats = {"mean": float(kl.mean()), "max": int(kl.max()), "zero": int((kl == 0).sum()), "sequences": int(len(kl))}
    json.dump({"context": a.context, "key_len": key_stats, "seed": a.seed, "epochs": a.epochs, "batch": a.batch, "lr": a.lr, "warmup": a.warmup, "val_frac": a.val_frac,
               "max_steps": a.max_steps, "steps": total, "counts": counts, "val_rows": int(len(val_idx)),
               "train_order_sha256": order_sha, "curve": curve, "val_curve": val_curve, "seconds": secs, "input_sha256": input_sha},
              open(os.path.join(a.out, "train-S.json"), "w"), indent=1)
    print(f"done: {total} steps, {secs:.0f}s", flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--npz", required=True)
    ap.add_argument("--data", nargs="+", required=True, help="ROWS DUMP TEACHER [ROWS DUMP TEACHER ...]")
    ap.add_argument("--out", required=True)
    ap.add_argument("--context", type=int, choices=[2, 64], default=2, help="2 = context_key (P2); 64 = last Han characters across punctuation (P3)")
    ap.add_argument("--seed", type=int, default=20261018); ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--batch", type=int, default=64); ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--warmup", type=int, default=100); ap.add_argument("--val-frac", type=float, default=0.02)
    ap.add_argument("--max-steps", type=int, default=0)
    ap.add_argument("--log-every", type=int, default=LOG_EVERY); ap.add_argument("--val-every", type=int, default=VAL_EVERY)
    ap.add_argument("--device", default=None)
    a = ap.parse_args(argv)
    if len(a.data) % 3:
        ap.error("--data takes ROWS DUMP TEACHER triples")
    if torch is None:
        sys.exit("train_p2: torch is not installed")
    a.device = a.device or ("cuda" if torch.cuda.is_available() else "cpu")
    try:
        run(a)
    except Mismatch as e:
        sys.exit(f"train_p2: STOP, {e}")


if __name__ == "__main__":
    main()
