"""S7a fusion grid, frozen selection, rowstats and the standard tables (contract sections 2.3, 3.3, 4). Numbers only, numpy not even needed.

  python fusegrid.py --scores DIR --models S M --settings chat formal \
         --sets cvtune wikitune dev302 typing76 user-reported [discordtune] --out OUT

reads DIR/<model>.<setting>.<set>.jsonl (score.py output), writes OUT/rowstats/<model>.<setting>.<set>.{base,new}[.k16].txt (only [0-9\\t\\n])
and prints Markdown: per model x setting the frozen (alpha, tau), one tools/evalstats.py table row per set, and the section 3 / 4 reports.
The (alpha, tau) selection reads ONLY the tuning sets (TUNING); the other sets are scored with the frozen values.
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
TUNING = ("cvtune", "wikitune")
CTX_REQUIRED = ("user-reported", "discordtune")   # sets that must show rows whose LL changes with context (section 3 item 3)
LL_CHANGED = 1e-6                                  # a candidate's LL counts as changed by context above this (log10)


# ------------------------------------------------------------------ pure functions
def pick(r, alpha, tau, col="ll"):
    """Index of the chosen candidate. Rerank only when the n-gram margin (first minus second) is < tau; ties go to the better n-gram rank."""
    if r["n"] < 2 or r["ng"][0] - r["ng"][1] >= tau:
        return 0
    s = [g + alpha * l for g, l in zip(r["ng"], r[col])]
    return max(range(len(s)), key=lambda i: (s[i], -i))


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
    rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    check_rows(rows, path)
    return rows


def decision(cells):
    """Section 4 for one model. cells[(setting, set)] = dict(fixed, broken, p, ci_lo, dcer, n) of the frozen-main fusion vs the n-gram first.
    Returns [(criterion, passed)]; the last entry is the overall verdict."""
    d = cells.get(("chat", "discordtune"))
    out = []
    if d is None:
        return [("discordtune chat present", False)]
    delta = d["fixed"] - d["broken"]
    out += [(f"delta top1 >= +1.0 pt ({delta} rows of {d['n']})", 100 * delta / d["n"] >= 1.0),
            (f"exact McNemar p < 0.05 (p = {d['p']:.4f})", d["p"] < 0.05),
            (f"delta top1 95% interval excludes 0 (lower {d['ci_lo']})", d["ci_lo"] > 0),
            (f"delta CER <= 0 ({d['dcer']:+.3f} pt)", d["dcer"] <= 0)]
    bad = [k for k, c in cells.items() if k[1] != "discordtune" and c["fixed"] < c["broken"] and c["p"] < 0.05]
    out.append((f"no other cell net-negative with p < 0.05 (bad: {bad})", not bad))
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


def run_setting(model, setting, sets, out):
    """Select on the tuning sets, then score every set with the frozen values. Returns (decision cells, tuning gain in points, stop notes)."""
    frozen = {"": select(sets, "ll"), "k16": select(sets, "ll16")}
    tune_base = sum(r["ok"][0] for t in TUNING for r in sets[t])
    tune_n = sum(len(sets[t]) for t in TUNING)
    gain = 100 * (frozen[""][2] - tune_base) / tune_n
    print(f"\n## {model} {setting}: frozen on {'+'.join(TUNING)}: alpha {frozen[''][0]}, tau {frozen[''][1]}; "
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


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--scores", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--models", nargs="+", default=["S", "M"]); ap.add_argument("--settings", nargs="+", default=["chat", "formal"])
    ap.add_argument("--sets", nargs="+", required=True)
    a = ap.parse_args(argv)
    os.makedirs(os.path.join(a.out, "rowstats"), exist_ok=True)
    stop, chat_gain = [], []
    for model in a.models:
        cells = {}
        for setting in a.settings:
            sets = {}
            for s in a.sets:
                p = os.path.join(a.scores, f"{model}.{setting}.{s}.jsonl")
                if os.path.isfile(p):
                    sets[s] = load_scores(p)
                else:
                    print(f"SKIP {model} {setting} {s}: no file {p}")
            if not all(t in sets for t in TUNING):
                sys.exit(f"fusegrid: tuning sets {TUNING} missing for {model} {setting}")
            c, gain, st = run_setting(model, setting, sets, a.out)
            cells.update(c); stop += st
            if setting == "chat":
                chat_gain.append(gain)
        print(f"\n### section 4, {model}")
        for crit, ok in decision(cells):
            print(f"- {'PASS' if ok else 'FAIL'}: {crit}")
    if chat_gain and all(g < 0.5 for g in chat_gain):
        print(f"STOP CONDITION (section 4): every model gains < +0.5 pt on {'+'.join(TUNING)} in the chat setting ({chat_gain})")
        stop.append("gain below +0.5 pt")
    if stop:
        print("STOP: " + "; ".join(stop))
        sys.exit(3)


if __name__ == "__main__":
    main()
