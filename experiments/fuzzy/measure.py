"""One measurement cell: python3 experiments/fuzzy/measure.py --set cvtune --file PATH --kind tone --gate G1 --p 0.05 --seed 1 --delta 2
--set dev299|typing76|cvtune|wikitune (cvtune/wikitune need --file PATH; any other --set value is read as a file path).
Columns (same in grid.py's TSV): rows = rows of the cell's set; n = rows the other columns count over (clean: all rows;
  p > 0: rows the injection changed, in the class).
--p 0 is clean input: prints base/gate top1, fixed, broken, exact rank 6-9 rows pushed out of the top 9, time ratio;
  --rowstats OUT writes the gate's rowstats and OUT.base the exact decoder's (feed both to tools/evalstats.py compare --net).
--p > 0 prints, per class (all / invalid / valid replacement): n changed rows; excluded = changed rows the exact decoder already
  gets wrong on the clean input (not rescuable, left out of wrong); wrong = the other changed rows the exact decoder gets wrong;
  empty = of those, rows whose exact list is empty (G1 gives at most 4 sentences there, so its top 9 is a top 4); rescue1,
  rescue9; rescue9_ne = rescue9 without the empty rows (compare G1 on this one). time_ratio (class all only) = gate time /
  exact decode time of the clean input.
Numbers only, no sentences."""
import argparse
import sys

import fuzzy as F

COLS = ["set", "kind", "gate", "p", "delta", "cls", "rows", "n", "excluded", "wrong", "empty", "rescue1", "rescue9", "rescue9_ne",
        "base_top1", "gate_top1", "fixed", "broken", "pushed9", "time_ratio"]


def cell_rows(setname, kind, gate, p, delta, recs):
    """TSV rows (dicts keyed by COLS) of one cell; clean when p == 0."""
    head = dict(set=setname, kind=kind, gate=gate, p=p, delta=delta, rows=len(recs))
    if p == 0:
        b, g, fx, br, pushed = F.clean_stats(recs, gate)[:5]
        return [dict(head, cls="clean", n=len(recs), base_top1=b, gate_top1=g, fixed=fx, broken=br, pushed9=pushed,
                     time_ratio=f"{F.time_ratio(recs, gate):.3f}")]
    out = []
    for cls in ("all", "invalid", "valid"):
        n, excl, wrong, r1, r9, empty, r9_ne = F.rescue(recs, gate, cls)
        out.append(dict(head, cls=cls, n=n, excluded=excl, wrong=wrong, empty=empty, rescue1=r1, rescue9=r9, rescue9_ne=r9_ne,
                        time_ratio=f"{F.time_ratio(recs, gate):.3f}" if cls == "all" else ""))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", required=True); ap.add_argument("--file")
    ap.add_argument("--kind", choices=F.KINDS, required=True); ap.add_argument("--gate", choices=F.GATES, required=True)
    ap.add_argument("--p", type=float, required=True); ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--delta", type=float, default=2.0); ap.add_argument("--profile", default="chat", choices=["chat", "formal"])
    ap.add_argument("--pairs", default=",".join(F.FUZZY_PAIRS), help="fuzzy kind: enabled pairs, e.g. ㄣㄥ,ㄓㄗ")
    ap.add_argument("--limit", type=int); ap.add_argument("--rowstats")
    a = ap.parse_args()
    rows = F.load_set(a.file if a.set in ("cvtune", "wikitune") and a.file else a.set)
    rows = rows[:a.limit] if a.limit else rows
    ctx = F.Ctx(a.profile)
    recs = F.process(ctx, rows, a.kind, a.p, a.seed, a.delta, a.pairs.split(","))
    print("\t".join(COLS))
    for r in cell_rows(a.set, a.kind, a.gate, a.p, a.delta, recs):
        print("\t".join(str(r.get(c, "")) for c in COLS))
    if a.rowstats:
        if a.p != 0:
            sys.exit("--rowstats needs --p 0")
        base, gate = F.clean_stats(recs, a.gate)[5:]
        open(a.rowstats, "w", encoding="utf-8", newline="").write(gate)
        open(a.rowstats + ".base", "w", encoding="utf-8", newline="").write(base)


if __name__ == "__main__":
    main()
