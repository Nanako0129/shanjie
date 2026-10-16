"""E2 (docs/contracts/e2-chat-eval.md section 3.2): one evalstats table per fusion setting and evaluation set, 1:1 scoring.

  python judge.py --scores DIR [DIR ...] --sets cvtune wikitune ... --setting chat|formal --out OUT [--model M8] [--no-net] \
         [--fixed LABEL ALPHA TAU DELTA]... [--select LABEL --tuning SET [SET ...] --weight W [--cap R]]

Reads only DIR/<model>.<setting>.<set>.jsonl (score.py output) for the chosen setting, the first DIR that has the file. Each --fixed is
one (alpha, tau, delta) used as given. --select picks one by fusegrid.select_weighted (maximise fixed - W x broken over the --tuning
sets pooled, never choosing a cell whose broken / rows is above R) and reports it as one more configuration. For every configuration
and set: the picks come from fusegrid.pick, rowstats go to OUT/rowstats/<label>.<setting>.<set>.{base,new}.txt (only [0-9\\t\\n]), and
tools/evalstats.py compare prints one table row against the n-gram first (with --net unless --no-net). No S7e decision, and nothing
needs discordtune or the other setting.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "s7a-tinylm"))
import fusegrid as fg  # noqa: E402  (also puts tools/ on sys.path)
import evalstats  # noqa: E402


def find_scores(dirs, model, setting, name):
    for d in dirs:
        p = os.path.join(d, f"{model}.{setting}.{name}.jsonl")
        if os.path.isfile(p):
            return p
    sys.exit(f"judge: no {model}.{setting}.{name}.jsonl in {dirs}")


def table(label, setting, sets, params, out, net):
    """Markdown lines: header once, then one row per set. params = (alpha, tau, delta)."""
    alpha, tau, delta = params
    lines = []
    for k, (name, rows) in enumerate(sets.items()):
        tag = f"{label}.{setting}.{name}"
        bp, np_ = (os.path.join(out, "rowstats", f"{tag}.{x}.txt") for x in ("base", "new"))
        with open(bp, "w", newline="") as f:
            f.write(fg.rowstats(rows, [0] * len(rows)))
        with open(np_, "w", newline="") as f:
            f.write(fg.rowstats(rows, [fg.pick(r, alpha, tau, "ll", delta) for r in rows]))
        t = evalstats.compare(bp, np_, f"{label} {setting} {name}", net=net).split("\n")
        lines += t if k == 0 else t[2:]
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--scores", nargs="+", required=True); ap.add_argument("--sets", nargs="+", required=True)
    ap.add_argument("--setting", choices=fg.SETTINGS, required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="M8"); ap.add_argument("--no-net", action="store_true")
    ap.add_argument("--fixed", nargs=4, action="append", default=[], metavar=("LABEL", "ALPHA", "TAU", "DELTA"))
    ap.add_argument("--select", metavar="LABEL"); ap.add_argument("--tuning", nargs="+")
    ap.add_argument("--weight", type=float); ap.add_argument("--cap", type=float)
    a = ap.parse_args(argv)
    if bool(a.select) != bool(a.tuning) or (a.select and not a.weight > 0):
        ap.error("--select needs --tuning and --weight (> 0), and the other way round")
    if not a.select and (a.weight is not None or a.cap is not None):
        ap.error("--weight and --cap only apply with --select")
    if a.cap is not None and not 0 <= a.cap <= 1:      # also rejects NaN
        ap.error("--cap must be within [0, 1]")
    if a.select and not set(a.tuning) <= set(a.sets):
        ap.error("--tuning sets must be among --sets")
    if not a.fixed and not a.select:
        ap.error("nothing to do: give --fixed and/or --select")
    sets = {n: fg.load_scores(find_scores(a.scores, a.model, a.setting, n)) for n in a.sets}
    os.makedirs(os.path.join(a.out, "rowstats"), exist_ok=True)
    configs = [(lab, (float(al), float(ta), float(de))) for lab, al, ta, de in a.fixed]
    if a.select:
        if not sum(len(sets[t]) for t in a.tuning):
            ap.error("no tuning rows")
        al, ta, de, f, b = fg.select_weighted(sets, a.weight, names=tuple(a.tuning), cap=a.cap)
        n = sum(len(sets[t]) for t in a.tuning)
        print(f"\n{a.select}: selected on {'+'.join(a.tuning)} ({n} rows) by fixed - {a.weight:g} x broken"
              f"{'' if a.cap is None else f', broken / rows <= {a.cap:g}'}: alpha {al}, tau {ta}, delta {de}; fixed {f}, broken {b}")
        configs.append((a.select, (al, ta, de)))
    for label, params in configs:
        print(f"\n{label}: alpha {params[0]:g}, tau {params[1]:g}, delta {params[2]:g}")
        print("\n".join(table(label, a.setting, sets, params, a.out, not a.no_net)))
    return configs


if __name__ == "__main__":
    main()
