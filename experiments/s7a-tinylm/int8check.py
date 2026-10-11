"""S7b section 2.3 (docs/contracts/s7b-larger-lm.md): is int8 enough, or is FP32 needed? Numbers only.

  python int8check.py dequant --npz WORK/tinylm-M.npz --out WORK/tinylm-M8.npz
      latency.quantize_int8 (per-row symmetric int8 for float matrices, float32 scales, vectors unchanged), then back to float32,
      so run_sets.py / score.py score the copy with the same numpy forward: this measures weight-only int8.
  python int8check.py compare --scores DIR --fp32 M --int8 M8 --chat 0.7 2 --formal 0.7 2 \
         --sets cvtune wikitune dev302 typing76 user-reported discordtune --out OUT
      reranks the fp32 and the int8 score files of every set and setting with the fp32 model's frozen (alpha, tau) (no re-selection),
      prints int8-vs-n-gram and int8-vs-fp32 table rows, the S7a section-4 decision for int8, and the verdict:
      "int8 enough" = section 4 passes and no int8-vs-fp32 cell is net-negative with p < 0.05; otherwise "FP32 needed"; "not applicable"
      when the fp32 model itself fails section 4. The given alpha/tau must be the fp32 model's own selection on cvtune+wikitune.
      Exit 3, before any verdict, when a set of --sets lacks score files in either setting or a section-4 cell is missing.
"""
import argparse
import os
import sys
import tempfile

import numpy as np

import fusegrid as fg  # first: it imports tinylm, which puts tools/ on sys.path
import evalstats  # noqa: E402
from latency import quantize_int8


def dequant(npz, out):
    with tempfile.TemporaryDirectory() as td:
        q = os.path.join(td, "q.npz")
        quantize_int8(npz, q)
        with np.load(q, allow_pickle=False) as z:      # closed before the temp dir goes (Windows cannot delete an open file)
            d = {k: (z[k].astype(np.float32) * z[k + ".scale"] if k + ".scale" in z.files else z[k])
                 for k in z.files if not k.endswith(".scale")}
    np.savez(out, **d)


def same_candidates(rf, rq, path):
    if [(r["i"], r["n"], r["ng"], r["ok"]) for r in rf] != [(r["i"], r["n"], r["ng"], r["ok"]) for r in rq]:
        sys.exit(f"int8check: {path}: the fp32 and int8 score files are not over the same candidates")


def compare(a):
    """Returns True ("int8 enough"), False ("FP32 needed") or None (not applicable: the fp32 model itself fails section 4)."""
    frozen = {"chat": tuple(a.chat), "formal": tuple(a.formal)}
    os.makedirs(os.path.join(a.out, "rowstats"), exist_ok=True)
    cells, cells_fp32, vs_fp32, missing = {}, {}, {}, []
    for setting in fg.SETTINGS:
        al, ta = frozen[setting]
        paths = {s: tuple(os.path.join(a.scores, f"{m}.{setting}.{s}.jsonl") for m in (a.fp32, a.int8)) for s in a.sets}
        tuning = {t: fg.load_scores(paths[t][0]) for t in fg.TUNING if t in paths and os.path.isfile(paths[t][0])}
        if len(tuning) == len(fg.TUNING):
            sel = fg.select(tuning)[:2]
            if sel != (al, ta):            # contract 2.3: the fp32 model's frozen values, not values typed for another model
                sys.exit(f"int8check: --{setting} {al} {ta} is not {a.fp32}'s frozen alpha/tau on {'+'.join(fg.TUNING)} ({sel[0]} {sel[1]})")
        print(f"\n## {a.int8} vs {a.fp32}, {setting}: frozen alpha {al}, tau {ta} (from {a.fp32}, not re-selected)")
        first = True
        for s in a.sets:
            pf, pq = paths[s]
            if not (os.path.isfile(pf) and os.path.isfile(pq)):
                missing.append((setting, s))
                continue
            rf, rq = fg.load_scores(pf), fg.load_scores(pq)
            same_candidates(rf, rq, pq)
            base, f32, i8 = (os.path.join(a.out, "rowstats", f"{a.int8}.{setting}.{s}.{x}.txt") for x in ("base", "fp32", "int8"))
            open(base, "w", newline="").write(fg.rowstats(rf, [0] * len(rf)))
            open(f32, "w", newline="").write(fg.rowstats(rf, [fg.pick(r, al, ta) for r in rf]))
            open(i8, "w", newline="").write(fg.rowstats(rq, [fg.pick(r, al, ta) for r in rq]))
            print(fg.table_row(base, i8, f"{a.int8} vs n-gram {setting} {s}", first=first))
            print(fg.table_row(f32, i8, f"{a.int8} vs {a.fp32} {setting} {s}", first=False))
            first = False
            cells[(setting, s)], cells_fp32[(setting, s)] = fg.cell_stats(base, i8), fg.cell_stats(base, f32)
            vs_fp32[(setting, s)] = fg.cell_stats(f32, i8)
            if (setting, s) == ("chat", "discordtune"):
                rb = evalstats.read_rowstats(base)
                cells[(setting, s)]["ci_lo"] = evalstats.paired_bootstrap(rb, evalstats.read_rowstats(i8))[0][0]
                cells_fp32[(setting, s)]["ci_lo"] = evalstats.paired_bootstrap(rb, evalstats.read_rowstats(f32))[0][0]
    # every set of --sets in both settings (12 cells for the six sets) and every section-4 cell must be there before any verdict
    missing += [c for c in fg.REQUIRED_CELLS if c not in cells and c not in missing]
    if missing:
        print(f"STOP: cells missing {sorted(missing)}")
        sys.exit(3)
    print(f"\n### section 4, {a.int8} (vs the n-gram first)")
    dec = fg.decision(cells)
    for crit, ok in dec:
        print(f"- {'PASS' if ok else 'FAIL'}: {crit}")
    bad = sorted(k for k, c in vs_fp32.items() if c["fixed"] < c["broken"] and c["p"] < 0.05)
    print(f"- {'PASS' if not bad else 'FAIL'}: no {a.int8}-vs-{a.fp32} cell net-negative with p < 0.05 (bad: {bad})")
    if not fg.decision(cells_fp32)[-1][1]:
        print(f"verdict: not applicable ({a.fp32} itself fails section 4, so neither int8 nor FP32 is a candidate)")
        return None
    enough = dec[-1][1] and not bad
    print(f"verdict: {'int8 enough' if enough else 'FP32 needed'}")
    return enough

def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dequant"); d.add_argument("--npz", required=True); d.add_argument("--out", required=True)
    c = sub.add_parser("compare")
    c.add_argument("--scores", required=True); c.add_argument("--out", required=True)
    c.add_argument("--fp32", required=True); c.add_argument("--int8", required=True)
    c.add_argument("--chat", nargs=2, type=float, required=True, metavar=("ALPHA", "TAU"))
    c.add_argument("--formal", nargs=2, type=float, required=True, metavar=("ALPHA", "TAU"))
    c.add_argument("--sets", nargs="+", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "dequant":
        dequant(a.npz, a.out)
    else:
        compare(a)


if __name__ == "__main__":
    main()
