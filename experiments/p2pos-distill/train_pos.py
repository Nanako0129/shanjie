"""Position-level distillation, arm T (torch, Colab GPU; experiments/p2pos-distill/README.md registration).

  python train_pos.py --npz tinylm-M.npz --data wiki.txt wiki.dump8 X0C.wiki.jsonl wiki.pos.jsonl.gz \
         pool.txt pool.dump8 X0C.pool.jsonl pool.pos.jsonl.gz --out DIR [--seed 20261018 --epochs 3 --batch 64 --lr 1e-4 --warmup 100 \
         --val-frac 0.02 --max-steps N --device cuda]

--data is ROWS / DUMP / P2-TEACHER / POS-TEACHER quadruples: the first three are P2's (train_p2.py: they define the usable rows and the
held-out 8-candidate KL), POS-TEACHER is teacher_pos.py's output for the same rows file.
Loss: for a usable row, the student (starting from M) reads [BOS] k gold with k = tinylm.row_key(prefix, 2) (the truncation of
tinylm.ll_many); at gold position j its logits over H(r_j) (the teacher's characters that the student's vocabulary has; the teacher is
renormalised over the same set) give p_j = softmax, and the row's loss is sum_j KL(q_j || p_j); the batch loss is the mean over rows.
Split and order: P2's plan() over ALL P2-usable rows (same seed, so the same held-out rows as P2), then the rows without a position
teacher are removed from the batches and from the held-out set; batches are therefore a little smaller than --batch.
Writes DIR/tinylm-T.npz (scored unchanged by s7a-tinylm/score.py) and DIR/train-T.json. Exit code 2 = diverged.
The loop `fit` is shared with train_r.py (arm R).
"""
import argparse
import array
import collections
import gzip
import hashlib
import json
import math
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "p2-distill"))     # P2 is imported, never edited (it adds s7a-tinylm itself)
import train_p2 as tp              # noqa: E402
import score                       # noqa: E402  s7a-tinylm/score.py: load_rows / load_dump
import tinylm                      # noqa: E402
from tinylm import BOS, CTX        # noqa: E402

torch = tp.torch
if torch is not None:
    import torch.nn.functional as F
    import torch_model
    s7a_train = tp.s7a_train

Mismatch = tp.Mismatch
NEG = tp.NEG


# ------------------------------------------------------------------ data joining (no torch)
def read_pos(path):
    """teacher_pos.py records, one at a time (the file is large: never a list)."""
    with (gzip.open(path, "rt", encoding="utf-8") if path.endswith(".gz") else open(path, encoding="utf-8")) as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def restrict_student(chars, lq, ids):
    """One position of a teacher record: drop the characters the student's vocabulary (char -> id) lacks and renormalise the rest.
    -> (token ids int32, log q float64 summing to 1 in probability) or None when nothing is left."""
    keep = [i for i, c in enumerate(chars) if c in ids]
    if not keep:
        return None
    l = np.asarray(lq, dtype=np.float64)[keep]
    m = l.max()
    return np.array([ids[chars[i]] for i in keep], dtype=np.int32), l - (m + math.log(np.exp(l - m).sum()))


def join_pos(rows, cands, teacher, pos_records, vocab, name="data"):
    """P2's join (tp.join_rows: n<2, skip, too_long) then the position teacher -> (items, posrows, counts).
    items: P2's [(prefix, [surface], [0c log10])] of the usable rows; posrows[n] = None, or (prefix, gold, [(token ids, log q)] per position)
    for items[n]. A row loses its position teacher (only counted) when 0c skipped it (pos_skip), the gold does not fit the context
    (gold_too_long) or a position has no character left in the student's vocabulary (empty_h_student)."""
    kept, counts = tp.join_rows([(g, s, i) for i, (g, s, _p) in enumerate(rows)], cands, teacher, name)   # row index rides in the prefix slot
    where = {i: n for n, (i, _s, _l) in enumerate(kept)}
    items = [(rows[i][2], surfs, ll) for i, surfs, ll in kept]
    posrows, why, seen = [None] * len(items), {"pos_skip": 0, "gold_too_long": 0, "empty_h_student": 0}, 0
    for i, rec in enumerate(pos_records):
        seen += 1
        if rec.get("i") != i + 1:
            raise Mismatch(f"{name}: position teacher record {i + 1} has i={rec.get('i')}")
        n = where.get(i)
        if n is None:
            continue
        gold, _syl, prefix = rows[i]
        if rec["skip"]:
            why["pos_skip"] += 1
            continue
        if len(rec["pos"]) != len(gold):
            raise Mismatch(f"{name}: row {i + 1}: {len(rec['pos'])} teacher positions for {len(gold)} characters")
        if len(gold) > CTX - 1:
            why["gold_too_long"] += 1
            continue
        ps = [restrict_student(ch, lq, vocab.ids) for ch, lq in rec["pos"]]
        if any(p is None for p in ps):
            why["empty_h_student"] += 1
            continue
        posrows[n] = (prefix, gold, ps)
    if seen != len(rows):
        raise Mismatch(f"{name}: {len(rows)} rows, {seen} position teacher records")
    counts["pos_unusable"] = why
    counts["pos_usable"] = sum(p is not None for p in posrows)
    return items, posrows, counts


class PackedPos:
    """posrows packed once. Row r (an index of items): sequence tok[off[r]:off[r+1]] = [BOS] k gold (empty without a position teacher),
    scored columns st[r]-1+j (j = 0..len(gold)-1) predict gold[j]; position g = poff[r]+j has the candidates htok / hlq[hoff[g]:hoff[g+1]]."""

    def __init__(self, posrows, vocab):
        tok, htok, hlq = array.array("i"), array.array("i"), array.array("f")
        off, st, poff, hoff = [0], [], [0], [0]
        for p in posrows:
            if p is None:
                st.append(0)
            else:
                prefix, gold, ps = p
                key = tinylm.row_key(prefix, 2)
                k = key[max(0, len(key) - (CTX - 1 - len(gold))):]       # tinylm.ll_many's truncation
                tok.extend([BOS] + vocab.enc(k) + vocab.enc(gold))
                st.append(1 + len(k))
                for t, l in ps:
                    htok.extend(t.tolist()); hlq.extend(l.tolist())
                    hoff.append(len(htok))
            off.append(len(tok)); poff.append(len(hoff) - 1)
        self.tok, self.htok, self.hlq = (np.frombuffer(x, dtype=d) for x, d in ((tok, np.int32), (htok, np.int32), (hlq, np.float32)))
        self.off, self.st, self.poff, self.hoff = np.array(off), np.array(st), np.array(poff), np.array(hoff)
        self.valid = np.array([p is not None for p in posrows], dtype=bool)

    def __len__(self):
        return len(self.valid)


def collate_pos(P, idx):
    """Rows idx (all valid) -> numpy batch: ids (B, T) right-padded with PAD; for the M scored positions of the batch pos_row / pos_col
    (row of the batch, column of the logits that predict the character), and H padded to Hm: htok (M, Hm) token ids, hmask, hlq (log q),
    hq (q; 0 and hlq 0 in the padding, so padded slots add exactly 0 to a KL)."""
    idx = np.asarray(idx)
    lens = P.off[idx + 1] - P.off[idx]
    T = int(lens.max())
    ids = np.zeros((len(idx), T), dtype=np.int64)
    ids[np.arange(T)[None, :] < lens[:, None]] = np.concatenate([P.tok[P.off[r]:P.off[r + 1]] for r in idx])
    nj = P.poff[idx + 1] - P.poff[idx]
    j = np.arange(nj.sum()) - np.repeat(np.cumsum(nj) - nj, nj)
    pos_row = np.repeat(np.arange(len(idx)), nj)
    pos_col = np.repeat(P.st[idx] - 1, nj) + j
    g = np.repeat(P.poff[idx], nj) + j
    hl = P.hoff[g + 1] - P.hoff[g]
    mask = np.arange(int(hl.max()))[None, :] < hl[:, None]
    flat = np.repeat(P.hoff[g] - (np.cumsum(hl) - hl), hl) + np.arange(hl.sum())
    htok, hlq = np.zeros(mask.shape, dtype=np.int64), np.zeros(mask.shape, dtype=np.float32)
    htok[mask], hlq[mask] = P.htok[flat], P.hlq[flat]
    return {"ids": ids, "pos_row": pos_row, "pos_col": pos_col, "htok": htok, "hmask": mask, "hlq": hlq,
            "hq": np.where(mask, np.exp(hlq), 0.0).astype(np.float32)}


# ------------------------------------------------------------------ torch half
def to_tensors(b, device):
    return {k: torch.from_numpy(np.ascontiguousarray(v)).to(device) for k, v in b.items()}


def autocast(cuda):
    return torch.autocast(device_type="cuda", dtype=torch.bfloat16, enabled=cuda)


def pos_terms(model, t):
    """-> (KL per scored position (M,), student argmax == teacher argmax per position (M,)). p_j = softmax of the student's logits over
    H(r_j) only, so the unused slots (NEG, q = 0) add exactly 0."""
    z = model(t["ids"])
    g = z[t["pos_row"], t["pos_col"]].float().gather(1, t["htok"]).masked_fill(~t["hmask"], NEG)
    kl = (t["hq"] * (t["hlq"] - F.log_softmax(g, dim=-1))).sum(-1)
    return kl, g.argmax(-1) == t["hq"].argmax(-1)


def pos_loss_rows(model, t):
    """(B,) sum over the positions of a row of KL(q_j || p_j)."""
    kl, _ = pos_terms(model, t)
    return torch.zeros(t["ids"].shape[0], device=kl.device).index_add(0, t["pos_row"], kl)


@torch.no_grad() if torch else (lambda f: f)
def evaluate_pos(model, PP, idx, device, cuda, bs=256):
    """(mean over rows of sum_j KL, share of positions whose student top character is the teacher's) over rows idx."""
    model.eval()
    kl = agree = npos = 0.0
    for s in range(0, len(idx), bs):
        t = to_tensors(collate_pos(PP, idx[s:s + bs]), device)
        with autocast(cuda):
            k, a = pos_terms(model, t)
        kl += float(k.sum()); agree += float(a.sum()); npos += len(a)
    model.train()
    return kl / max(1, len(idx)), agree / max(1, npos)


def prefetch(collate, batches, ahead=4):
    """collate(batch) on a worker thread, `ahead` batches in front of the GPU (train_p2.prefetch with the collate as an argument)."""
    with ThreadPoolExecutor(1) as ex:
        q = collections.deque(ex.submit(collate, b) for b in batches[:ahead])
        for k in range(len(batches)):
            batch = q.popleft().result()
            if k + ahead < len(batches):
                q.append(ex.submit(collate, batches[k + ahead]))
            yield batch


def fit(a, model, vocab, name, batches, collate, to_t, loss_fn, evaluate):
    """The P2 loop (AdamW, lr_at warm-up + cosine, clip 1.0, the divergence rule, the validation curve), shared by arms T and R.
    loss_fn(model, tensors) -> scalar mean loss (it opens its own autocast); evaluate(model) -> {name: value}, called at step 0, every
    --val-every steps and at the end. Exports DIR/tinylm-NAME.npz; returns what the train JSON needs. Exit code 2 = diverged."""
    cuda = a.device.startswith("cuda")
    total = len(batches)
    order_sha = hashlib.sha256(np.concatenate(batches).tobytes()).hexdigest()
    torch.manual_seed(a.seed)
    params = list(model.parameters())
    opt = torch.optim.AdamW([{"params": [p for p in params if p.ndim >= 2], "weight_decay": 0.1},
                             {"params": [p for p in params if p.ndim < 2], "weight_decay": 0.0}],
                            lr=a.lr, betas=(0.9, 0.95), fused=cuda)
    t0, acc, curve, val_curve, first, rows_done = time.time(), torch.zeros((), device=a.device), [], [], None, 0

    def validate(step):
        v = evaluate(model)
        val_curve.append([step, v])
        print(f"step {step} val " + " ".join(f"{k} {x:.4f}" for k, x in v.items()), flush=True)

    validate(0)                                   # step 0 = the start (M itself): the baseline of the validation curve
    model.train()
    for step, batch in enumerate(prefetch(collate, batches)):
        t = to_t(batch, a.device)
        for g in opt.param_groups:
            g["lr"] = s7a_train.lr_at(step, total, peak=a.lr, warmup=a.warmup)
        loss = loss_fn(model, t)
        opt.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(params, 1.0)
        opt.step()
        acc += loss.detach()
        rows_done += len(batches[step])
        end = step + 1
        if end % a.log_every == 0 or end == total:
            m = float(acc) / (end % a.log_every or a.log_every)
            acc.zero_()
            curve.append((end, m))
            print(f"step {end}/{total} loss {m:.4f} lr {s7a_train.lr_at(step, total, peak=a.lr, warmup=a.warmup):.2e} "
                  f"rows/s {rows_done / (time.time() - t0):.0f}", flush=True)
            if tp.diverged(m, first, end, a.warmup):
                print("DIVERGED", flush=True)
                sys.exit(2)
            first = m if first is None else first
        if end % a.val_every == 0 or end == total:
            validate(end)
    secs = time.time() - t0
    torch_model.export_npz(model, vocab.chars, os.path.join(a.out, f"tinylm-{name}.npz"))
    return {"steps": total, "train_order_sha256": order_sha, "curve": curve, "val_curve": val_curve, "seconds": secs}


def add_common_args(ap):
    ap.add_argument("--npz", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=20261018); ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--batch", type=int, default=64); ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--warmup", type=int, default=100); ap.add_argument("--val-frac", type=float, default=0.02)
    ap.add_argument("--max-steps", type=int, default=0)
    ap.add_argument("--log-every", type=int, default=tp.LOG_EVERY); ap.add_argument("--val-every", type=int, default=tp.VAL_EVERY)
    ap.add_argument("--device", default=None)


def run(a):
    cuda = a.device.startswith("cuda")
    os.makedirs(a.out, exist_ok=True)
    quads = list(zip(a.data[::4], a.data[1::4], a.data[2::4], a.data[3::4]))
    model, vocab = torch_model.from_npz(a.npz, a.device)
    items, posrows = [], []
    counts = {"rows_read": 0, "usable": 0, "unusable": {"n<2": 0, "skip": 0, "too_long": 0}, "pos_usable": 0,
              "pos_unusable": {"pos_skip": 0, "gold_too_long": 0, "empty_h_student": 0}, "per_file": []}
    for rp, dp, tpth, pp in quads:
        rows = score.load_rows(rows=rp)
        try:
            with open(dp, encoding="utf-8") as f:
                cands = score.load_dump(f, len(rows))
        except ValueError as e:
            raise Mismatch(f"{os.path.basename(rp)} vs its dump: {e}")
        it, pr, c = join_pos(rows, cands, tp.read_teacher(tpth), read_pos(pp), vocab, os.path.basename(rp))
        items += it; posrows += pr
        counts["per_file"].append(c)
        for k in ("rows_read", "usable", "pos_usable"):
            counts[k] += c[k]
        for kk in ("unusable", "pos_unusable"):
            for k, v in c[kk].items():
                counts[kk][k] += v
    if not counts["pos_usable"]:
        sys.exit("train_pos: no row with a position teacher")
    PP = PackedPos(posrows, vocab)
    del posrows
    val_p2, batches = tp.plan(len(items), a.val_frac, a.batch, a.epochs, a.seed, a.max_steps)
    batches = [b[PP.valid[b]] for b in batches]
    batches = [b for b in batches if len(b)]
    val = val_p2[PP.valid[val_p2]]
    Pval = tp.Packed([items[i] for i in val], vocab)         # P2's packing of the held-out rows: the 8-candidate KL
    print(f"rows read {counts['rows_read']}, P2-usable {counts['usable']}, with a position teacher {counts['pos_usable']} "
          f"({counts['pos_unusable']}), held-out {len(val)} of P2's {len(val_p2)}, steps {len(batches)}", flush=True)
    del items

    def evaluate(m):
        pk, pa = evaluate_pos(m, PP, val, a.device, cuda)
        ck, ca = tp.evaluate(m, Pval, np.arange(len(val)), a.device, cuda)
        return {"pos_kl": pk, "pos_top1": pa, "cand8_kl": ck, "cand8_top1": ca}

    def loss_fn(m, t):
        with autocast(cuda):
            return pos_loss_rows(m, t).mean()

    info = fit(a, model, vocab, "T", batches, lambda b: collate_pos(PP, b), to_tensors, loss_fn, evaluate)
    input_sha = {"npz": tp.sha256_file(a.npz), "files": [[os.path.basename(p), tp.sha256_file(p)] for q in quads for p in q]}
    json.dump({"seed": a.seed, "epochs": a.epochs, "batch": a.batch, "lr": a.lr, "warmup": a.warmup, "val_frac": a.val_frac,
               "max_steps": a.max_steps, "counts": counts, "val_rows_p2": int(len(val_p2)), "val_rows": int(len(val)),
               "input_sha256": input_sha, **info}, open(os.path.join(a.out, "train-T.json"), "w"), indent=1)
    print(f"done: {info['steps']} steps, {info['seconds']:.0f}s", flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser()
    add_common_args(ap)
    ap.add_argument("--data", nargs="+", required=True, help="ROWS DUMP TEACHER POS [ROWS DUMP TEACHER POS ...]")
    a = ap.parse_args(argv)
    if len(a.data) % 4:
        ap.error("--data takes ROWS DUMP TEACHER POS quadruples")
    if torch is None:
        sys.exit("train_pos: torch is not installed")
    a.device = a.device or ("cuda" if torch.cuda.is_available() else "cpu")
    try:
        run(a)
    except Mismatch as e:
        sys.exit(f"train_pos: STOP, {e}")


if __name__ == "__main__":
    main()
