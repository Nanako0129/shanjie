"""Whole grid -> one TSV (docs/contracts/fuzzy-research.md section 2). Runs on any machine with Python 3.
  python3 experiments/fuzzy/grid.py --cvtune CV.txt --wikitune WIKI.txt --out OUT.tsv [--seed 1] [--ps 0.05,0.1,0.2] [--deltas 0.5,1,2,3,4] [--limit N]
Phase tune: per kind, delta chosen on cvtune+wikitune pooled: most G2 rescue@1 at p=0.1 among deltas whose clean G2 break rate <= 0.5%
  (none qualifies -> the largest delta, marked fallback). Rows: phase=tune, one per delta.
Phase report: dev302 and typing76, every kind x gate x p with the chosen delta, plus clean (p=0) with fixed/broken/pushed9/time ratio.
Also writes OUT.evalstats.md: the tools/evalstats.py compare --net table row of each clean report cell."""
import argparse
import os
import tempfile

import fuzzy as F
import measure as M
from evalstats import compare

TUNE_P, MAX_BREAK = 0.1, 0.005


def tune(ctx, rows, kind, p, seed, deltas):
    """Rows pooled from several sets; returns [(delta, rescue1, wrong, broken, n_clean)]."""
    out = []
    for d in deltas:
        rec_p = F.process(ctx, rows, kind, p, seed, d)
        rec_0 = F.process(ctx, rows, kind, 0, seed, d)
        _, wrong, r1, _ = F.rescue(rec_p, "G2")
        out.append((d, r1, wrong, F.clean_stats(rec_0, "G2")[3], len(rec_0)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cvtune", required=True); ap.add_argument("--wikitune", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=1); ap.add_argument("--profile", default="chat")
    ap.add_argument("--ps", default="0.05,0.1,0.2"); ap.add_argument("--deltas", default="0.5,1,2,3,4")
    ap.add_argument("--limit", type=int, help="rows per set (smoke test)")
    a = ap.parse_args()
    ps, deltas = [float(x) for x in a.ps.split(",")], [float(x) for x in a.deltas.split(",")]
    lim = lambda r: r[:a.limit] if a.limit else r
    ctx = F.Ctx(a.profile)
    tune_rows = lim(F.load_set(a.cvtune)) + lim(F.load_set(a.wikitune))
    out = open(a.out, "w", encoding="utf-8", newline="")
    md = open(a.out + ".evalstats.md", "w", encoding="utf-8")
    out.write("\t".join(M.COLS + ["note"]) + "\n")
    row = lambda r, note="": out.write("\t".join(str(r.get(c, "")) for c in M.COLS) + "\t" + note + "\n") and out.flush()
    chosen = {}
    for kind in F.KINDS:
        res = tune(ctx, tune_rows, kind, TUNE_P, a.seed, deltas)
        ok = [r for r in res if r[3] / r[4] <= MAX_BREAK]
        pick = max(ok, key=lambda r: (r[1], -r[0])) if ok else max(res, key=lambda r: r[0])
        chosen[kind] = pick[0]
        for d, r1, wrong, broken, n in res:
            row(dict(set="tune", kind=kind, gate="G2", p=TUNE_P, delta=d, cls="all", n=n, wrong=wrong, rescue1=r1, broken=broken),
                "chosen" if d == pick[0] and ok else ("fallback" if d == pick[0] else ""))
    for name in ("dev302", "typing76"):
        rows = lim(F.load_set(name))
        for kind in F.KINDS:
            d = chosen[kind]
            clean = F.process(ctx, rows, kind, 0, a.seed, d)
            for gate in F.GATES:
                for r in M.cell_rows(name, kind, gate, 0, d, clean):
                    row(r)
                _, _, _, _, _, base_rs, gate_rs = F.clean_stats(clean, gate)
                with tempfile.TemporaryDirectory() as td:
                    b, g = os.path.join(td, "b"), os.path.join(td, "g")
                    open(b, "w", newline="").write(base_rs); open(g, "w", newline="").write(gate_rs)
                    md.write(compare(b, g, f"{name} {kind} {gate}", net=True).split("\n")[-1] + "\n")
            for p in ps:
                recs = F.process(ctx, rows, kind, p, a.seed, d)
                for gate in F.GATES:
                    for r in M.cell_rows(name, kind, gate, p, d, recs):
                        row(r)
    out.close(); md.close()


if __name__ == "__main__":
    main()
