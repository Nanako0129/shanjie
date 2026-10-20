#!/usr/bin/env python3
"""P4d analysis (README of this directory): the colloquial term moved onto the small model's layer, so the n-gram first stays the
anchor of the shipping fusion, with an optional soft gate by the small model's agreement. Counts only: never a sentence, context,
word or character of a row.

  python3 p4d.py --name chatclause --rows ROWS --dump D --dump-words W --rowstats R --tinylm-scores S --t2 T2.sjlm --select --out SEL.json
  python3 p4d.py --name discordtune ... --cell-from SEL.json --clause 1 [--out-rowstats CELL.rs] --out OUT.json

All inputs come from one shipping-setting run of the CLI (--context --packs acg --tinylm, plus --dump --dump-words --rowstats
--tinylm-scores). For candidate i of the first 8 (the n-gram order of the scores record), col_i = lc_i - 0.5 k_i (P4's T2 log10 term
and overlay/pack word count; gamma = mu / 2 as P4c's mu 0.5 cell), and

  ll_eff_i = ll_i + g_i * (mu / alpha) * (col_i - col_0),   g_i = 1 (T = inf) or 1 / (1 + exp(-(ll_i - ll_0) / T))

goes into the shipping pick (tools/triage.py `pick` = core/src/tinylm.rs) with the cell's alpha, tau 2, delta 0.25. Candidate 0 is the
n-gram first and its own term is 0, so the colloquial term only ever acts through the fusion's margin, never by moving the anchor.
Controls (not selectable): mu 0 at alpha 0.3 must equal the shipping rowstats row by row (the anchor: STOP otherwise); mu 0 at alpha
0.5 is the fusion-only arm. Selection (chatclause): the largest U2 = fixes - 2 breaks among cells with U2 >= 0; ties larger net,
smaller mu, gate inf then 1.0 then 0.5, alpha 0.3 first.
Exit codes: 0 ok, 1 STOP, 3 NO CELL QUALIFIES.
"""
import argparse, hashlib, json, math, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for sub in ("tools", "reference/proto", "experiments/diag-near-miss", "experiments/s7a-tinylm", "experiments/p4-colloquial",
            "experiments/p4c-colloquial"):
    sys.path.insert(0, os.path.join(ROOT, sub))
import evalstats  # noqa: E402
import triage  # noqa: E402
import p4c  # noqa: E402

INF = math.inf
TAU, DELTA, GAMMA_PER_MU = 2.0, 0.25, 0.5
MUS, ALPHAS, GATES = (0.15, 0.25, 0.35, 0.5), (0.3, 0.5), (INF, 1.0, 0.5)
GRID = [(mu, a, t) for mu in MUS for a in ALPHAS for t in GATES]          # 24 cells
CONTROLS = [(0.0, 0.3, INF), (0.0, 0.5, INF)]                              # shipping anchor, fusion-only arm
SHIP = (0.0, 0.3, INF)
N_SELECT, N_DISCORD = p4c.N_SELECT, p4c.N_DISCORD
T2_SHA256 = "2984eeb525a06b12c244b3c04b84641c9a67492486132ebf404198372b684c33"   # README: P4's T2 table
# P4's setting anchor (D1's recomputation): the shipping final against the n-gram first. A mismatch means the run was not the
# shipping setting (e.g. no --context or --packs acg), which the self-consistency anchor cannot see.
SETTING_ANCHORS = {"discordtune": (107, 26), "chatclause": (111, 26)}
NAMES = ("chatclause", "discordtune", "cvtune-clean", "typing76", "user-reported", "dev302", "chatjudge2")
N_CONFIRM = 5783                     # chatjudge-2 (README "chatjudge-2 確認")
CONFIRM_RATIO = 0.65


def sigmoid(x):
    """1 / (1 + e^-x) without overflow for large |x|."""
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def cols_from(pre):
    """col_i = lc_i - 0.5 k_i (gamma = mu / 2) for the first 8 candidates of each row, from p4.precompute's output."""
    return [[lc - GAMMA_PER_MU * k for lc, k in zip(p["lc"]["T2"][:8], p["k"][:8])] for p in pre]


def final_index(sc, col, mu, alpha, gate):
    """The cell's final pick over the scores record `sc` (first 8 in n-gram order) with colloquial terms `col` (same order)."""
    ng, ll = sc["ng"], sc.get("ll")
    if ll is None:
        return 0
    n = min(len(ng), len(ll))
    eff = []
    for i in range(n):
        g = 1.0 if gate == INF else sigmoid((ll[i] - ll[0]) / gate)
        eff.append(ll[i] + (g * (mu / alpha) * (col[i] - col[0]) if mu else 0.0))
    return triage.pick(ng[:n], eff, alpha, TAU, DELTA)


def confirm_verdict(s):
    """README "chatjudge-2 確認": net > 0, exact McNemar p < 0.05, and breaks / fixes <= 0.65."""
    return s["net"] > 0 and s["p"] < 0.05 and p4c.ratio_ok(s, CONFIRM_RATIO)


def u2(s):
    return s["fixes"] - 2 * s["breaks"]


def choose(cells):
    """cells: list of (mu, alpha, gate, stats). Largest U2 among U2 >= 0; ties larger net, smaller mu, gate inf, 1.0, 0.5, alpha 0.3."""
    order = {INF: 0, 1.0: 1, 0.5: 2}
    q = [c for c in cells if u2(c[3]) >= 0]
    return min(q, key=lambda c: (-u2(c[3]), -c[3]["net"], c[0], order[c[2]], abs(c[1] - 0.3))) if q else None


def label(c):
    return f"{c[0]},{c[1]},{'inf' if c[2] == INF else c[2]}"


def parse_cell(text):
    try:
        mu, a, g = text.split(",")
        cell = (float(mu), float(a), INF if g == "inf" else float(g))
    except ValueError:
        raise SystemExit("--cell wants MU,ALPHA,GATE (GATE inf, 1.0 or 0.5)")
    if cell not in GRID:
        raise SystemExit("off-grid cell refused")
    return cell


def evaluate(scores, cols, base_ok, cell):
    """-> (stats, picks) for one cell; `base_ok` is the shipping rowstats ok per row."""
    picks = [final_index(sc, col, *cell) for sc, col in zip(scores, cols)]
    new_ok = [bool(sc["ok"][j]) for sc, j in zip(scores, picks)]
    s = p4c.stats(base_ok, new_ok)
    s["u2"] = u2(s)
    return s, picks


def reach(scores, cols, base_ok, mu):
    """Report only (README "只報"): rows the shipping final gets wrong where P4's first-swap (the first 8 re-ranked by ng + mu col)
    would put a right candidate first, and how many of them have an n-gram gap >= tau, which the fusion, and so this scoring,
    never looks at."""
    fixes = beyond_tau = 0
    for sc, col, ok in zip(scores, cols, base_ok):
        ng = sc["ng"]
        n = min(len(ng), len(col))
        if ok or n < 2:
            continue
        top = max(range(n), key=lambda j: (ng[j] + mu * col[j], -j))
        if top != 0 and sc["ok"][top]:   # a swap: the n-gram first itself is not P4's doing
            fixes += 1
            beyond_tau += ng[0] - ng[1] >= TAU
    return {"swap_fixes": fixes, "swap_fixes_gap_ge_tau": beyond_tau}


def check_anchor(scores, cols, base_ok, name=None):
    if name in SETTING_ANCHORS:
        s = p4c.stats([bool(sc["ok"][0]) for sc in scores], base_ok)
        if (s["fixes"], s["breaks"]) != SETTING_ANCHORS[name]:
            print(f"STOP: setting anchor {name}: shipping vs n-gram first {s['fixes']}/{s['breaks']}, expected {SETTING_ANCHORS[name]}")
            raise SystemExit(1)
    _, picks = evaluate(scores, cols, base_ok, SHIP)
    bad_c = [i for i, (sc, j) in enumerate(zip(scores, picks), 1) if "ll" in sc and sc["c"] != j]
    bad_ok = [i for i, (sc, j, o) in enumerate(zip(scores, picks, base_ok), 1) if bool(sc["ok"][j]) != o]
    if bad_c or bad_ok:
        print(f"STOP: anchor mismatch: pick differs on {len(bad_c)} rows, ok on {len(bad_ok)} rows, first rows {(bad_c + bad_ok)[:10]}")
        raise SystemExit(1)


def load(a):
    """The P4 loader (d23 consistency checks), the T2 lc and the overlay/pack k for the first 8 candidates."""
    import d23 as D
    import p4
    with open(a.t2, "rb") as f:
        if hashlib.sha256(f.read()).hexdigest() != T2_SHA256:
            print("STOP: --t2 is not P4's T2 table (sha256)")
            raise SystemExit(1)
    lenient = D.T.load_lenient()
    try:
        rows, ok = D.build_rows(a.rows, a.dump, a.dump_words, a.rowstats, a.tinylm_scores, lenient)
        _, ovl = D.load_lex_scores()
        lex = D.T.Lex.load()
        # only the first 8 candidates carry ll, so only they need lc and k
        pre = p4.precompute([{**r, "cands": r["cands"][:8]} for r in rows], {"T2": p4.Table(a.t2)}, ovl, lex.base)
    except (p4.Stop, RuntimeError) as e:
        print(f"STOP: {e}")
        raise SystemExit(1)
    scores = [r["scores"] for r in rows]
    cols = cols_from(pre)
    base = evalstats.read_rowstats(a.rowstats)
    return scores, cols, [bool(o) for o in ok], base


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for k in ("rows", "dump", "dump-words", "rowstats", "tinylm-scores", "t2", "out"):
        ap.add_argument("--" + k, required=True)
    ap.add_argument("--name", required=True, choices=NAMES)
    m = ap.add_mutually_exclusive_group(required=True)
    m.add_argument("--select", action="store_true")
    m.add_argument("--cell")
    m.add_argument("--cell-from")
    ap.add_argument("--clause", choices=("1", "2a", "2b", "3", "confirm", "report"), default="report")
    ap.add_argument("--out-rowstats")
    a = ap.parse_args(argv)
    if a.select and a.name != "chatclause":
        raise SystemExit("--select is the chatclause step")
    if a.clause == "1" and a.name != "discordtune":
        raise SystemExit("--clause 1 is the discordtune clause")
    if (a.clause == "confirm") != (a.name == "chatjudge2"):
        raise SystemExit("--clause confirm is the chatjudge2 clause, and chatjudge2 takes no other")
    outf = open(a.out, "w", encoding="utf-8")   # fail now, not after the load
    scores, cols, base_ok, base = load(a)
    if a.select and len(base) != N_SELECT or a.clause == "1" and len(base) != N_DISCORD or a.clause == "confirm" and len(base) != N_CONFIRM:
        raise SystemExit(f"{a.name}: unexpected row count {len(base)}")
    check_anchor(scores, cols, base_ok, a.name)
    res = {"name": a.name, "n": len(base)}
    ctrl = {label(c): p4c.jsonable(evaluate(scores, cols, base_ok, c)[0]) for c in CONTROLS}
    for k, s in ctrl.items():
        print(f"control {k} fixes {s['fixes']} breaks {s['breaks']} net {s['net']} u2 {s['u2']}")
    res["controls"] = ctrl
    res["reach"] = {str(mu): reach(scores, cols, base_ok, mu) for mu in MUS}
    for mu, r in res["reach"].items():
        print(f"reach mu {mu} swap_fixes {r['swap_fixes']} gap_ge_tau {r['swap_fixes_gap_ge_tau']}")
    if a.select:
        cells = [(*c, evaluate(scores, cols, base_ok, c)[0]) for c in GRID]
        for mu, al, g, s in cells:
            print(f"cell {label((mu, al, g))} u2 {s['u2']} {p4c.kv(s)}")
        best = choose(cells)
        print(label(best[:3]) if best else "NO CELL QUALIFIES")
        res["cells"] = {label(c[:3]): p4c.jsonable(c[3]) for c in cells}
        res["chosen"] = label(best[:3]) if best else None
        with outf:
            json.dump(res, outf, indent=1)
        return 0 if best else 3
    if a.cell_from:
        with open(a.cell_from, encoding="utf-8") as f:
            chosen = json.load(f)["chosen"]
        if not chosen:
            raise SystemExit("--cell-from: the selection chose no cell")
        cell = parse_cell(chosen)
    elif a.cell:
        cell = parse_cell(a.cell)
    else:
        raise SystemExit("give --select, --cell or --cell-from")
    s, picks = evaluate(scores, cols, base_ok, cell)
    print(f"cell {label(cell)} u2 {s['u2']} {p4c.kv(s)}")
    res.update({"cell": label(cell), "clause": a.clause, **p4c.jsonable(s)})
    if a.clause == "2b":
        cand = [(bool(sc["ok"][j]), sc["ed"][j], sc["glen"]) for sc, j in zip(scores, picks)]
        (lo, hi), _ = evalstats.paired_bootstrap(base, cand, level=0.90)
        pp = 100 * lo / len(base)
        res.update({"verdict": pp >= -1.0, "bootstrap90_lower_rows": lo, "bootstrap90_lower_pp": pp})
        print(f"clause 2b {'PASS' if res['verdict'] else 'FAIL'} lower90_rows {lo} lower90_pp {pp:.3f}")
    elif a.clause == "confirm":
        res["verdict"] = confirm_verdict(s)
        print(f"clause confirm {'PASS' if res['verdict'] else 'FAIL'}")
    elif a.clause != "report":
        res["verdict"] = p4c.clause_verdict(a.clause, s)
        print(f"clause {a.clause} {'PASS' if res['verdict'] else 'FAIL'}")
    res["old_rule"] = p4c.old_rules(s)
    print("old_rule " + " ".join(f"{k} {int(v)}" for k, v in res["old_rule"].items()))
    if a.out_rowstats:
        with open(a.out_rowstats, "w", encoding="utf-8") as f:
            for i, (sc, j) in enumerate(zip(scores, picks), 1):
                f.write(f"{i}\t{int(bool(sc['ok'][j]))}\t{sc['ed'][j]}\t{sc['glen']}\n")
    with outf:
        json.dump(res, outf, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
