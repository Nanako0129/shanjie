"""S7b section 2.3 (docs/contracts/s7b-larger-lm.md): is int8 enough, or is FP32 needed? Numbers only.

  python int8check.py dequant --npz WORK/tinylm-M.npz --out WORK/tinylm-M8.npz
      latency.quantize_int8 (per-row symmetric int8 for float matrices, float32 scales, vectors unchanged), then back to float32,
      so run_sets.py / score.py score the copy with the same numpy forward: this measures weight-only int8.
  python int8check.py compare --scores DIR --fp32 M --int8 M8 --chat 0.7 2 --formal 0.7 2 \
         --sets cvtune wikitune dev302 typing76 user-reported discordtune --out OUT
      reranks the fp32 and the int8 score files of every set and setting with the fp32 model's frozen (alpha, tau) (no re-selection),
      prints int8-vs-n-gram and int8-vs-fp32 table rows, the S7a section-4 decision for int8, and the verdict:
      "int8 enough" = section 4 passes and no int8-vs-fp32 cell is net-negative with p < 0.05; otherwise "FP32 needed".
      Exit 3 when a required cell (fusegrid.REQUIRED_CELLS) has no score files.
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
    frozen = {"chat": tuple(a.chat), "formal": tuple(a.formal)}
    os.makedirs(os.path.join(a.out, "rowstats"), exist_ok=True)
    cells, vs_fp32 = {}, {}
    for setting in fg.SETTINGS:
        al, ta = frozen[setting]
        print(f"\n## {a.int8} vs {a.fp32}, {setting}: frozen alpha {al}, tau {ta} (from {a.fp32}, not re-selected)")
        for k, s in enumerate(a.sets):
            pf, pq = (os.path.join(a.scores, f"{m}.{setting}.{s}.jsonl") for m in (a.fp32, a.int8))
            if not (os.path.isfile(pf) and os.path.isfile(pq)):
                continue
            rf, rq = fg.load_scores(pf), fg.load_scores(pq)
            same_candidates(rf, rq, pq)
            base, f32, i8 = (os.path.join(a.out, "rowstats", f"{a.int8}.{setting}.{s}.{x}.txt") for x in ("base", "fp32", "int8"))
            open(base, "w", newline="").write(fg.rowstats(rf, [0] * len(rf)))
            open(f32, "w", newline="").write(fg.rowstats(rf, [fg.pick(r, al, ta) for r in rf]))
            open(i8, "w", newline="").write(fg.rowstats(rq, [fg.pick(r, al, ta) for r in rq]))
            print(fg.table_row(base, i8, f"{a.int8} vs n-gram {setting} {s}", first=(k == 0)))
            print(fg.table_row(f32, i8, f"{a.int8} vs {a.fp32} {setting} {s}", first=False))
            cells[(setting, s)] = fg.cell_stats(base, i8)
            vs_fp32[(setting, s)] = fg.cell_stats(f32, i8)
            if (setting, s) == ("chat", "discordtune"):
                cells[(setting, s)]["ci_lo"] = evalstats.paired_bootstrap(evalstats.read_rowstats(base), evalstats.read_rowstats(i8))[0][0]
    missing = [c for c in fg.REQUIRED_CELLS if c not in cells]     # whatever --sets says, every required cell must be there
    print(f"\n### section 4, {a.int8} (vs the n-gram first)")
    dec = fg.decision(cells)
    for crit, ok in dec:
        print(f"- {'PASS' if ok else 'FAIL'}: {crit}")
    bad = sorted(k for k, c in vs_fp32.items() if c["fixed"] < c["broken"] and c["p"] < 0.05)
    print(f"- {'PASS' if not bad else 'FAIL'}: no {a.int8}-vs-{a.fp32} cell net-negative with p < 0.05 (bad: {bad})")
    enough = dec[-1][1] and not bad
    print(f"verdict: {'int8 enough' if enough else 'FP32 needed'}")
    if missing:
        print(f"STOP: required cells missing {missing}")
        sys.exit(3)
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
