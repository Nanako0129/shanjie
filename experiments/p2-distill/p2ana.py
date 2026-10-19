"""P2 readout (README registration): the distilled student (score.py files) against M's shipped values; 0c as a reference. Numbers only.

  python3 experiments/p2-distill/p2ana.py SCORE_DIR    # SCORE_DIR/M.<set>.jsonl, S.<set>.jsonl (score.py), X0C.<set>.jsonl (score_0c.py)
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path[:0] = [os.path.join(ROOT, "experiments", "s7a-tinylm"), os.path.join(ROOT, "tools"), os.path.join(ROOT, "reference", "proto")]
import fusegrid as fg  # noqa: E402
import evalstats as ev  # noqa: E402

TUNE, DEV, B_SET = "chatclause", ["discordtune", "chatjudge", "lytune"], "discordtune"
CAP_TUNE = 0.003              # registered: the tuning cap, tighter than the dev limit
CAP_DEV = 0.005               # registered: the dev limit for the verdict
SHIPPED = (0.3, 2, 0.25)      # M's shipped chat values (alpha, tau, delta)
CJ2 = 5783                    # chatjudge-2 rows, for the MDE scaling


def right(rows, cell, col="ll"):
    return [bool(r["ok"][fg.pick(r, cell[0], cell[1], col, cell[2])]) for r in rows]


def fb(new, base):
    """(fixed, broken) of `new` against `base`, row by row."""
    return sum(a and not b for a, b in zip(new, base)), sum(b and not a for a, b in zip(new, base))


def alone(rows, col="ll"):
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


def select(rows):
    try:
        return fg.select_weighted({TUNE: rows}, 1, col="ll", names=(TUNE,), cap_ub=CAP_TUNE)
    except ValueError:   # no cell within the cap
        return None


def main(d):
    sets = [TUNE] + DEV
    arms = {name: {s: fg.load_scores(os.path.join(d, f"{f}.{s}.jsonl")) for s in sets}
            for name, f in (("student", "S"), ("0c", "X0C"), ("M same rule", "M"))}
    m = arms["M same rule"]
    for name, src in arms.items():
        for s in sets:
            if [tuple(r["ok"]) for r in src[s]] != [tuple(r["ok"]) for r in m[s]]:
                sys.exit(f"STOP: {s} ok columns differ between {name} and M")
    cells = {}
    print(f"## tuning: {TUNE} (n = {len(m[TUNE])}), cap_ub {CAP_TUNE}\n")
    for name, src in arms.items():
        cell = select(src[TUNE])
        if cell is None:   # keep the n-gram first everywhere (tau 0 never reranks)
            print(f"- {name}: no cell within the cap; keeps the n-gram first")
            cell = (0.0, 0, 0, 0, 0)
        cells[name] = cell
        n = len(src[TUNE])
        print(f"- {name}: alpha {cell[0]}, tau {cell[1]}, delta {cell[2]}; +{cell[3]} / -{cell[4]}, net {cell[3] - cell[4]:+d}, "
              f"UB / n {ev.poisson_upper95(cell[4]) / n:.2%}; {alone(src[TUNE])}")
    ng_t = [bool(r["ok"][0]) for r in m[TUNE]]
    f, b = fb(right(m[TUNE], SHIPPED), ng_t)
    print(f"- M shipped {SHIPPED}: +{f} / -{b}, net {f - b:+d}, UB / n {ev.poisson_upper95(b) / len(ng_t):.2%}")
    print("\n### student frontier on the tuning set (UB cap -> cell)\n")
    for cap, cell in fg.frontier({TUNE: arms["student"][TUNE]}, 1, col="ll", names=(TUNE,), ub=True):
        print(f"- {cap:.1%}: " + ("none" if cell is None else f"{cell[:3]} +{cell[3]} / -{cell[4]} net {cell[3] - cell[4]:+d}"))
    verdict = mde = None
    for s in DEV:
        n = len(m[s])
        ng = [bool(r["ok"][0]) for r in m[s]]
        shipped = right(m[s], SHIPPED)
        print(f"\n## {s} (n = {n})\n")
        print("| picks | vs n-gram +/- | net | UB / n | error ratio | vs M shipped: new right / old right | net | McNemar p |")
        print("|---|---|---|---|---|---|---|---|")
        f, b = fb(shipped, ng)
        print(f"| M shipped | +{f}/-{b} | {f - b:+d} | {ev.poisson_upper95(b) / n:.2%} | {b / max(f, 1):.3f} | | | |")
        for name, src in arms.items():
            p = right(src[s], cells[name])
            f, b = fb(p, ng)
            c1, c2 = fb(p, shipped)
            print(f"| {name} | +{f}/-{b} | {f - b:+d} | {ev.poisson_upper95(b) / n:.2%} | {b / max(f, 1):.3f} | {c1} / {c2} | "
                  f"{c1 - c2:+d} | {ev.mcnemar_exact(c1, c2):.3g} |")
            if name == "student" and s == B_SET:
                verdict = (c1 - c2, ev.poisson_upper95(b) / n)
            if name == "student" and s == "chatjudge":
                dd = round((c1 + c2) * CJ2 / n)
                mde = (dd, ev.mde(dd, 0.05), ev.mde(dd, 0.01), c1 + c2)
        print(f"\nstudent {alone(arms['student'][s])}; 0c {alone(arms['0c'][s])}; M {alone(m[s])}")
    print(f"\nMDE on chatjudge-2 (student vs M shipped, discordant {mde[3]} scaled to {mde[0]}): alpha 0.05 {mde[1]}, alpha 0.01 {mde[2]}")
    net, ub = verdict
    ok = net >= 40 and ub <= CAP_DEV
    print(f"\n## Verdict ({B_SET}, student at the tuned cell)\n\n- net vs M shipped {net:+d} (>= +40: {'yes' if net >= 40 else 'no'}); "
          f"UB / n vs n-gram {ub:.2%} (<= {CAP_DEV:.1%}: {'yes' if ub <= CAP_DEV else 'no'})\n- registered reading: {'PASS' if ok else 'FAIL'}")


if __name__ == "__main__":
    main(sys.argv[1])
