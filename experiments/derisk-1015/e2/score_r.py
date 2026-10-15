"""E2 scorer: experiments/s7a-tinylm/score.py with the reading channel. Same candidates (the top 8 of a lm_eval.py --dump), same records
and the same file format (so judge.py, fusegrid.py and parity.py read the output), but LL comes from tinylm_r.ll_many_r.

  one set:   python score_r.py one --arm control|new --npz tinylm-M.npz|tinylm-Mr.npz --backend numpy|torch --dump cvtune.chat.dump \
                         --rows cvtune.txt --name cvtune --expect-top1 N --out scores/E2C.chat.cvtune.jsonl
  all sets:  python score_r.py sets --arm control|new --npz ... --backend ... --work DIR --tune TUNEDIR --model-name E2C --out SCORES \
                         --sets cvtune wikitune dev302 typing76 user-reported [chatclause --chatclause F] [lytune --lytune F] [wikitypo --wikitypo F] \
                         [discordtune --discord F] [--reported-rows 66]
             (reads DIR/dumps/<set>.<setting>.dump and .top1 from run_sets.py dumps; writes SCORES/<model-name>.<setting>.<set>.jsonl)

control: the weights tinylm-v5 was exported from (tinylm-M.npz), a plain NumpyLM / TorchLM: the reading input is off.
new:     tinylm-Mr.npz (train_r.py), reading input on. The candidates' readings are the row's reading field (shared by every candidate);
         the BOS and the context key are NOREAD. A row whose candidates do not all have the length of the reading field (or with a syllable
         outside the model's table) is scored with NO readings for all its candidates; the count is printed.
"""
import argparse
import hashlib
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tinylm_r  # noqa: E402  (puts experiments/s7a-tinylm, reference/proto, tools on sys.path)
from tinylm_r import ll_many_r, row_readings  # noqa: E402
from tinylm import row_key  # noqa: E402
import score  # noqa: E402  experiments/s7a-tinylm/score.py (load_rows, load_dump, top1_from_dump, TOPK; not edited)
import run_sets  # noqa: E402  rows_args for the set names
from eval import lenient  # noqa: E402
from evalstats import levenshtein  # noqa: E402


def load_model(arm, npz, backend):
    """control: the S7a classes (no reading input). new: NumpyLMR / TorchLMR."""
    if arm == "control":
        return score.load_model(npz, backend)
    if backend == "numpy":
        return tinylm_r.NumpyLMR(npz)
    import torch
    from torch_r import TorchLMR
    return TorchLMR(npz, "cuda" if torch.cuda.is_available() else "cpu")


def build_records(model, rows, cands, bs=64):
    """score.build_records with readings. Returns (records, number of rows scored without readings although the model reads them)."""
    use = getattr(model, "has_readings", False)
    recs, items, bad = [], {"ll": [], "ll0": [], "ll16": []}, 0
    for i, ((gold, syl, prefix), cs) in enumerate(zip(rows, cands), 1):
        g = lenient(gold)
        ok = [int(lenient(s) == g) for s, _ in cs]
        r = {"i": i, "n": len(cs), "ng": [sc for _, sc in cs], "ok": ok,
             "ed": [0 if o else levenshtein(s, gold) for (s, _), o in zip(cs, ok)], "glen": len(gold)}
        k2, k16 = row_key(prefix, 2), row_key(prefix, 16)
        r["key"], r["key16"] = len(k2), len(k16)
        if len(cs) >= 2:
            rd, why = row_readings(model.rvocab, syl, [s for s, _ in cs]) if use else ([None] * len(cs), None)
            bad += use and why is not None
            for name, k in (("ll", k2), ("ll0", ""), ("ll16", k16)):
                items[name] += [(k, s, x) for (s, _), x in zip(cs, rd)]
        recs.append(r)
    done = {name: ll_many_r(model, it, bs) for name, it in items.items()}
    pos = {name: 0 for name in done}
    for r in recs:
        if r["n"] >= 2:
            for name in done:
                r[name] = done[name][pos[name]:pos[name] + r["n"]].tolist()
                pos[name] += r["n"]
    return recs, bad


def run_one(model, rows, dump, name, expect_top1, out, bs=64):
    cands = score.load_dump(open(dump, encoding="utf-8"), len(rows))
    t1 = score.top1_from_dump(rows, cands)
    sha = hashlib.sha256(open(dump, "rb").read()).hexdigest()
    print(f"{name}: rows {len(rows)}, dump sha256 {sha}, dump candidates {sum(map(len, cands))}, n-gram top1 from the dump {t1}, summary line {expect_top1}")
    if t1 != expect_top1:
        sys.exit("score_r: STOP, top1 recomputed from the dump differs from the summary line")
    recs, bad = build_records(model, rows, cands, bs)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
    print(f"{name}: rows scored without readings (length or syllable mismatch): {bad} of {sum(r['n'] >= 2 for r in recs)}; wrote {out}")
    return recs


def main_one(a):
    if bool(a.rows) == bool(a.dev):
        sys.exit("score_r: exactly one of --rows / --dev")
    rows = score.load_rows(a.rows, a.dev, a.limit)
    run_one(load_model(a.arm, a.npz, a.backend), rows, a.dump, a.name, a.expect_top1, a.out, a.bs)


def main_sets(a):
    os.makedirs(a.out, exist_ok=True)
    if a.reported_rows and "user-reported" in a.sets:
        run_sets.check_reported(os.path.join(run_sets.EVAL_DEV, "user-reported.txt"))
    model = load_model(a.arm, a.npz, a.backend)
    for name in a.sets:
        args = run_sets.rows_args(name, a)
        rows = score.load_rows(rows=args[args.index("--rows") + 1] if "--rows" in args else None,
                               dev=302 if "--dev" in args else None, limit=int(args[args.index("--limit") + 1]) if "--limit" in args else None)
        for setting in run_sets.SETTINGS:
            base = os.path.join(a.work, "dumps", f"{name}.{setting}")
            run_one(model, rows, base + ".dump", f"{a.model_name} {setting} {name}", int(open(base + ".top1").read().strip()),
                    os.path.join(a.out, f"{a.model_name}.{setting}.{name}.jsonl"), a.bs)


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for cmd in ("one", "sets"):
        p = sub.add_parser(cmd)
        p.add_argument("--arm", choices=["control", "new"], required=True)
        p.add_argument("--npz", required=True); p.add_argument("--backend", choices=["numpy", "torch"], default="numpy")
        p.add_argument("--out", required=True); p.add_argument("--bs", type=int, default=64)
    o = sub.choices["one"]
    o.add_argument("--dump", required=True); o.add_argument("--name", required=True); o.add_argument("--expect-top1", type=int, required=True)
    o.add_argument("--rows"); o.add_argument("--dev", type=int); o.add_argument("--limit", type=int)
    s = sub.choices["sets"]
    s.add_argument("--work", required=True); s.add_argument("--sets", nargs="+", required=True); s.add_argument("--model-name", required=True)
    s.add_argument("--tune"); s.add_argument("--discord"); s.add_argument("--reported-rows", type=int, choices=[run_sets.REPORTED_ROWS])
    for e in run_sets.EXTRA_SETS:
        s.add_argument(f"--{e}")
    a = ap.parse_args(argv)
    {"one": main_one, "sets": main_sets}[a.cmd](a)


if __name__ == "__main__":
    main()
