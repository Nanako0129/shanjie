"""X0C-6 readout (README registration): 0c fused with the n-gram score against v0.6.0 shipping and the n-gram first. Numbers only.

  python3 experiments/x0c6/x0c6ana.py DIR [EMIT_DIR]
  DIR/X0C.<set>.jsonl   score_0c.py records per set (prep.py split)
  DIR/ship.<set>.r      the shipping CLI's rowstats       DIR/ng.<set>.r   the same rows without --tinylm (the n-gram first)
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path[:0] = [os.path.join(ROOT, "experiments", "s7a-tinylm"), os.path.join(ROOT, "tools"), os.path.join(ROOT, "reference", "proto")]
import fusegrid as fg  # noqa: E402
import evalstats as ev  # noqa: E402

TUNE, JUDGE, PUBLIC = "chatclause", "discordtune", ["dev302", "typing76", "user-reported"]
CAP_UB = 0.004      # P1's lesson: a cell that holds 0.5% on the tuning set came out 0.61% on the judge set, so the cap leaves a margin
WEIGHT = 1          # fixed - 1 x broken, as P1


def fb(new, base):
    """(fixed, broken) of `new` against `base`, row by row."""
    return sum(a and not b for a, b in zip(new, base)), sum(b and not a for a, b in zip(new, base))


def rowstats_ok(path):
    return [bool(ok) for ok, _e, _n in ev.read_rowstats(path)]


def alone(rows):
    """0c's own first choice among the candidates it can read: fixes / fixable rows and keeps / keepable rows."""
    fix = fixable = keep = keepable = 0
    for r in rows:
        if r["n"] < 2 or r["skip"]:
            continue
        j = max(range(r["n"]), key=lambda i: (r["ll"][i], -i))
        if r["ok"][0]:
            keepable += 1; keep += j == 0
        elif any(r["ok"]):
            fixable += 1; fix += bool(r["ok"][j])
    return f"alone-fix {fix}/{fixable} ({fix / max(fixable, 1):.1%}), retain {keep}/{keepable} ({keep / max(keepable, 1):.1%})"


def load(d, s):
    x = fg.load_scores(os.path.join(d, f"X0C.{s}.jsonl"))
    a, b = rowstats_ok(os.path.join(d, f"ship.{s}.r")), rowstats_ok(os.path.join(d, f"ng.{s}.r"))
    if not (len(x) == len(a) == len(b)) or b != [bool(r["ok"][0]) for r in x]:
        sys.exit(f"STOP: {s}: the n-gram-only rowstats and the score file's rank 1 differ, or the row counts do")
    return x, a, b


def emit_arms(emit, s, arms):
    """EMIT/arm.<set>.<arm>.txt, `row<TAB>0/1` per row: all that leaves the VM for a private set (no scores, no text)."""
    for name, v in arms.items():
        with open(os.path.join(emit, f"arm.{s}.{name}.txt"), "w") as f:
            f.writelines(f"{i}\t{int(o)}\n" for i, o in enumerate(v, 1))


def main(d, emit=None):
    x, a, b = load(d, TUNE)   # a missing or inconsistent file stops here with its own error
    try:
        cell = fg.select_weighted({TUNE: x}, WEIGHT, col="ll", names=(TUNE,), cap_ub=CAP_UB)
        sel = f"alpha {cell[0]}, tau {cell[1]}, delta {cell[2]}"
    except ValueError:   # no cell within the cap: 0c never replaces the n-gram first (tau 0 never reranks)
        cell, sel = (0.0, 0, 0, 0, 0), "no cell within the cap; keeps the n-gram first"
    n = len(x)
    out = {"cell": cell}
    if emit:
        os.makedirs(emit, exist_ok=True)
        emit_arms(emit, TUNE, {"ship": a, "ng": b, "fused": [bool(r["ok"][fg.pick(r, cell[0], cell[1], "ll", cell[2])]) for r in x]})
    f, bk = fb(a, b)
    print(f"## tuning: {TUNE} (n = {n}), cap_ub {CAP_UB:.1%}\n")
    print(f"- 0c fused: {sel}; +{cell[3]} / -{cell[4]}, net {cell[3] - cell[4]:+d}, UB / n {ev.poisson_upper95(cell[4]) / n:.2%}; {alone(x)}; "
          f"rows 0c cannot read {sum(r['skip'] for r in x)}")
    print(f"- v0.6.0 shipping: +{f} / -{bk}, net {f - bk:+d}, UB / n {ev.poisson_upper95(bk) / n:.2%} (not tuned here)")
    print("\n### frontier on the tuning set (UB / n cap -> cell)\n")
    for cap, c in fg.frontier({TUNE: x}, WEIGHT, col="ll", names=(TUNE,), ub=True):
        print(f"- {cap:.1%}: " + ("none" if c is None else f"{c[:3]} +{c[3]} / -{c[4]} net {c[3] - c[4]:+d}"))
    for s in [JUDGE] + PUBLIC:
        x, a, b = load(d, s)
        n = len(x)
        c = [bool(r["ok"][fg.pick(r, cell[0], cell[1], "ll", cell[2])]) for r in x]
        skip = sum(r["skip"] for r in x)
        if emit:
            emit_arms(emit, s, {"ship": a, "ng": b, "fused": c})
        print(f"\n## {s} (n = {n}{', judged' if s == JUDGE else ', picture only'})\n")
        print(f"top1: n-gram first {sum(b)}, shipping {sum(a)}, 0c fused {sum(c)}; rows 0c cannot read (keep the n-gram first) {skip}\n")
        print("| pair | fixed | broken | net | error ratio | UB(broken) / n | McNemar p |")
        print("|---|---|---|---|---|---|---|")
        res = {}
        for name, new, old in (("shipping vs n-gram first", a, b), ("0c fused vs n-gram first", c, b), ("0c fused vs shipping", c, a)):
            fx, br = fb(new, old)
            res[name] = (fx, br)
            print(f"| {name} | {fx} | {br} | {fx - br:+d} | {br / max(fx, 1):.3f} | {ev.poisson_upper95(br) / n:.2%} | {ev.mcnemar_exact(fx, br):.3g} |")
        out[s] = {"top1": (sum(b), sum(a), sum(c)), "skip": skip, **res}
    return out


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
