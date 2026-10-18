"""P1 readout (README registration): 0c (score_0c.py files) against M (score.py files) on the chat sets. Numbers only.

  python3 experiments/p1-0c/p1ana.py SCORE_DIR      # SCORE_DIR/M.<set>.jsonl, SCORE_DIR/X0C.<set>.jsonl
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path[:0] = [os.path.join(ROOT, "experiments", "s7a-tinylm"), os.path.join(ROOT, "tools"), os.path.join(ROOT, "reference", "proto")]
import fusegrid as fg  # noqa: E402
import evalstats as ev  # noqa: E402

TUNE, DEV, CAP, B_SET = "chatclause", ["discordtune", "chatjudge", "lytune"], 0.005, "discordtune"
SHIPPED = (0.3, 2, 0.25)      # M's shipped chat values (alpha, tau, delta)
CJ2 = 5783                    # chatjudge-2 rows, for the MDE scaling


def right(rows, cell, col):
    return [bool(r["ok"][fg.pick(r, cell[0], cell[1], col, cell[2])]) for r in rows]


def fb(new, base):
    """(fixed, broken) of `new` against `base`, row by row."""
    return sum(a and not b for a, b in zip(new, base)), sum(b and not a for a, b in zip(new, base))


def alone(rows, col):
    fix = fixable = keep = keepable = 0
    for r in rows:
        if r["n"] < 2 or r.get("skip"):
            continue
        j = max(range(r["n"]), key=lambda i: (r[col][i], -i))
        if r["ok"][0]:
            keepable += 1; keep += j == 0
        elif any(r["ok"]):
            fixable += 1; fix += bool(r["ok"][j])
    return f"alone-fix {fix}/{fixable} ({fix / max(fixable, 1):.1%}), retain {keep}/{keepable} ({keep / max(keepable, 1):.1%})"


def main(d):
    sets = [TUNE] + DEV
    m = {s: fg.load_scores(os.path.join(d, f"M.{s}.jsonl")) for s in sets}
    x = {s: fg.load_scores(os.path.join(d, f"X0C.{s}.jsonl")) for s in sets}
    for s in sets:
        if [tuple(r["ok"]) for r in m[s]] != [tuple(r["ok"]) for r in x[s]]:
            sys.exit(f"STOP: {s} ok columns differ between M and X0C")
    arms = {"0c ll": (x, "ll"), "0c ll0": (x, "ll0"), "M same rule": (m, "ll")}
    cells = {}
    print(f"## tuning: {TUNE} (n = {len(m[TUNE])}), cap_ub {CAP}\n")
    for name, (src, col) in arms.items():
        try:
            cell = fg.select_weighted({TUNE: src[TUNE]}, 1, col=col, names=(TUNE,), cap_ub=CAP)
        except ValueError:   # no cell within the cap: keep the n-gram first everywhere (tau 0 never reranks)
            print(f"- {name}: no cell within the cap; keeps the n-gram first")
            cell = (0.0, 0, 0, 0, 0)
        cells[name] = cell
        n = len(src[TUNE])
        skip = sum(1 for r in src[TUNE] if r.get("skip"))
        print(f"- {name}: alpha {cell[0]}, tau {cell[1]}, delta {cell[2]}; +{cell[3]} / -{cell[4]}, net {cell[3] - cell[4]:+d}, "
              f"UB / n {ev.poisson_upper95(cell[4]) / n:.2%}; {alone(src[TUNE], col)}; skip rows {skip}")
    ng_t = [bool(r["ok"][0]) for r in m[TUNE]]
    f, b = fb(right(m[TUNE], SHIPPED, "ll"), ng_t)
    print(f"- M shipped {SHIPPED}: +{f} / -{b}, net {f - b:+d}, UB / n {ev.poisson_upper95(b) / len(ng_t):.2%}")
    print("\n### 0c ll frontier on the tuning set (UB cap -> cell)\n")
    for cap, cell in fg.frontier({TUNE: x[TUNE]}, 1, col="ll", names=(TUNE,), ub=True):
        print(f"- {cap:.1%}: " + ("none" if cell is None else f"{cell[:3]} +{cell[3]} / -{cell[4]} net {cell[3] - cell[4]:+d}"))
    verdict = None
    for s in DEV:
        n = len(m[s])
        ng = [bool(r["ok"][0]) for r in m[s]]
        shipped = right(m[s], SHIPPED, "ll")
        print(f"\n## {s} (n = {n})\n")
        print("| picks | vs n-gram +/- | net | UB / n | error ratio | vs M shipped: new right / old right | net | McNemar p |")
        print("|---|---|---|---|---|---|---|---|")
        f, b = fb(shipped, ng)
        print(f"| M shipped | +{f}/-{b} | {f - b:+d} | {ev.poisson_upper95(b) / n:.2%} | {b / max(f, 1):.3f} | | | |")
        picks = {}
        for name, (src, col) in arms.items():
            picks[name] = p = right(src[s], cells[name], col)
            f, b = fb(p, ng)
            c1, c2 = fb(p, shipped)
            print(f"| {name} | +{f}/-{b} | {f - b:+d} | {ev.poisson_upper95(b) / n:.2%} | {b / max(f, 1):.3f} | {c1} / {c2} | "
                  f"{c1 - c2:+d} | {ev.mcnemar_exact(c1, c2):.3g} |")
            if s == B_SET and name == "0c ll":
                verdict = (c1 - c2 >= 40, ev.poisson_upper95(b) / n <= CAP, c1 - c2, ev.poisson_upper95(b) / n)
        c_ll, c_ll0 = cells["0c ll"], cells["0c ll0"]
        moved = sum(1 for r in x[s] if fg.pick(r, c_ll[0], c_ll[1], "ll", c_ll[2]) != fg.pick(r, c_ll0[0], c_ll0[1], "ll0", c_ll0[2]))
        only_ll, only_ll0 = fb(picks["0c ll"], picks["0c ll0"])
        print(f"\ncontext: rows whose pick differs between ll and ll0 {moved}; ll right only {only_ll}, ll0 right only {only_ll0}")
        print(f"0c ll {alone(x[s], 'll')}; M {alone(m[s], 'll')}")
        if s == "chatjudge":
            c1, c2 = fb(picks["0c ll"], shipped)
            dd = round((c1 + c2) * CJ2 / n)
            print(f"MDE on chatjudge-2 (discordant {c1 + c2} scaled to {dd}): alpha 0.05 {ev.mde(dd, 0.05)}, alpha 0.01 {ev.mde(dd, 0.01)}")
    ok_net, ok_ub, net, ub = verdict
    print(f"\n## Verdict ({B_SET}, 0c ll at the tuned cell)\n\n- net vs M shipped {net:+d} (>= +40: {'yes' if ok_net else 'no'}); "
          f"UB / n vs n-gram {ub:.2%} (<= 0.5%: {'yes' if ok_ub else 'no'})\n- registered reading: {'PASS' if ok_net and ok_ub else 'FAIL'}")


if __name__ == "__main__":
    main(sys.argv[1])
