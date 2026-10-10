"""S7a candidate scoring: candidates (top 8 of a lm_eval.py --dump) x LL -> a numbers-only score file (JSON lines, no text).

  python score.py --npz tinylm-S.npz --backend numpy|torch --dump cvtune.chat.dump --rows cvtune.txt --name cvtune \
                  --expect-top1 3412 --out scores/S.chat.cvtune.jsonl [--ppl-out ppl.json]
  rows: --rows FILE (format `prefix|sentence|reading`) or --dev N (first N rows of eval/dev/*.txt, sorted, like lm_eval.py --dev).

One record per row: i (1-based), n (candidates kept, <= 8), ng (n-gram scores), ok (lenient-correct 0/1), ed (Levenshtein of the
candidate to the gold, 0 when ok), glen, key / key16 (length of k = context_key / of the 16-character key), and for rows with n >= 2:
ll (LL with k = context_key), ll0 (k empty), ll16 (k = last 16 Han characters of the prefix). LL is the same function for every column (tinylm.ll_many).
"""
import argparse
import glob
import hashlib
import json
import os
import sys

import numpy as np

import tinylm
from tinylm import ROOT, ll_many, row_key

from lm_eval import rows_of  # noqa: E402  reference/proto: the very row reader of the evaluation (rows with 3 fields only)
from eval import lenient  # noqa: E402
from evalstats import levenshtein  # noqa: E402

TOPK = 8


def load_rows(rows=None, dev=None, limit=None):
    """[(gold, syllables, prefix)] exactly as lm_eval.py reads them."""
    if dev:
        return rows_of(sorted(glob.glob(os.path.join(ROOT, "eval", "dev", "*.txt"))))[:dev]
    r = rows_of([rows])
    return r[:limit] if limit else r


def load_dump(lines, n_rows, topk=TOPK):
    """lm_eval.py --dump lines `row<TAB>rank<TAB>surface<TAB>score` -> per row [(surface, score)] of ranks 1..topk, in rank order.
    Stops (ValueError) if a row is missing, its ranks do not start at 1 and run without gaps, or the dump has rows beyond n_rows."""
    out = [[] for _ in range(n_rows)]
    seen = set()
    for line in lines:
        i, rank, surf, sc = line.rstrip("\n").split("\t")
        i, rank = int(i), int(rank)
        if not 1 <= i <= n_rows:
            raise ValueError(f"dump row {i} outside 1..{n_rows}")
        if rank != len(out[i - 1]) + 1 and rank <= topk:
            raise ValueError(f"dump row {i}: rank {rank} after {len(out[i - 1])} candidates")
        if rank > topk:
            seen.add(i)
            continue
        out[i - 1].append((surf, float(sc)))
        seen.add(i)
    if len(seen) != n_rows or any(not c for c in out):
        raise ValueError(f"dump covers {len(seen)} of {n_rows} rows")
    return out


def top1_from_dump(rows, cands):
    """The n-gram baseline recomputed from the dump's first candidates: the number that must equal the CLI summary line's top1."""
    return sum(lenient(c[0][0]) == lenient(r[0]) for r, c in zip(rows, cands))


def build_records(model, rows, cands, bs=64):
    recs, items = [], {"ll": [], "ll0": [], "ll16": []}
    for i, ((gold, _syl, prefix), cs) in enumerate(zip(rows, cands), 1):
        g = lenient(gold)
        ok = [int(lenient(s) == g) for s, _ in cs]
        r = {"i": i, "n": len(cs), "ng": [sc for _, sc in cs], "ok": ok,
             "ed": [0 if o else levenshtein(s, gold) for (s, _), o in zip(cs, ok)], "glen": len(gold)}
        k2, k16 = row_key(prefix, 2), row_key(prefix, 16)
        r["key"], r["key16"] = len(k2), len(k16)       # all rows: the report counts rows whose key is not the sentinel
        if len(cs) >= 2:
            for name, k in (("ll", k2), ("ll0", ""), ("ll16", k16)):
                items[name] += [(k, s) for s, _ in cs]
        recs.append(r)
    done = {name: ll_many(model, it, bs) for name, it in items.items()}
    pos = {name: 0 for name in done}
    for r in recs:
        if r["n"] >= 2:
            for name in done:
                r[name] = done[name][pos[name]:pos[name] + r["n"]].tolist()
                pos[name] += r["n"]
    return recs


def gold_ppl(model, rows, bs=64):
    """Per-character perplexity of the gold sentences (same LL, k = context_key of the row's prefix, one `LL` call per row)."""
    items = [(row_key(prefix, 2), gold) for gold, _s, prefix in rows]
    total, chars = float(ll_many(model, items, bs).sum()), sum(len(g) for g, _s, _p in rows)
    return {"rows": len(rows), "chars": chars, "sum_ll10": total, "ppl_per_char": 10 ** (-total / chars)}


def load_model(npz, backend):
    if backend == "numpy":
        return tinylm.NumpyLM(npz)
    import torch
    from torch_model import TorchLM
    return TorchLM(npz, "cuda" if torch.cuda.is_available() else "cpu")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--npz", required=True); ap.add_argument("--backend", choices=["numpy", "torch"], default="numpy")
    ap.add_argument("--dump", required=True); ap.add_argument("--name", required=True)
    ap.add_argument("--rows"); ap.add_argument("--dev", type=int); ap.add_argument("--limit", type=int)
    ap.add_argument("--expect-top1", type=int, required=True, help="the top1 of the CLI / lm_eval.py summary line for the same rows and settings")
    ap.add_argument("--out", required=True); ap.add_argument("--ppl-out"); ap.add_argument("--bs", type=int, default=64)
    a = ap.parse_args(argv)
    if bool(a.rows) == bool(a.dev):
        ap.error("exactly one of --rows / --dev")
    rows = load_rows(a.rows, a.dev, a.limit)
    cands = load_dump(open(a.dump, encoding="utf-8"), len(rows))
    t1 = top1_from_dump(rows, cands)
    with open(a.dump, "rb") as f:
        dump_sha = hashlib.sha256(f.read()).hexdigest()
    print(f"{a.name}: rows {len(rows)}, dump sha256 {dump_sha}, dump candidates {sum(map(len, cands))}, "
          f"n-gram top1 from the dump {t1}, summary line {a.expect_top1}")
    if t1 != a.expect_top1:
        sys.exit("score: STOP, top1 recomputed from the dump differs from the summary line")
    model = load_model(a.npz, a.backend)
    recs = build_records(model, rows, cands, a.bs)
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
    if a.ppl_out:
        p = gold_ppl(model, rows, a.bs)
        json.dump(dict(p, name=a.name), open(a.ppl_out, "w"))
        print(f"{a.name}: gold perplexity per character {p['ppl_per_char']:.3f} over {p['chars']} characters")
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
