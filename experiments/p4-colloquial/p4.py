#!/usr/bin/env python3
"""P4 analysis (README of this directory): a colloquial count table as a separate log-linear term plus a penalty for overlay/pack words, scored on
the dump of the shipping chat setting. Prints and writes counts only: never a sentence, context, word or character of a row.

  python3 experiments/p4-colloquial/p4.py --name chatclause --rows ROWS --dump D --dump-words W --rowstats R --tinylm-scores S \
      --t1 T1.sjlm --t2 T2.sjlm --select --out OUT.json
  python3 experiments/p4-colloquial/p4.py --name discordtune ... --t1 T1.sjlm --cell T1,0.5,0.25 --out OUT.json   (only the needed table is read)

Reuses experiments/diag-near-miss/d23.py (row loading with its consistency checks, C rows, the D3 (ii)/(iii) classification), tools/triage.py,
reference/proto/lm.py BigramLM (classes off, no KN) and experiments/s7a-tinylm/fusegrid.py `pick` (the shipping fusion).
Readings of the README that were ambiguous (chosen closest to the text):
  - lc backs off to (c(w)+1)/(N+V+1) with V = the table's vocabulary size (<s> and </s> included); the end term P(</s>|w_k) backs off to the
    table's own p_eos = eos_total/N, as BigramLM.eos does (a unigram count of </s> is 0 and would make the end term meaningless).
  - k counts words with multiplicity (a word twice in the split counts twice).
  - Candidate j's n-gram score is the scores record's `ng[j]` for the first 8 and the dump score beyond (as d23 does); `ll` exists for the first 8.
  - "Affected" (the 10% denominator) = the new top 8 differs from the original top 8 in set or order. The final choice is nevertheless recomputed for
    every row whose new top 8 all have an `ll` (the fusion also sees the margins, which s changes even when the order does not).
    A row without `ll` in its record is decided by the new n-gram first (d23's rule); it is never "needs rescoring".
  - Final-choice fixes/breaks compare the new final pick with the rowstats final pick (the baseline); n-gram level compares s-first with the dump's rank 0.
  - D3 (ii)/(iii): the rows d23 classes as (ii) or (iii) under the baseline (C rows), counted when s-first is right.
  - Selection ties: larger n-gram net, then smaller mu, then smaller gamma, then T1 before T2; the baseline cell (mu 0, gamma 0) is not a candidate.
"""
import argparse, collections, json, math, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for sub in ("tools", "reference/proto", "experiments/diag-near-miss", "experiments/s7a-tinylm"):
    sys.path.insert(0, os.path.join(ROOT, sub))
import d23 as D  # noqa: E402
import fusegrid as FG  # noqa: E402
import evalstats  # noqa: E402

T = D.T
SHIP = D.SHIP
TABLES = ("T1", "T2")
MUS, GAMMAS = (0.2, 0.5, 1.0), (0.0, 0.25, 0.5)
# 18 table cells + the two penalty-only cells (no table: mu 0).
GRID = [(t, mu, g) for t in TABLES for mu in MUS for g in GAMMAS] + [("none", 0.0, g) for g in (0.25, 0.5)]
BASE_FIXES_BREAKS = (107, 26)    # D1's recomputed final-vs-n-gram-first on discordtune


class Stop(Exception):
    """A baseline-recomputation check failed (README: any mismatch stops the run)."""


# ---------------------------------------------------------------- lc and k

class Table:
    """BigramLM without classes and KN, with the README's lc. `memo` caches log10 P per (previous word, word)."""

    def __init__(self, path):
        import lm as protolm
        self.lm = protolm.BigramLM(path, classes=False)
        self.n1 = self.lm.N + len(self.lm.vocab) + 1
        self.memo = {}

    def logp(self, v, w):
        key = (v, w)
        if key not in self.memo:
            pb = self.lm.p_eos if w == "</s>" else (self.lm.count(w) + 1) / self.n1
            self.memo[key] = math.log10(self.lm.prob(v, w, pb))
        return self.memo[key]

    def lc(self, words):
        prev, tot = "<s>", 0.0
        for w in words:
            tot += self.logp(prev, w)
            prev = w
        return tot + self.logp(prev, "</s>")


def k_of(words, ovl, base):
    """Words of the split in the overlay-add.tsv/pack set and not in the base lexicon (D3 (ii)'s set), with multiplicity."""
    return sum(1 for w in words if w in ovl and w not in base)


def precompute(rows, tables, ovl, base):
    """-> per row {"k": [per candidate], "lc": {table name: [per candidate]}} for the first 64 candidates."""
    out = []
    for row in rows:
        cands = row["cands"][:T.BEAM]
        if any(c[2] is None for c in cands):
            raise RuntimeError("a dumped candidate has no split (dump-words does not cover the dump)")
        out.append({"k": [k_of(c[2], ovl, base) for c in cands],
                    "lc": {name: [tb.lc(c[2]) for c in cands] for name, tb in tables.items()}})
    return out


# ---------------------------------------------------------------- one row, one cell

def ng_scores(row):
    sc, cands = row["scores"], row["cands"][:T.BEAM]
    return [sc["ng"][j] if j < len(sc["ng"]) else cands[j][1] for j in range(len(cands))]


def eval_row(row, pre, cell, par=SHIP):
    """-> (order of the new top 8, final pick or None = needs rescoring, affected). Baseline (mu 0, gamma 0): s == ng exactly."""
    table, mu, gamma = cell
    ng = ng_scores(row)
    s = [ng[j] + (mu * pre["lc"][table][j] if mu else 0.0) - (gamma * pre["k"][j] if gamma else 0.0) for j in range(len(ng))]
    orig = sorted(range(len(ng)), key=lambda j: (-ng[j], j))[:T.TOPK]
    order = sorted(range(len(ng)), key=lambda j: (-s[j], j))[:T.TOPK]
    ll = row["scores"].get("ll")
    if ll is None:
        return order, order[0], order != orig
    if any(j >= len(ll) for j in order):
        return order, None, True
    alpha, tau, delta = par
    r = {"n": len(order), "ng": [s[j] for j in order], "ll": [ll[j] for j in order]}
    return order, order[FG.pick(r, alpha, tau, "ll", delta)], order != orig


def unreliable(rescoring, affected):
    return D.unreliable(rescoring, affected)


def eval_cell(rows, ok, pre, cell, lenient, d3cls, par=SHIP):
    """The numbers of one cell against the baseline. d3cls: {row index: "ii" | "iii"} from the baseline classification."""
    o = dict.fromkeys(("ng_fixes", "ng_breaks", "fixes", "breaks", "rescoring", "affected", "ii_fixed", "iii_fixed"), 0)
    for i, row in enumerate(rows):
        t, cands = lenient(row["truth"]), row["cands"]
        order, choice, affected = eval_row(row, pre[i], cell, par)
        base0, new0 = lenient(cands[0][0]) == t, lenient(cands[order[0]][0]) == t
        o["ng_fixes"] += (not base0) and new0
        o["ng_breaks"] += base0 and not new0
        if i in d3cls and new0:
            o["ii_fixed" if d3cls[i] == "ii" else "iii_fixed"] += 1
        o["affected"] += affected
        if choice is None:
            o["rescoring"] += 1
            continue
        now = lenient(cands[choice][0]) == t
        o["fixes"] += (not ok[i]) and now
        o["breaks"] += ok[i] and not now
    o["ng_net"], o["net"] = o["ng_fixes"] - o["ng_breaks"], o["fixes"] - o["breaks"]
    o["ng_p"] = evalstats.mcnemar_exact(o["ng_fixes"], o["ng_breaks"])
    o["unreliable"] = unreliable(o["rescoring"], o["affected"])
    return o


def choose(cells):
    """cells: {(table, mu, gamma): stats}. Max n-gram net; ties: smaller mu, smaller gamma, T1 before T2. The baseline cell is not a candidate."""
    cand = [k for k in cells if (k[1], k[2]) != (0.0, 0.0)]
    return min(cand, key=lambda k: (-cells[k]["ng_net"], k[1], k[2], k[0] == "T2"))


# ---------------------------------------------------------------- baseline checks

def check_baseline(rows, ok, pre, name, lenient, par=SHIP):
    """README: at mu 0, gamma 0 s-first is dump rank 0 and the recomputed fusion is the rowstats pick, every row; on discordtune the
    final-vs-n-gram-first fixes/breaks are D1's +107/-26. Raises Stop (row numbers only, no content)."""
    fixes = breaks = 0
    for i, row in enumerate(rows):
        order, choice, _ = eval_row(row, pre[i], ("none", 0.0, 0.0), par)
        if order[0] != 0:
            raise Stop(f"row {i + 1}: the n-gram first of the baseline is not dump rank 0")
        if choice != row["c"] or (lenient(row["cands"][choice][0]) == lenient(row["truth"])) != ok[i]:
            raise Stop(f"row {i + 1}: the recomputed fusion differs from the rowstats pick")
        t = lenient(row["truth"])
        base0 = lenient(row["cands"][0][0]) == t
        fixes += (not base0) and ok[i]
        breaks += base0 and not ok[i]
    if name == "discordtune" and (fixes, breaks) != BASE_FIXES_BREAKS:
        raise Stop(f"discordtune final vs n-gram first is +{fixes}/-{breaks}, D1 recomputed +{BASE_FIXES_BREAKS[0]}/-{BASE_FIXES_BREAKS[1]}")
    return {"final_fixes_vs_ng_first": fixes, "final_breaks_vs_ng_first": breaks}


# ---------------------------------------------------------------- driver

def d3_classes(rows, ok, lex, ovl, count, lexscore, lenient):
    """-> (crows, {row index: "ii" | "iii"}, C sub-type sizes)."""
    crows = D.c_rows(rows, ok, lex, lenient)
    index = {id(r): i for i, r in enumerate(rows)}
    cls = {}
    for row, r, segs, gw, _, _ in crows:
        k = D.d3_class(row, r, segs, gw, lex, ovl, count, lexscore)[0]
        if k in ("ii", "iii"):
            cls[index[id(row)]] = k
    return crows, cls, dict(collections.Counter(r["sub"] for _, r, *_ in crows))


def cell_label(c):
    return f"{c[0]},{c[1]},{c[2]}"


def render(name, n, res):
    L = [f"## {name} (n = {n}); C sub-types {res['c_subs']}; D3 (ii) rows {res['d3_total']['ii']}, (iii) rows {res['d3_total']['iii']}",
         "\n| table | mu | gamma | ng fixes | ng breaks | ng net | ng p | (ii) fixed | (iii) fixed | final fixes | final breaks | final net | needs rescoring | affected | |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for lab, o in res["cells"].items():
        t, mu, g = lab.split(",")
        L.append(f"| {t} | {mu} | {g} | {o['ng_fixes']} | {o['ng_breaks']} | {o['ng_net']:+d} | {o['ng_p']:.4f} | {o['ii_fixed']} | {o['iii_fixed']} | "
                 f"{o['fixes']} | {o['breaks']} | {o['net']:+d} | {o['rescoring']} | {o['affected']} | {'unreliable' if o['unreliable'] else ''} |")
    if res.get("chosen"):
        L.append(f"\nchosen cell (table, mu, gamma): {res['chosen']}")
    return "\n".join(L + [""])


def main(argv=None):
    a = argparse.ArgumentParser()
    for k in ("name", "rows", "dump", "dump-words", "rowstats", "tinylm-scores", "out"):
        a.add_argument("--" + k, required=True)
    a.add_argument("--t1")
    a.add_argument("--t2")
    a.add_argument("--lm-dir", default=os.path.join(ROOT, "data/lm"), help="bigram.sjlm here supplies the corpus counts of D3 (iii)")
    m = a.add_mutually_exclusive_group(required=True)
    m.add_argument("--select", action="store_true")
    m.add_argument("--cell", metavar="TABLE,MU,GAMMA")
    a = a.parse_args(argv)
    if a.select:
        cells = GRID
    else:
        t, mu, g = a.cell.split(",")
        cells = [(t, float(mu), float(g))]
        if t not in TABLES + ("none",):
            sys.exit(f"--cell: table must be T1, T2 or none, got {t!r}")
    need = sorted({c[0] for c in cells if c[0] in TABLES})
    paths = {"T1": a.t1, "T2": a.t2}
    if any(not paths[t] for t in need):
        sys.exit("the chosen cells need --t1 / --t2")
    import lm as protolm
    lenient = T.load_lenient()
    try:
        rows, ok = D.build_rows(a.rows, a.dump, getattr(a, "dump_words"), a.rowstats, getattr(a, "tinylm_scores"), lenient)
        lexscore, ovl = D.load_lex_scores()
        lex = T.Lex.load()
        pre = precompute(rows, {t: Table(paths[t]) for t in need}, ovl, lex.base)
        base = check_baseline(rows, ok, pre, a.name, lenient)
    except (Stop, RuntimeError) as e:
        print(f"STOP: {e}", file=sys.stderr)
        sys.exit(1)
    count = protolm.BigramLM(os.path.join(a.lm_dir, "bigram.sjlm"), classes=False).count
    _, d3cls, subs = d3_classes(rows, ok, lex, ovl, count, lexscore, lenient)
    out = {c: eval_cell(rows, ok, pre, c, lenient, d3cls) for c in cells}
    res = {"name": a.name, "n": len(rows), "errors": len(rows) - sum(ok), "baseline": base, "c_subs": subs,
           "d3_total": {k: sum(1 for v in d3cls.values() if v == k) for k in ("ii", "iii")},
           "cells": {cell_label(c): o for c, o in out.items()}}
    if a.select:
        res["chosen"] = list(choose(out))
    print(render(a.name, len(rows), res))
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
