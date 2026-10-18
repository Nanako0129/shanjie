"""P2B scoring: the reading-aware student (train_b.py's tinylm-B.npz) on candidates -> the JSON-lines score file of s7a-tinylm/score.py.

  python score_b.py --npz tinylm-B.npz --backend torch --dump cvtune.chat.dump --rows cvtune.txt --name cvtune \
                    --expect-top1 3412 --out scores/B.chat.cvtune.jsonl [--bs 16]

Records: i, n, ng, ok, ed, glen, key, key16 as score.py writes them, and for rows with n >= 2: ll (k = context_key), ll0 (k empty), ll16
(a COPY of ll, as in E-B: the reading student is not scored with a 16-character key). LL is log10 of the candidate's score
sum_j ln p(c_j | [BOS] k c_<j) with the reading input, i.e. the very function train_b.py trains. A row with n >= 2 whose candidates do not
all have as many characters as the row has syllables (len_mismatch), or with a candidate too long for the context (too_long), gets ll = ll0 =
ll16 = 0.0 and noll = 1, so fusegrid.pick keeps the n-gram first. Syllables outside the model's table use the unknown row. CPU, float32, no TF32.
"""
import argparse
import hashlib
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import train_b as tb               # noqa: E402  (adds p2-distill and s7a-tinylm to sys.path)
import score                       # noqa: E402
from tinylm import CTX, LN10, row_key  # noqa: E402
from eval import lenient           # noqa: E402
from evalstats import levenshtein  # noqa: E402


def reason(syls, cs):
    """Why a row with n >= 2 cannot be scored, else None."""
    if any(len(s) != len(syls) for s, _ in cs):
        return "len_mismatch"
    if any(len(s) > CTX - 1 for s, _ in cs):
        return "too_long"
    return None


def ll_b(model, vocab, items, bs=16):
    """items [(prefix, surfs, syls)] -> one float64 array of log10 LL per item (one entry per candidate). The prefix goes through
    row_key(prefix, 2) like score.py's `ll`; pass "" for the empty-k column."""
    import torch
    sid = tb.syl_ids(model.table)
    P = tb.PackedB([(p, surfs, [0.0] * len(surfs), syls) for p, surfs, syls in items], vocab, sid)
    out = []
    with torch.no_grad():
        for s in range(0, len(items), bs):
            idx = np.arange(s, min(s + bs, len(items)))
            t = tb.tp.to_tensors(tb.collate_b(P, idx), "cpu")
            S = tb.cand_scores_b(model, t).double().numpy()
            out += [S[r, :len(items[i][1])] / LN10 for r, i in enumerate(idx)]
    return out


def build_records(model, vocab, rows, cands, bs=16):
    """-> (records, counts of unscorable rows by reason)."""
    recs, todo, why = [], [], {"len_mismatch": 0, "too_long": 0}
    for i, ((gold, syls, prefix), cs) in enumerate(zip(rows, cands), 1):      # base fields: score.build_records, line for line
        g = lenient(gold)
        ok = [int(lenient(s) == g) for s, _ in cs]
        r = {"i": i, "n": len(cs), "ng": [sc for _, sc in cs], "ok": ok,
             "ed": [0 if o else levenshtein(s, gold) for (s, _), o in zip(cs, ok)], "glen": len(gold)}
        r["key"], r["key16"] = len(row_key(prefix, 2)), len(row_key(prefix, 16))
        if len(cs) >= 2:
            bad = reason(syls, cs)
            if bad:
                why[bad] += 1
                r.update(ll=[0.0] * len(cs), ll0=[0.0] * len(cs), ll16=[0.0] * len(cs), noll=1)
            else:
                todo.append((r, prefix, [s for s, _ in cs], syls))
        recs.append(r)
    ll = ll_b(model, vocab, [(p, c, y) for _, p, c, y in todo], bs)
    ll0 = ll_b(model, vocab, [("", c, y) for _, _, c, y in todo], bs)
    for (r, *_), a, b in zip(todo, ll, ll0):
        r["ll"], r["ll0"], r["ll16"] = a.tolist(), b.tolist(), a.tolist()
    return recs, why


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--npz", required=True); ap.add_argument("--backend", choices=["torch"], default="torch")
    ap.add_argument("--dump", required=True); ap.add_argument("--name", required=True)
    ap.add_argument("--rows"); ap.add_argument("--dev", type=int); ap.add_argument("--limit", type=int)
    ap.add_argument("--expect-top1", type=int, required=True, help="the top1 of the CLI / lm_eval.py summary line for the same rows and settings")
    ap.add_argument("--out", required=True); ap.add_argument("--bs", type=int, default=16, help="rows per batch (<= 8 sequences each)")
    a = ap.parse_args(argv)
    if bool(a.rows) == bool(a.dev):
        ap.error("exactly one of --rows / --dev")
    rows = score.load_rows(a.rows, a.dev, a.limit)
    cands = score.load_dump(open(a.dump, encoding="utf-8"), len(rows))
    t1 = score.top1_from_dump(rows, cands)
    with open(a.dump, "rb") as f:
        dump_sha = hashlib.sha256(f.read()).hexdigest()
    print(f"{a.name}: rows {len(rows)}, dump sha256 {dump_sha}, dump candidates {sum(map(len, cands))}, "
          f"n-gram top1 from the dump {t1}, summary line {a.expect_top1}")
    if t1 != a.expect_top1:
        sys.exit("score_b: STOP, top1 recomputed from the dump differs from the summary line")
    import torch
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    model, vocab = tb.load_b(a.npz, "cpu")
    model.eval()
    recs, why = build_records(model, vocab, rows, cands, a.bs)
    n2 = sum(r["n"] >= 2 for r in recs)
    known = tb.syl_ids(model.table)
    unk = sum(s not in known for _, syls, _ in rows for s in syls)
    print(f"{a.name}: rows with n >= 2 {n2}, not scored (noll = 1) {sum(why.values())}: {why}; syllables outside the table {unk}; ll16 is a copy of ll")
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
