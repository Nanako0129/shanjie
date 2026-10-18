"""P2B training (torch, Colab GPU): P2 with exactly one change, the student also sees the reading of the candidate it scores.

  python train_b.py --npz tinylm-M.npz --data wiki.txt wiki.dump8 X0C.wiki.jsonl pool.txt pool.dump8 X0C.pool.jsonl --out DIR \
         [--seed 20261018 --epochs 3 --batch 64 --lr 1e-4 --warmup 100 --val-frac 0.02 --max-steps N --device cuda]

Everything but the reading input is train_p2.py (imported, not copied): data joining, filtering, split, schedule, KL loss, divergence rule.
Reading input: a table R (n_syl, d), zeros at the start, so the model starts as M exactly. Input at position t = emb(token_t) + pos(t) + R(r_t);
r_t = syllable of the candidate character predicted at position t + 1 (c_1's at the last context position, or BOS; c_j's at c_{j-1}'s
position), 0 ("none", a row that is never trained) everywhere else. Syllable table: 0 none, 1 unknown, then the sorted syllables of the usable
training rows. Rows whose candidates do not all have as many characters as the row has syllables are not trained on (`len_mismatch`).
Writes DIR/tinylm-B.npz (the S7a arrays plus rd_emb (n_syl, d) and rd_syl (n_syl names)) and DIR/train-B.json. Exit code 2 = diverged.
"""
import argparse
import hashlib
import json
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "p2-distill"))     # P2 is imported, never edited (it adds s7a-tinylm itself)
import train_p2 as tp              # noqa: E402
import score                       # noqa: E402  s7a-tinylm/score.py: load_rows / load_dump

torch = tp.torch
if torch is not None:
    import torch.nn as nn
    import torch_model
    s7a_train = tp.s7a_train

NONE, UNK_SYL = 0, 1               # syllable table rows 0 / 1; the real syllables start at 2
TABLE_HEAD = ["<none>", "<unk>"]
Mismatch = tp.Mismatch


# ------------------------------------------------------------------ syllables and data joining (no torch)
def build_syl_table(syl_lists):
    return TABLE_HEAD + sorted({s for syls in syl_lists for s in syls})


def syl_ids(table):
    """syllable -> row of R. Syllables outside the table (and the two head names) are not in it: look them up with .get(s, UNK_SYL)."""
    return {s: i for i, s in enumerate(table) if i >= 2}


def join_rows_b(rows, cands, teacher, name="data"):
    """tp.join_rows (P2's reasons, in P2's order), then the reading filter -> (items [(prefix, surfs, ll, syls)], counts).
    A row is `len_mismatch` when any candidate's character count differs from the row's syllable count."""
    # tp.join_rows carries the prefix slot through untouched: put the row number there to match the kept items back to their rows
    kept, counts = tp.join_rows([(g, s, i) for i, (g, s, _p) in enumerate(rows)], cands, teacher, name)
    items, bad = [], 0
    for i, surfs, ll in kept:
        _g, syls, prefix = rows[i]
        if any(len(s) != len(syls) for s in surfs):
            bad += 1
        else:
            items.append((prefix, surfs, ll, syls))
    counts["unusable"]["len_mismatch"] = bad
    counts["usable"] = len(items)
    return items, counts


class PackedB(tp.Packed):
    """tp.Packed plus, per row, the syllable ids (rsyl[roff[r]:roff[r+1]]) of the row's reading."""

    def __init__(self, items, vocab, sid):
        super().__init__([it[:3] for it in items], vocab)
        flat, off = [], [0]
        for it in items:
            flat += [sid.get(s, UNK_SYL) for s in it[3]]
            off.append(len(flat))
        self.rsyl, self.roff = np.array(flat, dtype=np.int64), np.array(off)


def collate_b(P, idx):
    """tp.collate plus rd (N, T): at every scored position (the one that predicts candidate character j) the syllable id of character j,
    0 everywhere else."""
    b = tp.collate(P, idx)
    seqs = np.concatenate([np.arange(P.rowseq[r], P.rowseq[r + 1]) for r in idx])          # the sequences, in tp.collate's order
    row = b["seq_row"][b["pos_seq"]]
    j = b["pos_col"] - (P.st[seqs][b["pos_seq"]] - 1)
    b["rd"] = np.zeros_like(b["ids"])
    b["rd"][b["pos_seq"], b["pos_col"]] = P.rsyl[P.roff[idx][row] + j]
    return b


# ------------------------------------------------------------------ torch half
if torch is not None:
    class ReadingGPT(nn.Module):
        """S7a GPT (`gpt`) plus R. Row 0 of R is padding_idx: its gradient is zero and it starts at 0, so it stays exactly 0."""

        def __init__(self, gpt, table):
            super().__init__()
            self.gpt, self.table = gpt, list(table)
            self.rd = nn.Embedding(len(table), gpt.cfg["d"], padding_idx=NONE)
            nn.init.zeros_(self.rd.weight)

        def forward(self, ids, rd):
            g = self.gpt
            x = g.emb(ids) + g.pos(torch.arange(ids.shape[1], device=ids.device)) + self.rd(rd)
            for b in g.blocks:
                x = b(x)
            return g.lnf(x) @ g.emb.weight.t()

    def save_b(model, chars, path):
        """torch_model.export_npz of the GPT, then rd_emb and rd_syl added to the same file."""
        tmp = path + ".gpt.npz"
        torch_model.export_npz(model.gpt, chars, tmp)
        with np.load(tmp) as zf:
            z = {k: zf[k] for k in zf.files}
        os.remove(tmp)
        z["rd_emb"] = model.rd.weight.detach().cpu().float().numpy()
        z["rd_syl"] = np.array(model.table, dtype=str)
        np.savez(path, **z)

    def load_b(path, device="cpu"):
        """-> (ReadingGPT, Vocab). torch_model.from_npz ignores the extra keys, so the GPT is exactly the one the file holds."""
        gpt, vocab = torch_model.from_npz(path, device)
        with np.load(path, allow_pickle=False) as z:
            table, w = [str(s) for s in z["rd_syl"]], z["rd_emb"]
        assert w.shape == (len(table), gpt.cfg["d"]) and not w[NONE].any(), "rd_emb: wrong shape, or a trained 'none' row"
        m = ReadingGPT(gpt, table).to(device)
        with torch.no_grad():
            m.rd.weight.copy_(torch.from_numpy(w))
        return m, vocab

    def cand_scores_b(model, t):
        """tp.cand_scores with the reading input: (R, TOPK) natural-log candidate scores, NEG in the unused slots."""
        return tp.cand_scores(lambda ids: model(ids, t["rd"]), t)

    @torch.no_grad()
    def evaluate_b(model, P, idx, device, cuda, bs=256):
        """tp.evaluate with the reading input."""
        model.eval()
        kl = agree = 0.0
        for s in range(0, len(idx), bs):
            t = tp.to_tensors(collate_b(P, idx[s:s + bs]), device)
            with torch.autocast(device_type="cuda", dtype=torch.bfloat16, enabled=cuda):
                S = cand_scores_b(model, t)
            kl += float(tp.kl_rows(S, t["q"], t["logq"]).sum())
            agree += float((S.argmax(-1) == t["q"].argmax(-1)).sum())
        model.train()
        return kl / max(1, len(idx)), agree / max(1, len(idx))


def prefetch_b(P, batches, ahead=4):
    """tp.prefetch with collate_b."""
    import collections
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(1) as ex:
        q = collections.deque(ex.submit(collate_b, P, b) for b in batches[:ahead])
        for k in range(len(batches)):
            batch = q.popleft().result()
            if k + ahead < len(batches):
                q.append(ex.submit(collate_b, P, batches[k + ahead]))
            yield batch


def run(a):
    cuda = a.device.startswith("cuda")
    os.makedirs(a.out, exist_ok=True)
    triples = list(zip(a.data[::3], a.data[1::3], a.data[2::3]))
    gpt, vocab = torch_model.from_npz(a.npz, a.device)
    items, counts = [], {"rows_read": 0, "usable": 0, "unusable": {"n<2": 0, "skip": 0, "too_long": 0, "len_mismatch": 0}, "per_file": []}
    for rp, dp, tpth in triples:
        rows = score.load_rows(rows=rp)
        try:
            with open(dp, encoding="utf-8") as f:
                cands = score.load_dump(f, len(rows))
        except ValueError as e:
            raise Mismatch(f"{os.path.basename(rp)} vs its dump: {e}")
        it, c = join_rows_b(rows, cands, tp.read_teacher(tpth), os.path.basename(rp))
        items += it
        counts["per_file"].append(c)
        for k in ("rows_read", "usable"):
            counts[k] += c[k]
        for k, v in c["unusable"].items():
            counts["unusable"][k] += v
    if not items:
        sys.exit("train_b: no usable rows")
    table = build_syl_table(it[3] for it in items)
    counts["n_syl"] = len(table)
    model = ReadingGPT(gpt, table).to(a.device)
    P = PackedB(items, vocab, syl_ids(table))
    val_idx, batches = tp.plan(len(P), a.val_frac, a.batch, a.epochs, a.seed, a.max_steps)
    total = len(batches)
    order_sha = hashlib.sha256(np.concatenate(batches).tobytes()).hexdigest()
    input_sha = {"npz": tp.sha256_file(a.npz), "files": [[os.path.basename(p), tp.sha256_file(p)] for tr in triples for p in tr]}
    print(f"rows read {counts['rows_read']}, usable {counts['usable']}, unusable {counts['unusable']}, syllables {len(table)} (with none, unknown), "
          f"val {len(val_idx)}, steps {total}, train order sha256 {order_sha[:16]}", flush=True)

    torch.manual_seed(a.seed)
    params = list(model.parameters())
    opt = torch.optim.AdamW([{"params": [p for p in params if p.ndim >= 2], "weight_decay": 0.1},
                             {"params": [p for p in params if p.ndim < 2], "weight_decay": 0.0}],
                            lr=a.lr, betas=(0.9, 0.95), fused=cuda)
    t0, acc, curve, val_curve, first, rows_done = time.time(), torch.zeros((), device=a.device), [], [], None, 0
    if len(val_idx):                      # step 0 = the start (M itself, R all zero): the baseline of the validation curve
        v, va = evaluate_b(model, P, val_idx, a.device, cuda)
        val_curve.append((0, v, va))
        print(f"step 0 val KL {v:.4f} top1 agreement {va:.4f} ({len(val_idx)} rows)", flush=True)
    model.train()
    for step, batch in enumerate(prefetch_b(P, batches)):
        t = tp.to_tensors(batch, a.device)
        for g in opt.param_groups:
            g["lr"] = s7a_train.lr_at(step, total, peak=a.lr, warmup=a.warmup)
        with torch.autocast(device_type="cuda", dtype=torch.bfloat16, enabled=cuda):
            S = cand_scores_b(model, t)
        loss = tp.kl_rows(S, t["q"], t["logq"]).mean()
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
            if tp.diverged(m, first, end, a.warmup):
                print("DIVERGED", flush=True)
                sys.exit(2)
            first = m if first is None else first
        if len(val_idx) and (end % a.val_every == 0 or end == total):
            v, va = evaluate_b(model, P, val_idx, a.device, cuda)
            val_curve.append((end, v, va))
            print(f"step {end} val KL {v:.4f} top1 agreement {va:.4f}", flush=True)
    secs = time.time() - t0
    save_b(model, vocab.chars, os.path.join(a.out, "tinylm-B.npz"))
    json.dump({"seed": a.seed, "epochs": a.epochs, "batch": a.batch, "lr": a.lr, "warmup": a.warmup, "val_frac": a.val_frac,
               "max_steps": a.max_steps, "steps": total, "counts": counts, "n_syl": len(table), "val_rows": int(len(val_idx)),
               "train_order_sha256": order_sha, "curve": curve, "val_curve": val_curve, "seconds": secs, "input_sha256": input_sha},
              open(os.path.join(a.out, "train-B.json"), "w"), indent=1)
    print(f"done: {total} steps, {secs:.0f}s", flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--npz", required=True)
    ap.add_argument("--data", nargs="+", required=True, help="ROWS DUMP TEACHER [ROWS DUMP TEACHER ...]")
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=20261018); ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--batch", type=int, default=64); ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--warmup", type=int, default=100); ap.add_argument("--val-frac", type=float, default=0.02)
    ap.add_argument("--max-steps", type=int, default=0)
    ap.add_argument("--log-every", type=int, default=tp.LOG_EVERY); ap.add_argument("--val-every", type=int, default=tp.VAL_EVERY)
    ap.add_argument("--device", default=None)
    a = ap.parse_args(argv)
    if len(a.data) % 3:
        ap.error("--data takes ROWS DUMP TEACHER triples")
    if torch is None:
        sys.exit("train_b: torch is not installed")
    a.device = a.device or ("cuda" if torch.cuda.is_available() else "cpu")
    try:
        run(a)
    except Mismatch as e:
        sys.exit(f"train_b: STOP, {e}")


if __name__ == "__main__":
    main()
