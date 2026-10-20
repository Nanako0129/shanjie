#!/usr/bin/env python3
"""P4c analysis (README of this directory): offline grid over mu x alpha x delta of the colloquial table + the small-model fusion, against the
shipping final pick. Prints and writes counts only: never a sentence, context, word or character of a row.

  python3 p4c.py --select --base BASE.rowstats --mu-scores 0.1=S.jsonl ... --mu-rowstats 0.1=R.txt ... --out SELECT.json
  python3 p4c.py --cell-from SELECT.json | --cell MU,ALPHA,DELTA  --base BASE.rowstats --mu-scores MU=S.jsonl [--mu-rowstats MU=R.txt]
      --clause {1,2a,3,report} [--out-rowstats CELL.rowstats] [--out OUT.json]

BASE is the shipping rowstats (no --colloquial); a --mu-scores file is the CLI's --tinylm-scores of the --colloquial T2 --colloquial-mu MU run
(rows i, n, ng, ok, ed, glen, c, ll); a --mu-rowstats file is the same run's --rowstats. The 4-column rowstats written by --out-rowstats is read
by tools/evalstats.py (`compare BASE CELL --ci 0.90` is clause 2b).
Readings of the README that were ambiguous:
  - "error ratio" = wrong rows of the cell / wrong rows of the base (n - ok count), n the set size.
  - "discordant rows" = fixes + breaks (rows whose final ok differs from the base).
  - Ties in the choice: larger net, then smaller mu, then |alpha - 0.3| smaller, then delta 0.25 before 0.
  - The anchor (alpha 0.3, delta 0.25 equals the CLI's rowstats ok on every row) runs in --select always, in --cell when --mu-rowstats is given.
  - fusegrid.pick is copied (3 lines) because importing fusegrid pulls numpy; test_p4c checks the copy against it when numpy is available.
"""
import argparse, json, math, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import evalstats  # noqa: E402

TAU = 2.0
MUS, ALPHAS, DELTAS = (0.1, 0.2, 0.3, 0.5), (0.3, 0.5, 0.7), (0.0, 0.25)
GRID = [(m, a, d) for m in MUS for a in ALPHAS for d in DELTAS]
ANCHOR = (0.3, 0.25)
CAP_SELECT = 30   # 26 * 5761 / 4958, README "選格"
CAP_DISCORD = 26
RATIO = 0.5


def pick(r, alpha, tau, col="ll", delta=0):
    """Copy of experiments/s7a-tinylm/fusegrid.py pick."""
    if r["n"] < 2 or r["ng"][0] - r["ng"][1] >= tau:
        return 0
    s = [g + alpha * l for g, l in zip(r["ng"], r[col])]
    j = max(range(len(s)), key=lambda i: (s[i], -i))
    return j if s[j] - s[0] >= delta else 0


def final_pick(r, alpha, delta):
    """Rows without `ll` keep index 0, as fusegrid / d1 do."""
    return pick(r, alpha, TAU, "ll", delta) if "ll" in r else 0


def load_scores(path):
    with open(path, encoding="utf-8") as f:
        rows = [json.loads(l) for l in f if l.strip()]
    if [r["i"] for r in rows] != list(range(1, len(rows) + 1)):
        raise ValueError(f"{path}: rows are not 1..n in order")
    return rows


def stats(base_ok, new_ok):
    """Counts of the new final ok against the base: fixes, breaks, net, exact McNemar p, error ratio, discordant."""
    f = sum(1 for b, c in zip(base_ok, new_ok) if c and not b)
    k = sum(1 for b, c in zip(base_ok, new_ok) if b and not c)
    n = len(base_ok)
    be, ce = n - sum(base_ok), n - sum(new_ok)
    return {"n": n, "fixes": f, "breaks": k, "net": f - k, "p": evalstats.mcnemar_exact(f, k),
            "error_ratio": ce / be if be else (math.inf if ce else 1.0), "discordant": f + k}


def ratio_ok(s, limit=RATIO):
    """breaks / fixes <= limit; fixes 0 gives an infinite ratio unless breaks is 0."""
    return s["breaks"] == 0 or (s["fixes"] > 0 and s["breaks"] <= limit * s["fixes"])


def clause_verdict(clause, s):
    if clause == "1":
        return (s["p"] >= 0.05 or s["net"] >= 0) and (s["breaks"] <= CAP_DISCORD or ratio_ok(s))
    if clause == "2a":
        return (s["fixes"] == 0 and s["breaks"] == 0) or (s["breaks"] <= 2 and s["net"] >= 0)
    if clause == "3":
        return s["net"] > 0 and ratio_ok(s)
    return None   # report


def qualifies(s):
    return (s["p"] >= 0.05 or s["net"] >= 0) and (s["breaks"] <= CAP_SELECT or ratio_ok(s))


def cell_ok(rows, alpha, delta):
    return [r["ok"][final_pick(r, alpha, delta)] for r in rows]


def check_anchor(mu, rows, rs_ok):
    if len(rows) != len(rs_ok):
        raise SystemExit(f"STOP: mu {mu} scores have {len(rows)} rows, rowstats {len(rs_ok)}")
    bad = [i + 1 for i, (o, c) in enumerate(zip(cell_ok(rows, *ANCHOR), rs_ok)) if o != c]
    if bad:
        print(f"STOP: anchor mismatch at mu {mu}, {len(bad)} rows, first rows {bad[:10]}")
        raise SystemExit(1)


def choose(cells):
    """cells: list of (mu, alpha, delta, stats). Max net among qualifying; ties smaller mu, |alpha-0.3| smaller, delta 0.25 first."""
    q = [c for c in cells if qualifies(c[3])]
    return min(q, key=lambda c: (-c[3]["net"], c[0], abs(c[1] - 0.3), -c[2])) if q else None


def kv(s):
    r = s["error_ratio"]
    return (f"n {s['n']} fixes {s['fixes']} breaks {s['breaks']} net {s['net']} p {s['p']:.4f} "
            f"error_ratio {'inf' if r == math.inf else f'{r:.4f}'} discordant {s['discordant']}")


def jsonable(s):
    return {k: ("inf" if v == math.inf else v) for k, v in s.items()}


def read_base(path):
    return [r[0] for r in evalstats.read_rowstats(path)]


def parse_map(items, what):
    out = {}
    for it in items or []:
        k, _, v = it.partition("=")
        try:
            out[float(k)] = v
        except ValueError:
            raise SystemExit(f"--{what} wants MU=FILE, got a bad item")
    return out


def parse_cell(text):
    try:
        mu, a, d = (float(x) for x in text.split(","))
    except ValueError:
        raise SystemExit("--cell wants MU,ALPHA,DELTA")
    if (mu, a, d) not in GRID:
        raise SystemExit("off-grid cell refused")
    return mu, a, d


def select(base_ok, mu_scores, mu_rs, out):
    cells = []
    for mu in MUS:
        if mu not in mu_scores or mu not in mu_rs:
            raise SystemExit(f"--select needs --mu-scores and --mu-rowstats for mu {mu}")
        rows = load_scores(mu_scores[mu])
        if len(rows) != len(base_ok):
            raise SystemExit(f"STOP: mu {mu} scores have {len(rows)} rows, base {len(base_ok)}")
        check_anchor(mu, rows, [r[0] for r in evalstats.read_rowstats(mu_rs[mu])])
        for m, a, d in GRID:
            if m == mu:
                cells.append((m, a, d, stats(base_ok, cell_ok(rows, a, d))))
    for m, a, d, s in cells:
        print(f"mu {m} alpha {a} delta {d} qualifies {int(qualifies(s))} {kv(s)}")
    best = choose(cells)
    if best:
        print(f"{best[0]},{best[1]},{best[2]}")
    else:
        print("NO CELL QUALIFIES")
    res = {"chosen": best and {"mu": best[0], "alpha": best[1], "delta": best[2], "stats": jsonable(best[3])},
           "no_cell_qualifies": best is None,
           "cells": [{"mu": m, "alpha": a, "delta": d, "qualifies": qualifies(s), **jsonable(s)} for m, a, d, s in cells]}
    if out:
        with open(out, "w", encoding="utf-8") as f:
            json.dump(res, f, indent=1)
    return res


def cell(base_ok, rows, mu, alpha, delta, clause, out_rowstats, out, rs_ok=None):
    if len(rows) != len(base_ok):
        raise SystemExit(f"STOP: scores have {len(rows)} rows, base {len(base_ok)}")
    if rs_ok is not None:
        check_anchor(mu, rows, rs_ok)
    picks = [final_pick(r, alpha, delta) for r in rows]
    s = stats(base_ok, [r["ok"][j] for r, j in zip(rows, picks)])
    print(f"cell {mu},{alpha},{delta} {kv(s)}")
    res = {"cell": [mu, alpha, delta], "clause": clause, **jsonable(s)}
    if clause != "report":
        res["verdict"] = clause_verdict(clause, s)
        print(f"clause {clause} {'PASS' if res['verdict'] else 'FAIL'}")
    old = {"clause1_breaks_le_26": s["breaks"] <= 26, "typo_breaks_le_2": s["breaks"] <= 2}
    res["old_rule"] = old
    print("old_rule " + " ".join(f"{k} {int(v)}" for k, v in old.items()))
    if out_rowstats:
        with open(out_rowstats, "w", encoding="utf-8") as f:
            for i, (r, j) in enumerate(zip(rows, picks), 1):
                f.write(f"{i}\t{r['ok'][j]}\t{r['ed'][j]}\t{r['glen']}\n")
    if out:
        with open(out, "w", encoding="utf-8") as f:
            json.dump(res, f, indent=1)
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", required=True)
    ap.add_argument("--mu-scores", action="append", default=[])
    ap.add_argument("--mu-rowstats", action="append", default=[])
    ap.add_argument("--select", action="store_true")
    ap.add_argument("--cell")
    ap.add_argument("--cell-from")
    ap.add_argument("--clause", choices=("1", "2a", "3", "report"), default="report")
    ap.add_argument("--out-rowstats")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    base_ok = read_base(a.base)
    ms, mr = parse_map(a.mu_scores, "mu-scores"), parse_map(a.mu_rowstats, "mu-rowstats")
    if a.select:
        select(base_ok, ms, mr, a.out)
        return 0
    if a.cell_from:
        with open(a.cell_from, encoding="utf-8") as f:
            c = json.load(f)["chosen"]
        if not c:
            raise SystemExit("--cell-from: the selection chose no cell")
        mu, al, de = parse_cell(f"{c['mu']},{c['alpha']},{c['delta']}")
    elif a.cell:
        mu, al, de = parse_cell(a.cell)
    else:
        raise SystemExit("give --select, --cell or --cell-from")
    if mu not in ms:
        raise SystemExit(f"--mu-scores for mu {mu} missing")
    rs = [r[0] for r in evalstats.read_rowstats(mr[mu])] if mu in mr else None
    cell(base_ok, load_scores(ms[mu]), mu, al, de, a.clause, a.out_rowstats, a.out, rs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
