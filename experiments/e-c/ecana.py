"""E-C analysis (experiments/e-c/README.md): per decoding cell, pick α/τ/δ with the upper-bound rule on the tuning layer,
then compare cells by the rows right in the end; the best cell goes to the development layer. Prints numbers only.

  python3 ecana.py REPO SCORE_DIR"""
import os, sys

REPO, D = sys.argv[1], sys.argv[2]
sys.path[:0] = [os.path.join(REPO, "experiments", "s7a-tinylm"), os.path.join(REPO, "tools"), os.path.join(REPO, "reference", "proto")]
import fusegrid as fg, evalstats as ev  # noqa: E402

CAP = 0.005
PROF = {
    "chat": dict(cells=[f"l{l}_b{b}" for l in (0.3, 0.4, 0.5, 0.6, 0.7) for b in (0.5, 0.75, 1.0)], ship_cell="l0.5_b1.0",
                 tune=["chatclause"], dev=["discordtune", "lytune", "chatjudge"], ship=(0.3, 2.0, 0.25)),
    "formal": dict(cells=[f"l{l}_f{f}" for l in (0.5, 0.6, 0.7, 0.8, 0.9) for f in (0.3, 0.5, 0.7)], ship_cell="l0.7_f0.5",
                   tune=["cvtune-clean", "wikitune-clean"], dev=["wikijudge", "wikijudge2", "wikijudge3"], ship=(0.7, 2.0, 1.0)),
}


def right(rows, cell):
    a, t, de = cell[:3]
    return [bool(r["ok"][fg.pick(r, a, t, "ll", de)]) for r in rows]


def fb(x, y):
    return sum(a and not b for a, b in zip(x, y)), sum(b and not a for a, b in zip(x, y))


for prof, p in PROF.items():
    sets = p["tune"] + p["dev"]
    load = lambda c: {s: fg.load_scores(os.path.join(D, f"{c}.{prof}.{s}.jsonl")) for s in sets}
    base = load(p["ship_cell"])
    bcell = fg.select_weighted(base, 1, names=tuple(p["tune"]), cap_ub=CAP)
    b_same = {s: right(base[s], bcell) for s in sets}
    b_ship = {s: right(base[s], p["ship"]) for s in sets}
    n = sum(len(base[s]) for s in p["tune"])
    print(f"\n######## {prof}: tuning rows {n}; shipped cell {p['ship_cell']}, same-rule α/τ/δ {bcell[:3]}")
    res = []
    for c in p["cells"]:
        data = base if c == p["ship_cell"] else load(c)
        cell = fg.select_weighted(data, 1, names=tuple(p["tune"]), cap_ub=CAP)
        rt = {s: right(data[s], cell) for s in sets}
        tune_right = sum(sum(rt[s]) for s in p["tune"])
        ng_right = sum(sum(bool(r["ok"][0]) for r in data[s]) for s in p["tune"])
        f, b = (sum(x) for x in zip(*(fb(rt[s], b_same[s]) for s in p["tune"])))
        res.append((tune_right, c, cell, rt))
        print(f"  {c}: α/τ/δ {cell[:3]} rerank +{cell[3]}/-{cell[4]}; tuning right {tune_right} (n-gram first {ng_right});"
              f" vs shipped cell same-rule +{f}/-{b} net {f - b:+d} ub {ev.poisson_upper95(b) / n:.2%}")
    best = max(res, key=lambda x: (x[0], x[1] == p["ship_cell"]))
    print(f"  -> best {best[1]} (tuning right {best[0]}; shipped cell same-rule {sum(sum(b_same[s]) for s in p['tune'])})")
    for s in p["dev"]:
        rt = best[3][s]
        f1, b1 = fb(rt, b_same[s]); f2, b2 = fb(rt, b_ship[s])
        print(f"  dev {s} n {len(rt)}: best vs same-rule +{f1}/-{b1} p {ev.mcnemar_exact(f1, b1):.2g} | vs shipped values +{f2}/-{b2} p {ev.mcnemar_exact(f2, b2):.2g}"
              f" | right {sum(rt)} / same-rule {sum(b_same[s])} / shipped {sum(b_ship[s])}")
