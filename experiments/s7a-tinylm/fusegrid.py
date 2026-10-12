"""S7a fusion grid, frozen selection, rowstats and the standard tables (contract sections 2.3, 3.3, 4). Numbers only, numpy not even needed.

  python fusegrid.py --scores DIR --models S M --settings chat formal \
         --sets cvtune wikitune dev302 typing76 user-reported [fw] [discordtune] --out OUT [--tune-keep FLAGDIR] [--break-weight W]

reads DIR/<model>.<setting>.<set>.jsonl (score.py output), writes OUT/rowstats/<model>.<setting>.<set>.{base,new}[.k16].txt (only [0-9\\t\\n];
with --break-weight also .shipped.txt and no .k16; the two modes write the same .base/.new names, so give each its own OUT)
and prints Markdown: per model x setting the frozen (alpha, tau), one tools/evalstats.py table row per set, and the section 3 / 4 reports.
The (alpha, tau) selection reads ONLY the tuning sets (TUNING); the other sets are scored with the frozen values. With --tune-keep (S7d)
the selection reads only the tuning rows whose flag is 0 (buildcheck.py substr --flags); the tables still use every row.
With --break-weight W (S7e, docs/contracts/s7e-fewer-breaks.md) the selection maximises fixed - W x broken against the n-gram first
over (alpha, tau, delta), each set gets a second table row against the shipped values (SHIPPED), and the decision is S7e section 3
instead of S7a section 4; the 16-character context variant is not run in that mode.
"""
import argparse
import json
import math
import os
import sys

import tinylm  # noqa: F401  (sys.path)
import evalstats  # noqa: E402  tools/evalstats.py

ALPHAS = [0.1, 0.2, 0.3, 0.5, 0.7, 1, 1.5, 2]
TAUS = [0.5, 1, 2, 3, 5, math.inf]       # inf = no threshold; listed ascending because ties go to the smaller tau
DELTAS = [0, 0.25, 0.5, 1, 1.5, 2, 3, 4]  # S7e: the fused winner replaces the n-gram first only if it beats it by at least delta
SHIPPED = (0.7, 2, 0)                     # S7e section 2.4: (alpha, tau, delta) in tinylm-v1, both settings
TUNING = ("cvtune", "wikitune")
CTX_REQUIRED = ("user-reported", "discordtune")   # sets that must show rows whose LL changes with context (section 3 item 3)
GUARDS = ("cvtune", "wikitune", "dev302", "typing76", "user-reported")   # section 4: every one, in both settings, plus discordtune chat
SETTINGS = ("chat", "formal")
REQUIRED_CELLS = [(st, g) for st in SETTINGS for g in GUARDS] + [("chat", "discordtune")]
GUARDS_S7E = GUARDS + ("fw",)            # S7e section 3 item 4: the function-word sentences guard too
REQUIRED_S7E = [(st, g) for st in SETTINGS for g in GUARDS_S7E] + [("chat", "discordtune")]
LL_CHANGED = 1e-6                                  # a candidate's LL counts as changed by context above this (log10)


# ------------------------------------------------------------------ pure functions
def pick(r, alpha, tau, col="ll", delta=0):
    """Index of the chosen candidate. Rerank only when the n-gram margin (first minus second) is < tau; ties go to the better n-gram rank.
    S7e: the fused winner replaces the first only if its fused score is at least `delta` above the first's (0: always, as before)."""
    if r["n"] < 2 or r["ng"][0] - r["ng"][1] >= tau:
        return 0
    s = [g + alpha * l for g, l in zip(r["ng"], r[col])]
    j = max(range(len(s)), key=lambda i: (s[i], -i))
    return j if s[j] - s[0] >= delta else 0


def correct(rows, alpha, tau, col="ll"):
    return sum(r["ok"][pick(r, alpha, tau, col)] for r in rows)


def select(sets, col="ll"):
    """(alpha, tau, pooled top1) maximising the top1 over the tuning sets pooled; ties: smaller tau, then smaller alpha.
    `sets` is a dict name -> rows; only TUNING names are read."""
    rows = [r for name in TUNING for r in sets[name]]
    best = None
    for tau in TAUS:
        for alpha in ALPHAS:
            c = correct(rows, alpha, tau, col)
            if best is None or c > best[2]:
                best = (alpha, tau, c)
    return best


def fixed_broken(rows, alpha, tau, delta, col="ll"):
    """S7e: rows the fusion turns right (fixed) and wrong (broken) against the n-gram first."""
    f = b = 0
    for r in rows:
        j = pick(r, alpha, tau, col, delta)
        f += bool(r["ok"][j] and not r["ok"][0])
        b += bool(r["ok"][0] and not r["ok"][j])
    return f, b


def select_weighted(sets, weight, col="ll"):
    """S7e section 2.2: (alpha, tau, delta, fixed, broken) maximising fixed - weight * broken over the tuning sets pooled; ties: fewer
    broken, then larger delta, then smaller tau, then smaller alpha. Only TUNING names are read."""
    rows = [r for name in TUNING for r in sets[name]]
    best = key = None
    for alpha in ALPHAS:
        for tau in TAUS:
            for delta in DELTAS:
                f, b = fixed_broken(rows, alpha, tau, delta, col)
                k = (f - weight * b, -b, delta, -tau, -alpha)
                if key is None or k > key:
                    best, key = (alpha, tau, delta, f, b), k
    return best


def kept(sets, keep):
    """S7d: the tuning sets reduced to the rows whose flag is 0 (gold sentence not verbatim in the training text); other sets as
    they are. keep: {tuning set: [0/1 per row, row i at index i - 1]}; None keeps everything."""
    if keep is None:
        return sets
    return {name: [r for r in rows if keep[name][r["i"] - 1] == 0] if name in TUNING else rows for name, rows in sets.items()}


def lm_alone(r, col="ll"):
    """The small model alone: the candidate with the highest LL (ties: better n-gram rank); rows with one candidate keep it."""
    return 0 if r["n"] < 2 else max(range(r["n"]), key=lambda i: (r[col][i], -i))


def rowstats(rows, picks):
    """The rowstats text of tools/evalstats.py (`row ok errors gold_len`) from numbers only; errors are 0 on a correct row."""
    return "".join(f"{r['i']}\t{r['ok'][p]}\t{0 if r['ok'][p] else r['ed'][p]}\t{r['glen']}\n" for r, p in zip(rows, picks))


def report(rows, alpha, tau, col="ll"):
    n2 = [r for r in rows if r["n"] >= 2]
    ctx = [r for r in n2 if r["key"] > 0]          # rows that can be reranked and have a context key
    changed = [r for r in ctx if max(abs(a - b) for a, b in zip(r["ll"], r["ll0"])) > LL_CHANGED]
    return {"n": len(rows), "n2": len(n2),
            "triggered": sum(r["ng"][0] - r["ng"][1] < tau for r in n2),
            "oracle8": sum(any(r["ok"]) for r in rows),
            "base": sum(r["ok"][0] for r in rows),
            "lm_alone": sum(r["ok"][lm_alone(r, col)] for r in rows),
            "key_rows": sum(r["key"] > 0 for r in rows), "ll_changed": len(changed),
            "alone_changed": sum(lm_alone(r, "ll") != lm_alone(r, "ll0") for r in ctx),
            "key16_longer": sum(r["key16"] > r["key"] for r in n2)}


def check_rows(rows, path):
    if [r["i"] for r in rows] != list(range(1, len(rows) + 1)):
        raise ValueError(f"{path}: rows are not 1..n in order")


def load_scores(path):
    with open(path, encoding="utf-8") as f:
        rows = [json.loads(l) for l in f if l.strip()]
    check_rows(rows, path)
    return rows


def decision(cells):
    """Section 4 for one model. cells[(setting, set)] = dict(fixed, broken, p, ci_lo, dcer, n) of the frozen-main fusion vs the n-gram first.
    Returns [(criterion, passed)]; the last entry is the overall verdict."""
    missing = [c for c in REQUIRED_CELLS if c not in cells]
    out = [(f"every guard set in both settings plus discordtune chat present (missing: {missing})", not missing)]
    if ("chat", "discordtune") not in cells:
        return out + [("PASS (all above)", False)]
    d = cells[("chat", "discordtune")]
    delta = d["fixed"] - d["broken"]
    out += [(f"delta top1 >= +1.0 pt ({delta} rows of {d['n']})", 100 * delta / d["n"] >= 1.0),
            (f"exact McNemar p < 0.05 (p = {d['p']:.4f})", d["p"] < 0.05),
            (f"delta top1 95% interval excludes 0 (lower {d['ci_lo']})", d["ci_lo"] > 0),
            (f"delta CER <= 0 ({d['dcer']:+.3f} pt)", d["dcer"] <= 0)]
    bad = [k for k, c in cells.items() if k[1] != "discordtune" and c["fixed"] < c["broken"] and c["p"] < 0.05]
    out.append((f"no other cell net-negative with p < 0.05 (bad: {bad})", not bad))
    out.append(("PASS (all above)", all(ok for _, ok in out)))
    return out


def decision_weighted(cells, shipped, weight):
    """S7e section 3. cells / shipped[(setting, set)] = cell_stats of the new / shipped values against the n-gram first.
    Returns [(criterion, passed)]; the last entry is the overall verdict."""
    missing = [c for c in REQUIRED_S7E if c not in cells or c not in shipped]
    out = [(f"every guard set in both settings plus discordtune chat present (missing: {missing})", not missing)]
    k = ("chat", "discordtune")
    if k not in cells or k not in shipped:
        return out + [("PASS (all above)", False)]
    d, s = cells[k], shipped[k]
    net, net0 = d["fixed"] - weight * d["broken"], s["fixed"] - weight * s["broken"]
    out += [(f"1. discordtune chat fixed - {weight:g} x broken > 0 and above the shipped values ({net:g}; shipped {net0:g})",
             net > 0 and net > net0),
            (f"2. discordtune chat broken below the shipped values ({d['broken']}; shipped {s['broken']})", d["broken"] < s["broken"]),
            (f"3. discordtune chat delta top1 >= 0 ({d['fixed'] - d['broken']:+d} rows)", d["fixed"] - d["broken"] >= 0)]
    bad = [c for c in REQUIRED_S7E if c in cells and c[1] != "discordtune" and cells[c]["fixed"] < cells[c]["broken"] and cells[c]["p"] < 0.05]
    out.append((f"4. no guard cell with fixed < broken and p < 0.05 (bad: {bad})", not bad))
    out.append(("PASS (all above)", all(ok for _, ok in out)))
    return out


# ------------------------------------------------------------------ driver
def cell_stats(base_path, new_path):
    """Numbers for the decision from the two rowstats files, with the same functions evalstats.compare uses."""
    b, c = evalstats.read_rowstats(base_path), evalstats.read_rowstats(new_path)
    fixed = sum(1 for x, y in zip(b, c) if not x[0] and y[0])
    broken = sum(1 for x, y in zip(b, c) if x[0] and not y[0])
    g = sum(x[2] for x in b)
    return {"n": len(b), "fixed": fixed, "broken": broken, "p": evalstats.mcnemar_exact(fixed, broken),
            "dcer": 100 * (sum(y[1] for y in c) - sum(x[1] for x in b)) / g}


def table_row(bp, np_, label, first):
    """One evalstats table row; the two header lines only for the first row of a block."""
    t = evalstats.compare(bp, np_, label).split("\n")
    return "\n".join(t if first else t[2:])


def run_setting(model, setting, sets, out, keep=None):
    """Select on the tuning sets (S7d: only their kept rows when `keep` is given), then score every set, all rows, with the frozen
    values. Returns (decision cells, tuning gain in points on the rows the selection used, stop notes)."""
    sel = kept(sets, keep)
    frozen = {"": select(sel, "ll"), "k16": select(sel, "ll16")}
    tune_base = sum(r["ok"][0] for t in TUNING for r in sel[t])
    tune_n = sum(len(sel[t]) for t in TUNING)
    if tune_n == 0:
        sys.exit(f"fusegrid: no tuning rows left to select on for {model} {setting} (every row flagged?)")
    gain = 100 * (frozen[""][2] - tune_base) / tune_n
    print(f"\n## {model} {setting}: frozen on {'+'.join(TUNING)}{' (kept rows)' if keep else ''}: alpha {frozen[''][0]}, tau {frozen[''][1]}; "
          f"pooled top1 {tune_base} -> {frozen[''][2]} of {tune_n} ({gain:+.2f} pt). "
          f"16-character context (record only), own selection by the same rule: alpha {frozen['k16'][0]}, tau {frozen['k16'][1]}, pooled top1 {frozen['k16'][2]}")
    cells, stop = {}, []
    for k, (s, rows) in enumerate(sets.items()):
        for var, col in (("", "ll"), ("k16", "ll16")):
            al, ta, _ = frozen[var]
            tag = f"{model}.{setting}.{s}" + (".k16" if var else "")
            bp, np_ = (os.path.join(out, "rowstats", f"{tag}.{x}.txt") for x in ("base", "new"))
            open(bp, "w", newline="").write(rowstats(rows, [0] * len(rows)))
            open(np_, "w", newline="").write(rowstats(rows, [pick(r, al, ta, col) for r in rows]))
            print(table_row(bp, np_, f"{model} {setting} {s}" + (" (16-char context)" if var else ""), first=(k == 0 and not var)))
            if not var:
                cells[(setting, s)] = cell_stats(bp, np_)
                if s == "discordtune":
                    cells[(setting, s)]["ci_lo"] = evalstats.paired_bootstrap(evalstats.read_rowstats(bp), evalstats.read_rowstats(np_))[0][0]
        rep = report(rows, frozen[""][0], frozen[""][1])
        print(f"  {s}: n {rep['n']} (>=2 candidates {rep['n2']}), triggered {rep['triggered']}, n-gram top1 {rep['base']}, "
              f"LM alone {rep['lm_alone']}, oracle@8 {rep['oracle8']}, key not sentinel {rep['key_rows']}, "
              f"LL changed by context {rep['ll_changed']}, LM-alone pick changed {rep['alone_changed']}, 16-char key longer {rep['key16_longer']}")
        if s in CTX_REQUIRED and rep["ll_changed"] < 1:
            stop.append(f"context not wired: {model} {setting} {s}")
    return cells, gain, stop


def run_setting_weighted(model, setting, sets, out, weight, keep=None):
    """S7e: select by fixed - weight * broken (S7e section 2.2), then for every set the table row against the n-gram first and the row
    against the shipped values (section 2.4), each with the weighted net. Returns (cells, shipped cells, stop notes)."""
    sel = kept(sets, keep)
    tune = [r for t in TUNING for r in sel[t]]
    if not tune:    # every (alpha, tau, delta) would tie at 0/0 and the tie rule would "select" a corner (as run_setting stops)
        sys.exit(f"fusegrid: no tuning rows left to select on for {model} {setting} (every row flagged?)")
    al, ta, de, f, b = select_weighted(sel, weight)
    f0, b0 = fixed_broken(tune, *SHIPPED)
    print(f"\n## {model} {setting}: selected on {'+'.join(TUNING)}{' (kept rows)' if keep else ''} by fixed - {weight:g} x broken: "
          f"alpha {al}, tau {ta}, delta {de}; fixed {f}, broken {b} of {len(tune)} (shipped {SHIPPED[0]}/{SHIPPED[1]}/{SHIPPED[2]}: "
          f"fixed {f0}, broken {b0})")
    cells, shipped, stop = {}, {}, []
    for k, (s, rows) in enumerate(sets.items()):
        tag = f"{model}.{setting}.{s}"
        bp, sp, np_ = (os.path.join(out, "rowstats", f"{tag}.{x}.txt") for x in ("base", "shipped", "new"))
        open(bp, "w", newline="").write(rowstats(rows, [0] * len(rows)))
        open(sp, "w", newline="").write(rowstats(rows, [pick(r, *SHIPPED[:2], "ll", SHIPPED[2]) for r in rows]))
        open(np_, "w", newline="").write(rowstats(rows, [pick(r, al, ta, "ll", de) for r in rows]))
        print(table_row(bp, np_, f"{model} {setting} {s}", first=(k == 0)))
        print(table_row(sp, np_, f"{model} {setting} {s} (vs shipped)", first=False))
        cells[(setting, s)], shipped[(setting, s)] = cell_stats(bp, np_), cell_stats(bp, sp)
        c, c0 = cells[(setting, s)], shipped[(setting, s)]
        rep = report(rows, al, ta)
        print(f"  {s}: fixed - {weight:g} x broken {c['fixed'] - weight * c['broken']:g} ({c['fixed']}/{c['broken']}); shipped "
              f"{c0['fixed'] - weight * c0['broken']:g} ({c0['fixed']}/{c0['broken']}); LL changed by context {rep['ll_changed']}")
        if s in CTX_REQUIRED and rep["ll_changed"] < 1:
            stop.append(f"context not wired: {model} {setting} {s}")
    return cells, shipped, stop


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--scores", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--models", nargs="+", default=["S", "M"]); ap.add_argument("--settings", nargs="+", default=["chat", "formal"])
    ap.add_argument("--sets", nargs="+", required=True)
    ap.add_argument("--tune-keep", help="S7d: DIR with <tuning set>.flags (0/1 per row, buildcheck.py substr --flags); select on the 0 rows only")
    ap.add_argument("--break-weight", type=float, help="S7e: select by fixed - W x broken over (alpha, tau, delta); decide by S7e section 3")
    a = ap.parse_args(argv)
    if a.break_weight is not None and not a.break_weight > 0:   # NaN fails this too: every comparison would be False
        ap.error("--break-weight must be a number > 0")
    required = REQUIRED_S7E if a.break_weight is not None else REQUIRED_CELLS
    keep = None
    if a.tune_keep:
        keep = {t: open(os.path.join(a.tune_keep, f"{t}.flags")).read().split() for t in TUNING}
        if any(x not in ("0", "1") for v in keep.values() for x in v):
            sys.exit("fusegrid: a .flags file holds something other than 0 / 1 per line")
        keep = {t: [int(x) for x in v] for t, v in keep.items()}
    os.makedirs(os.path.join(a.out, "rowstats"), exist_ok=True)
    stop, chat_gain = [], []
    for model in a.models:
        cells, shipped = {}, {}
        for setting in a.settings:
            sets = {}
            for s in a.sets:
                p = os.path.join(a.scores, f"{model}.{setting}.{s}.jsonl")
                if os.path.isfile(p):
                    sets[s] = load_scores(p)
                elif (setting, s) in required:
                    print(f"MISSING {model} {setting} {s}: no file {p}")
            for s in CTX_REQUIRED:
                if s in a.sets and s not in sets and (setting == "chat" or s != "discordtune"):
                    stop.append(f"context not checked, no scores: {model} {setting} {s}")
            if not all(t in sets for t in TUNING):
                sys.exit(f"fusegrid: tuning sets {TUNING} missing for {model} {setting}")
            for t in TUNING if keep else ():
                if len(keep[t]) != len(sets[t]):      # row i of the scores is line i of the flags (load_scores checks i = 1..n)
                    sys.exit(f"fusegrid: {t}.flags has {len(keep[t])} rows, the scores of {model} {setting} {t} have {len(sets[t])}")
            if a.break_weight is not None:
                c, c0, st = run_setting_weighted(model, setting, sets, a.out, a.break_weight, keep)
                cells.update(c); shipped.update(c0); stop += st
                continue
            c, gain, st = run_setting(model, setting, sets, a.out, keep)
            cells.update(c); stop += st
            if setting == "chat":
                chat_gain.append(gain)
        print(f"\n### {'S7e section 3' if a.break_weight is not None else 'section 4'}, {model}")
        dec = decision_weighted(cells, shipped, a.break_weight) if a.break_weight is not None else decision(cells)
        for crit, ok in dec:
            print(f"- {'PASS' if ok else 'FAIL'}: {crit}")
        if not dec[0][1]:
            stop.append(f"the decision cannot be made for {model}: {dec[0][0]}")
    if chat_gain and all(g < 0.5 for g in chat_gain):
        print(f"STOP CONDITION (section 4): every model gains < +0.5 pt on {'+'.join(TUNING)} in the chat setting ({chat_gain})")
        stop.append("gain below +0.5 pt")
    if stop:
        print("STOP: " + "; ".join(stop))
        sys.exit(3)


if __name__ == "__main__":
    main()
