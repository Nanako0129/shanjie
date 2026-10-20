#!/usr/bin/env python3
"""P4c analysis (README of this directory): offline grid over mu x alpha x delta of the colloquial table + the small-model fusion, against the
shipping final pick. Prints and writes counts only: never a sentence, context, word or character of a row.

  python3 p4c.py --select --base BASE.rowstats --mu-scores 0.1=S.jsonl ... --mu-rowstats 0.1=R.txt ... --out SELECT.json
  python3 p4c.py --cell-from SELECT.json | --cell MU,ALPHA,DELTA  --base BASE.rowstats --mu-scores MU=S.jsonl [--mu-rowstats MU=R.txt]
      --clause {1,2a,2b,3,report} [--out-rowstats CELL.rowstats] [--out OUT.json]

BASE is the shipping rowstats (no --colloquial); a --mu-scores file is the CLI's --tinylm-scores of the --colloquial T2 --colloquial-mu MU run
(rows i, n, ng, ok, ed, glen, c, ll); a --mu-rowstats file is the same run's --rowstats. The 4-column rowstats written by --out-rowstats is read
by tools/evalstats.py (`compare BASE CELL --ci 0.90` is clause 2b).
Readings of the README that were ambiguous:
  - "error ratio" = breaks / fixes (docs/methodology.md 2026-10-18 #4; tools/kpi.py error_ratio), printed "none" when fixes is 0 and breaks > 0.
  - "discordant rows" = fixes + breaks (rows whose final ok differs from the base).
  - Ties in the choice: larger net, then smaller mu, then |alpha - 0.3| smaller, then delta 0.25 before 0.
  - Anchor, in every mode, from the scores file itself: the offline pick at alpha 0.3, delta 0.25 must equal the CLI's chosen index `c` on every
    row with an `ll` (as tools/triage.py run_cell does); with --mu-rowstats the final ok must also equal the rowstats ok. Base gold length must
    equal the scores' glen row by row. Rows without `ll` keep index 0 offline (fusegrid / d1) and are not compared on `c`.
  - Clause 2a checks correctness flips (ok), not identical output strings.
  - The pick is tools/triage.py `pick` (core/src/tinylm.rs line for line; no numpy).
  - Guards: --select needs n == N_SELECT (chatclause), --clause 1 needs n == N_DISCORD (discordtune).
Exit codes: 0 ok, 1 STOP, 3 NO CELL QUALIFIES.
"""
import argparse, json, math, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import evalstats  # noqa: E402
import triage  # noqa: E402

TAU = 2.0
MUS, ALPHAS, DELTAS = (0.1, 0.2, 0.3, 0.5), (0.3, 0.5, 0.7), (0.0, 0.25)
GRID = [(m, a, d) for m in MUS for a in ALPHAS for d in DELTAS]
ANCHOR = (0.3, 0.25)
N_SELECT, N_DISCORD = 5761, 4958   # chatclause, discordtune row counts
CAP_SELECT = 30   # 26 * 5761 / 4958, README "選格"
CAP_DISCORD = 26
RATIO = 0.5


def final_pick(r, alpha, delta):
    """tools/triage.py pick; rows without `ll` keep index 0, as fusegrid / d1 do."""
    return triage.pick(r["ng"], r["ll"], alpha, TAU, delta) if "ll" in r else 0


def load_scores(path):
    with open(path, encoding="utf-8") as f:
        rows = [json.loads(l) for l in f if l.strip()]
    if [r["i"] for r in rows] != list(range(1, len(rows) + 1)):
        raise ValueError(f"{path}: rows are not 1..n in order")
    return rows


def stats(base_ok, new_ok, s_first=None):
    """Counts of the new final ok against the base: fixes, breaks, net, exact McNemar p, error ratio (breaks / fixes), discordant.
    With `s_first` (the scores' ok[0] per row) also how many fixes / breaks have the s-first right / wrong."""
    f = sum(1 for b, c in zip(base_ok, new_ok) if c and not b)
    k = sum(1 for b, c in zip(base_ok, new_ok) if b and not c)
    out = {"n": len(base_ok), "fixes": f, "breaks": k, "net": f - k, "p": evalstats.mcnemar_exact(f, k),
           "error_ratio": k / f if f else (math.inf if k else 0.0), "discordant": f + k}
    if s_first is not None:
        for name, test in (("fixes", lambda b, c: c and not b), ("breaks", lambda b, c: b and not c)):
            hit = [bool(sf) for b, c, sf in zip(base_ok, new_ok, s_first) if test(b, c)]
            out[f"{name}_sfirst_right"], out[f"{name}_sfirst_wrong"] = sum(hit), len(hit) - sum(hit)
    return out


def ratio_ok(s, limit=RATIO):
    """breaks / fixes <= limit; fixes 0 gives an infinite ratio unless breaks is 0."""
    return s["breaks"] == 0 or (s["fixes"] > 0 and s["breaks"] <= limit * s["fixes"])


def clause_verdict(clause, s):
    if clause == "1":
        return (s["p"] >= 0.05 or s["net"] >= 0) and (s["breaks"] <= CAP_DISCORD or ratio_ok(s))
    if clause == "2a":
        return s["breaks"] <= 2 and s["net"] >= 0   # (0 breaks, 0 fixes satisfies it)
    if clause == "3":
        return s["net"] > 0 and ratio_ok(s)
    return None   # report, and 2b (needs the bootstrap)


def old_rules(s):
    return {"old_clause1": (s["p"] >= 0.05 or s["net"] >= 0) and s["breaks"] <= 26, "old_clause3": s["net"] > 0 and s["breaks"] <= 2}


def qualifies(s):
    return (s["p"] >= 0.05 or s["net"] >= 0) and (s["breaks"] <= CAP_SELECT or ratio_ok(s))


def cell_ok(rows, alpha, delta):
    return [r["ok"][final_pick(r, alpha, delta)] for r in rows]


def check_anchor(mu, rows, base, rs_ok=None):
    """STOP (exit 1, row numbers only) unless the offline pick at ANCHOR equals the CLI's `c`, the gold lengths match the base and, with
    rowstats, the final ok equals theirs."""
    if len(rows) != len(base) or (rs_ok is not None and len(rs_ok) != len(rows)):
        print(f"STOP: mu {mu} row counts differ: scores {len(rows)}, base {len(base)}" + (f", rowstats {len(rs_ok)}" if rs_ok is not None else ""))
        raise SystemExit(1)
    checks = {"pick": [i for i, r in enumerate(rows, 1) if "ll" in r and r["c"] != final_pick(r, *ANCHOR)],
              "glen": [i for i, (r, b) in enumerate(zip(rows, base), 1) if r["glen"] != b[2]]}
    if rs_ok is not None:
        checks["rowstats_ok"] = [i for i, (r, o) in enumerate(zip(rows, rs_ok), 1) if r["ok"][final_pick(r, *ANCHOR)] != o]
    for what, bad in checks.items():
        if bad:
            print(f"STOP: anchor mismatch ({what}) at mu {mu}, {len(bad)} rows, first rows {bad[:10]}")
            raise SystemExit(1)


def choose(cells):
    """cells: list of (mu, alpha, delta, stats). Max net among qualifying; ties smaller mu, |alpha-0.3| smaller, delta 0.25 first."""
    q = [c for c in cells if qualifies(c[3])]
    return min(q, key=lambda c: (-c[3]["net"], c[0], abs(c[1] - 0.3), -c[2])) if q else None


def kv(s):
    r = s["error_ratio"]
    t = (f"n {s['n']} fixes {s['fixes']} breaks {s['breaks']} net {s['net']} p {s['p']:.4f} "
         f"error_ratio {'none' if r == math.inf else f'{r:.4f}'} discordant {s['discordant']}")
    if "fixes_sfirst_right" in s:
        t += (f" fixes_sfirst_right {s['fixes_sfirst_right']} fixes_sfirst_wrong {s['fixes_sfirst_wrong']}"
              f" breaks_sfirst_right {s['breaks_sfirst_right']} breaks_sfirst_wrong {s['breaks_sfirst_wrong']}")
    return t


def jsonable(s):
    return {k: ("inf" if v == math.inf else v) for k, v in s.items()}


def read_base(path):
    return evalstats.read_rowstats(path)   # (ok, errors, gold_len) per row


def parse_map(items, what):
    out = {}
    for it in items or []:
        k, _, v = it.partition("=")
        try:
            mu = float(k)
        except ValueError:
            raise SystemExit(f"--{what} wants MU=FILE, got a bad item")
        if mu in out:
            raise SystemExit(f"--{what}: mu {mu} given twice")
        out[mu] = v
    return out


def parse_cell(text):
    try:
        mu, a, d = (float(x) for x in text.split(","))
    except ValueError:
        raise SystemExit("--cell wants MU,ALPHA,DELTA")
    if (mu, a, d) not in GRID:
        raise SystemExit("off-grid cell refused")
    return mu, a, d


def select(base, mu_scores, mu_rs, out):
    if len(base) != N_SELECT:
        raise SystemExit(f"--select is the chatclause step: n must be {N_SELECT}, base has {len(base)}")
    base_ok = [b[0] for b in base]
    cells = []
    for mu in MUS:
        if mu not in mu_scores or mu not in mu_rs:
            raise SystemExit(f"--select needs --mu-scores and --mu-rowstats for mu {mu}")
        rows = load_scores(mu_scores[mu])
        check_anchor(mu, rows, base, [r[0] for r in evalstats.read_rowstats(mu_rs[mu])])
        sf = [r["ok"][0] for r in rows]
        for m, a, d in GRID:
            if m == mu:
                cells.append((m, a, d, stats(base_ok, cell_ok(rows, a, d), sf)))
    for m, a, d, s in cells:
        print(f"mu {m} alpha {a} delta {d} qualifies {int(qualifies(s))} {kv(s)}")
    best = choose(cells)
    print(f"{best[0]},{best[1]},{best[2]}" if best else "NO CELL QUALIFIES")
    res = {"chosen": best and {"mu": best[0], "alpha": best[1], "delta": best[2], "stats": jsonable(best[3])},
           "no_cell_qualifies": best is None,
           "cells": [{"mu": m, "alpha": a, "delta": d, "qualifies": qualifies(s), **jsonable(s)} for m, a, d, s in cells]}
    if out:
        with open(out, "w", encoding="utf-8") as f:
            json.dump(res, f, indent=1)
    return res


def cell(base, rows, mu, alpha, delta, clause, out_rowstats, out, rs_ok=None):
    if clause == "1" and len(base) != N_DISCORD:
        raise SystemExit(f"--clause 1 is the discordtune clause: n must be {N_DISCORD}, base has {len(base)}")
    check_anchor(mu, rows, base, rs_ok)
    picks = [final_pick(r, alpha, delta) for r in rows]
    new_ok = [r["ok"][j] for r, j in zip(rows, picks)]
    s = stats([b[0] for b in base], new_ok, [r["ok"][0] for r in rows])
    print(f"cell {mu},{alpha},{delta} {kv(s)}")
    res = {"cell": [mu, alpha, delta], "clause": clause, **jsonable(s)}
    if clause == "2b":
        cand = [(o, r["ed"][j], r["glen"]) for r, j, o in zip(rows, picks, new_ok)]
        (lo, hi), _ = evalstats.paired_bootstrap(base, cand, level=0.90)
        pp = 100 * lo / len(base)
        res["verdict"] = pp >= -1.0
        res["bootstrap90_lower_rows"], res["bootstrap90_lower_pp"] = lo, pp
        print(f"clause 2b {'PASS' if res['verdict'] else 'FAIL'} lower90_rows {lo} lower90_pp {pp:.3f}")
    elif clause != "report":
        res["verdict"] = clause_verdict(clause, s)
        print(f"clause {clause} {'PASS' if res['verdict'] else 'FAIL'}" + (" (correctness flips, not identical strings)" if clause == "2a" else ""))
    old = old_rules(s)
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
    ap.add_argument("--clause", choices=("1", "2a", "2b", "3", "report"), default="report")
    ap.add_argument("--out-rowstats")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    if a.select and (a.cell or a.cell_from):
        raise SystemExit("--select cannot be combined with --cell / --cell-from")
    base = read_base(a.base)
    ms, mr = parse_map(a.mu_scores, "mu-scores"), parse_map(a.mu_rowstats, "mu-rowstats")
    if a.select:
        res = select(base, ms, mr, a.out)
        return 3 if res["no_cell_qualifies"] else 0
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
    cell(base, load_scores(ms[mu]), mu, al, de, a.clause, a.out_rowstats, a.out, rs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
