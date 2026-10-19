"""P3 candidate scoring: score.py's records with a choosable context, plus strict correctness.

  python score_p3.py --npz tinylm-S64.npz --backend numpy|torch --dump cvtune.chat.dump --rows cvtune.txt --name cvtune \
                     --expect-top1 3412 --context 2|64 --out scores/S64.chat.cvtune.jsonl
  rows: --rows FILE or --dev N, exactly as s7a-tinylm/score.py (whose load_rows / load_dump / top1_from_dump / load_model are imported).

Records have score.py's fields (i, n, ng, ok, ed, glen, key, key16, and for n >= 2 ll, ll0) so fusegrid.py / p2ana.py read them unchanged;
ll16 is not written. --context 2: k = row_key(prefix, 2), identical to score.py. --context 64: k = k_cross(prefix, CTX - 1 - len(c)) per
candidate; `key` is the length of the k of the first candidate. Extra field `oks`: 1 when the candidate equals the gold exactly (not lenient).
"""
import argparse
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "s7a-tinylm"))      # S7a is imported, never edited
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "p2-distill"))
from tinylm import CTX, ll_many, row_key           # noqa: E402
from score import load_rows, load_dump, top1_from_dump, load_model  # noqa: E402
from train_p2 import k_cross                        # noqa: E402
from eval import lenient                            # noqa: E402
from evalstats import levenshtein                   # noqa: E402


def key_for(prefix, cand, context):
    return k_cross(prefix, CTX - 1 - len(cand)) if context == 64 else row_key(prefix, 2)


def build_records(model, rows, cands, context=2, bs=64):
    recs, items = [], {"ll": [], "ll0": []}
    for i, ((gold, _syl, prefix), cs) in enumerate(zip(rows, cands), 1):
        g = lenient(gold)
        ok = [int(lenient(s) == g) for s, _ in cs]
        r = {"i": i, "n": len(cs), "ng": [sc for _, sc in cs], "ok": ok, "oks": [int(s == gold) for s, _ in cs],
             "ed": [0 if o else levenshtein(s, gold) for (s, _), o in zip(cs, ok)], "glen": len(gold),
             "key": len(key_for(prefix, cs[0][0], context)), "key16": len(row_key(prefix, 16))}
        if len(cs) >= 2:
            items["ll"] += [(key_for(prefix, s, context), s) for s, _ in cs]
            items["ll0"] += [("", s) for s, _ in cs]
        recs.append(r)
    done = {name: ll_many(model, it, bs) for name, it in items.items()}
    pos = {name: 0 for name in done}
    for r in recs:
        if r["n"] >= 2:
            for name in done:
                r[name] = done[name][pos[name]:pos[name] + r["n"]].tolist()
                pos[name] += r["n"]
    return recs


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--npz", required=True); ap.add_argument("--backend", choices=["numpy", "torch"], default="numpy")
    ap.add_argument("--dump", required=True); ap.add_argument("--name", required=True)
    ap.add_argument("--rows"); ap.add_argument("--dev", type=int); ap.add_argument("--limit", type=int)
    ap.add_argument("--context", type=int, choices=[2, 64], default=2)
    ap.add_argument("--expect-top1", type=int, required=True, help="the top1 of the CLI / lm_eval.py summary line for the same rows and settings")
    ap.add_argument("--out", required=True); ap.add_argument("--bs", type=int, default=64)
    a = ap.parse_args(argv)
    if bool(a.rows) == bool(a.dev):
        ap.error("exactly one of --rows / --dev")
    rows = load_rows(a.rows, a.dev, a.limit)
    cands = load_dump(open(a.dump, encoding="utf-8"), len(rows))
    t1 = top1_from_dump(rows, cands)
    with open(a.dump, "rb") as f:
        dump_sha = hashlib.sha256(f.read()).hexdigest()
    print(f"{a.name}: rows {len(rows)}, dump sha256 {dump_sha}, dump candidates {sum(map(len, cands))}, "
          f"n-gram top1 from the dump {t1}, summary line {a.expect_top1}, context {a.context}")
    if t1 != a.expect_top1:
        sys.exit("score_p3: STOP, top1 recomputed from the dump differs from the summary line")
    recs = build_records(load_model(a.npz, a.backend), rows, cands, a.context, a.bs)
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
