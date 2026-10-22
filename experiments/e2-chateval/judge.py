"""E2 (docs/contracts/e2-chat-eval.md section 3.2): one evalstats table per fusion setting and evaluation set, 1:1 scoring.

  python judge.py --scores DIR [DIR ...] --sets cvtune wikitune ... --setting chat|formal --out OUT [--model M8] [--no-net] \
         [--fixed LABEL ALPHA TAU DELTA]... [--select LABEL --tuning SET [SET ...] --weight W [--cap R | --cap-ub R] [--frontier]] [--jsubsets] [--diagnose]

Reads only DIR/<model>.<setting>.<set>.jsonl (score.py output) for the chosen setting, the first DIR that has the file. Each --fixed is
one (alpha, tau, delta) used as given. --select picks one by fusegrid.select_weighted (maximise fixed - W x broken over the --tuning
sets pooled, never choosing a cell whose broken / rows is above R) and reports it as one more configuration. --cap-ub R (instead of
--cap) judges the cap by the 95% upper bound of broken (one-sided exact Poisson, as compare --net) divided by n, all pooled rows, and prints the
chosen cell's n, n_Jright (informational), broken, point rate and bound. --frontier adds a table of the best cell under each cap 0.1% .. 0.5% (same
criterion as the run). --jsubsets prints, per configuration and set, the three fixed subsets J-right / J-wrong-in-top-8 /
J-wrong-outside-top-8. --diagnose prints per set, with no grid, the headroom and the small model's alone-pick and keep rates. For every configuration
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


def jsubset_lines(label, setting, sets, params):
    alpha, tau, delta = params
    out = []
    for name, rows in sets.items():
        j = fg.jsubsets(rows, [fg.pick(r, alpha, tau, "ll", delta) for r in rows])
        out.append(f"  {label} {setting} {name}: J-right n {j['n_right']}, broken {j['broken']}; J-wrong-in-top-8 n {j['n_wrong_in8']}, "
                   f"fixed {j['fixed']}; J-wrong-outside-top-8 n {j['n_wrong_out8']}")
    return out


def diagnose_lines(setting, sets):
    out = []
    for name, rows in sets.items():
        d = fg.diagnose(rows)
        out.append(f"  {setting} {name}: n {d['n']}, headroom (fusion ceiling) {d['headroom']} ({100 * d['share']:.2f}%), model alone right on headroom "
                   f"{d['alone_head']}, keeps candidate 0 on J-right {d['keep']} of {d['n_right']}, oracle@8 {d['oracle8']}")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--scores", nargs="+", required=True); ap.add_argument("--sets", nargs="+", required=True)
    ap.add_argument("--setting", choices=fg.SETTINGS, required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="M8"); ap.add_argument("--no-net", action="store_true")
    ap.add_argument("--fixed", nargs=4, action="append", default=[], metavar=("LABEL", "ALPHA", "TAU", "DELTA"))
    ap.add_argument("--select", metavar="LABEL"); ap.add_argument("--tuning", nargs="+")
    ap.add_argument("--weight", type=float); ap.add_argument("--cap", type=float)
    ap.add_argument("--cap-ub", type=float, dest="cap_ub"); ap.add_argument("--frontier", action="store_true")
    ap.add_argument("--jsubsets", action="store_true"); ap.add_argument("--diagnose", action="store_true")
    a = ap.parse_args(argv)
    if bool(a.select) != bool(a.tuning) or (a.select and not (a.weight or 0) > 0):
        ap.error("--select needs --tuning and --weight (> 0), and the other way round")
    if not a.select and (a.weight is not None or a.cap is not None or a.cap_ub is not None or a.frontier):
        ap.error("--weight, --cap, --cap-ub and --frontier only apply with --select")
    if a.cap is not None and a.cap_ub is not None:
        ap.error("--cap and --cap-ub are mutually exclusive")
    if a.cap_ub is not None and not 0 <= a.cap_ub <= 1:
        ap.error("--cap-ub must be within [0, 1]")
    if a.cap is not None and not 0 <= a.cap <= 1:      # also rejects NaN
        ap.error("--cap must be within [0, 1]")
    if a.select and not set(a.tuning) <= set(a.sets):
        ap.error("--tuning sets must be among --sets")
    if not a.fixed and not a.select and not a.diagnose:
        ap.error("nothing to do: give --fixed and/or --select (or --diagnose)")
    if a.jsubsets and not a.fixed and not a.select:
        ap.error("--jsubsets needs --fixed or --select")
    sets = {n: fg.load_scores(find_scores(a.scores, a.model, a.setting, n)) for n in a.sets}
    os.makedirs(os.path.join(a.out, "rowstats"), exist_ok=True)
    configs = [(lab, (float(al), float(ta), float(de))) for lab, al, ta, de in a.fixed]
    if a.select:
        if not sum(len(sets[t]) for t in a.tuning):
            ap.error("no tuning rows")
        n = sum(len(sets[t]) for t in a.tuning)
        if a.frontier:      # before the selection, so an infeasible cap still shows which caps have a cell
            print("\n| cap | alpha | tau | delta | fixed | broken | n | point rate | 95% upper bound |\n|---|---|---|---|---|---|---|---|---|")
            for c, cell in fg.frontier(sets, a.weight, names=tuple(a.tuning), ub=a.cap_ub is not None):
                if cell is None:
                    print(f"| {100 * c:.1f}% | no cell | | | | | {n} | | |")
                else:
                    cb = cell[4]
                    print(f"| {100 * c:.1f}% | {cell[0]:g} | {cell[1]:g} | {cell[2]:g} | {cell[3]} | {cb} | {n} | "
                          f"{100 * cb / n:.3f}% | {100 * evalstats.poisson_upper95(cb) / n:.3f}% |")
        try:
            al, ta, de, f, b = fg.select_weighted(sets, a.weight, names=tuple(a.tuning), cap=a.cap, cap_ub=a.cap_ub)
        except ValueError as e:     # no cell passes the cap
            sys.exit(f"judge: {e}")
        crit = (f', broken / rows <= {a.cap:g}' if a.cap is not None else
                f', 95% upper bound of broken (Poisson) / n <= {a.cap_ub:g}' if a.cap_ub is not None else '')
        print(f"\n{a.select}: selected on {'+'.join(a.tuning)} ({n} rows) by fixed - {a.weight:g} x broken"
              f"{crit}: alpha {al}, tau {ta}, delta {de}; fixed {f}, broken {b}")
        if a.cap_ub is not None:
            nj = fg.n_jright([r for t in a.tuning for r in sets[t]])
            print(f"  n {n}, n_Jright {nj}, broken {b}, point rate {100 * b / n:.3f}%, 95% upper bound {100 * evalstats.poisson_upper95(b) / n:.3f}%")
        configs.append((a.select, (al, ta, de)))
    for label, params in configs:
        print(f"\n{label}: alpha {params[0]:g}, tau {params[1]:g}, delta {params[2]:g}")
        print("\n".join(table(label, a.setting, sets, params, a.out, not a.no_net)))
        if a.jsubsets:
            print("\n".join(jsubset_lines(label, a.setting, sets, params)))
    if a.diagnose:
        print(f"\ndiagnose ({a.setting}):")
        print("\n".join(diagnose_lines(a.setting, sets)))
    return configs


if __name__ == "__main__":
    main()
