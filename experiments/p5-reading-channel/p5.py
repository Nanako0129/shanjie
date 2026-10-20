#!/usr/bin/env python3
"""P5 analysis (README of this directory): a reading-channel term P(reading | word) from the lexicon, added to the small model's layer on
top of P4d's fusion. Counts only on stdout and in the JSON: never a sentence, context, word or character of a row (only --probe prints
sentences, and it is for PUBLIC row files only).

  python3 p5.py --name chatclause --rows ROWS --dump D --dump-words W --rowstats R --tinylm-scores S --t2 T2.sjlm --select --out SEL.json
  python3 p5.py --name discordtune ... --cell-from SEL.json --clause 1 [--out-rowstats CELL.rs] [--out-picks PICKS.tsv] --out OUT.json
  python3 p5.py --probe PUBLIC_ROWS --dump D --dump-words W --rowstats R --tinylm-scores S --t2 T2.sjlm --cell NU,C,COLLOQ

Inputs are one shipping-setting CLI run (--profile chat --context --packs acg --tinylm, plus --dump --dump-words --rowstats
--tinylm-scores), loaded by p4d.load_full (d23 consistency checks, P4's T2 colloquial terms). Cell = (nu, c, colloquial on/off).

  P(r|w)  = 10^s(r,w) / sum_r' 10^s(r',w) over the readings of the same word in d23.load_lex_scores (one reading: 1), in log space.
  ch_i    = sum over the words w of candidate i (the row's syllables cut over them in order, r = the syllables of w)
            of max(log10 P(r|w), -c); a (r, w) not in the lexicon counts 0 and is counted.
  ll_eff_i = ll_i + (nu / alpha) * (ch_i - ch_0) [+ P4d's colloquial term when on], then the shipping pick (tau 2, delta 0.25).
  off: alpha 0.3 (shipping). on: alpha 0.5, mu 0.5, gate T 1.0 (the P4d cell).
Both terms are added to ll before the pick, so their order does not matter; here the colloquial term first, then the channel term.
Controls (not selectable): nu 0 / off = shipping (the anchor: STOP unless it equals the CLI's choice and rowstats row by row);
nu 0 / on = the P4d cell (STOP unless it equals p4d row by row, and on chatclause / discordtune 113/37 and 74/43).
Selection (chatclause): largest U2 = fixes - 2 breaks among cells with U2 >= 0; ties larger net, smaller nu, larger c, off first.
Judging uses the current standard with ratio limit 0.6 (README table); p4c.clause_verdict is the 0.5 version and is not used.
Exit codes: 0 ok, 1 STOP, 3 NO CELL QUALIFIES.
"""
import argparse, json, math, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "experiments", "p4d-colloquial"))
import p4d as P  # noqa: E402  (puts tools, reference/proto, diag-near-miss, p4c ... on sys.path)
import evalstats  # noqa: E402
import triage  # noqa: E402
import p4c  # noqa: E402

INF = math.inf
NUS, CS = (0.1, 0.25, 0.5, 1.0), (1, 2, 4)
GRID = [(nu, c, q) for nu in NUS for c in CS for q in (False, True)]                    # 24 cells
CONTROLS = [(0.0, CS[0], False), (0.0, CS[0], True)]                                    # shipping anchor, P4d cell (c is moot at nu 0)
SHIP, P4D_CELL = CONTROLS
OFF = (0.0, 0.3, INF)                    # colloquial off: (mu, alpha, gate), the shipping fusion
ON = P.CONFIRM_CELL                      # colloquial on: P4d's cell (mu 0.5, alpha 0.5, gate 1.0)
RATIO = 0.6                              # the current standard's breaks / fixes limit (p4c's is 0.5)
CAP_DISCORD = p4c.CAP_DISCORD
NAMES = ("chatclause", "discordtune", "cvtune-clean", "typing76", "user-reported", "dev302")
P4D_ANCHORS = {"chatclause": (113, 37), "discordtune": (74, 43)}   # README: the P4d cell against shipping
TOP_K = 8


def reading_logp(lex):
    """{(reading tuple, word): log10 P(reading | word)} from d23.load_lex_scores' {(reading, word): score}; softmax of 10^s per word."""
    by_word = {}
    for (r, w), s in lex.items():
        by_word.setdefault(w, []).append((r, s))
    out = {}
    for w, rs in by_word.items():
        m = max(s for _, s in rs)
        lse = m + math.log10(sum(10.0 ** (s - m) for _, s in rs))     # shifted by the max: no overflow, no underflow of the sum
        for r, s in rs:
            out[(r, w)] = s - lse
    return out


def word_terms(syls, words, logp):
    """[log10 P(r|w) or None for a pair missing from the lexicon] per word of one candidate, the syllables cut in order (one per
    character). None (not a list) when the words do not cover the row's syllables exactly: that candidate gets ch 0 and is counted."""
    if words is None or sum(len(w) for w in words) != len(syls):
        return None
    out, p = [], 0
    for w in words:
        out.append(logp.get((tuple(syls[p:p + len(w)]), w)))
        p += len(w)
    return out


def ch_of(terms, c):
    """Sum of max(term, -c); a missing pair counts 0, an uncut candidate (None) is 0."""
    return 0.0 if terms is None else sum(max(t, -c) for t in terms if t is not None)


def row_terms(rows, logp):
    """[[word_terms of candidate j for j < 8] per row]."""
    return [[word_terms(r["syls"], cand[2], logp) for cand in r["cands"][:TOP_K]] for r in rows]


def channel(terms, c):
    return [[ch_of(t, c) for t in row] for row in terms]


def term_counts(terms):
    """Report only: word slots of the first 8 candidates, pairs missing from the lexicon, candidates the words could not be cut for, and the
    terms that sit below -c for each c of the grid (clipped)."""
    slots = missing = uncut = 0
    clipped = dict.fromkeys(CS, 0)
    for row in terms:
        for t in row:
            if t is None:
                uncut += 1
                continue
            slots += len(t)
            for x in t:
                if x is None:
                    missing += 1
                else:
                    for c in CS:
                        clipped[c] += x < -c
    return {"candidates": sum(len(r) for r in terms), "uncut_candidates": uncut, "word_slots": slots, "missing_pairs": missing,
            "clipped": {str(c): v for c, v in clipped.items()}}


def fused(sc, col, ch, nu, q):
    """-> (ng, ll_eff, alpha) of one cell, or None for a row without `ll`: ll + P4d's colloquial term (on) + (nu / alpha) (ch_i - ch_0).
    Relative to the anchor, so candidate 0 keeps its own ll (the pick alone cannot tell this from the absolute form: the shift is the same for
    all candidates of the margin, so the test reads ll_eff itself)."""
    mu, alpha, gate = ON if q else OFF
    e = P.eff_ll(sc, col, mu, alpha, gate)
    if e is None:
        return None
    ng, eff = e
    if nu:
        eff = [x + (nu / alpha) * (ch[i] - ch[0]) for i, x in enumerate(eff)]
    return ng, eff, alpha


def final_index(sc, col, ch, nu, q):
    """One cell's final pick: the shipping pick (tau 2, delta 0.25) over `fused` with the cell's alpha."""
    f = fused(sc, col, ch, nu, q)
    return 0 if f is None else triage.pick(f[0], f[1], f[2], P.TAU, P.DELTA)


def evaluate(scores, cols, chs, base_ok, cell):
    """-> (stats, picks); `chs` = channel(terms, c) of the cell's c."""
    nu, _, q = cell
    picks = [final_index(sc, col, ch, nu, q) for sc, col, ch in zip(scores, cols, chs)]
    s = p4c.stats(base_ok, [bool(sc["ok"][j]) for sc, j in zip(scores, picks)])
    s["u2"] = P.u2(s)
    return s, picks


def vs_p4d(scores, picks, p4d_picks):
    """Report only: fixes / breaks of a cell's final ok against the P4d cell's."""
    s = p4c.stats([bool(sc["ok"][j]) for sc, j in zip(scores, p4d_picks)], [bool(sc["ok"][j]) for sc, j in zip(scores, picks)])
    return {k: s[k] for k in ("fixes", "breaks", "net")}


def choose(cells):
    """cells: list of (nu, c, q, stats). Largest U2 among U2 >= 0; ties larger net, smaller nu, larger c, colloquial off first."""
    ok = [c for c in cells if P.u2(c[3]) >= 0]
    return min(ok, key=lambda c: (-P.u2(c[3]), -c[3]["net"], c[0], -c[1], c[2])) if ok else None


def verdict(clause, s):
    """README table at the current standard (ratio limit 0.6). 2b needs the bootstrap and is decided in main."""
    if clause == "1":
        return (s["p"] >= 0.05 or s["net"] >= 0) and (s["breaks"] <= CAP_DISCORD or p4c.ratio_ok(s, RATIO))
    if clause == "2a":
        return s["breaks"] <= 2 and s["net"] >= 0
    if clause == "3":
        return s["net"] > 0 and p4c.ratio_ok(s, RATIO)
    return None


def label(c):
    return f"{c[0]},{c[1]},{'on' if c[2] else 'off'}"


def parse_cell(text):
    try:
        nu, c, q = text.split(",")
        cell = (float(nu), int(c), {"on": True, "off": False}[q])
    except (ValueError, KeyError):
        raise SystemExit("--cell wants NU,C,COLLOQ (COLLOQ on or off)")
    if cell not in GRID and cell not in CONTROLS:
        raise SystemExit("off-grid cell refused")
    return cell


def check_controls(name, scores, cols, base_ok, base, terms):
    """STOP unless nu 0 / off is the CLI's choice and rowstats row by row, nu 0 / on is p4d's cell row by row, and the registered counts hold."""
    P.check_anchor(scores, cols, base_ok, name)
    chs = channel(terms, CS[0])
    s0, p0 = evaluate(scores, cols, chs, base_ok, SHIP)
    bad = [i for i, (sc, j) in enumerate(zip(scores, p0), 1) if "ll" in sc and sc["c"] != j]
    if s0["fixes"] or s0["breaks"] or bad:
        print(f"STOP: nu 0 / off is not shipping: {s0['fixes']}/{s0['breaks']}, pick differs on {len(bad)} rows, first rows {bad[:10]}")
        raise SystemExit(1)
    s1, p1 = evaluate(scores, cols, chs, base_ok, P4D_CELL)
    _, pp = P.evaluate(scores, cols, base_ok, ON)
    bad = [i for i, (a, b) in enumerate(zip(p1, pp), 1) if a != b]
    if bad:
        print(f"STOP: nu 0 / on differs from p4d's cell on {len(bad)} rows, first rows {bad[:10]}")
        raise SystemExit(1)
    if name in P4D_ANCHORS and (s1["fixes"], s1["breaks"]) != P4D_ANCHORS[name]:
        print(f"STOP: nu 0 / on {name}: {s1['fixes']}/{s1['breaks']}, expected {P4D_ANCHORS[name]}")
        raise SystemExit(1)
    return s0, s1, pp


def probe(a, rows, scores, cols, terms):
    """PUBLIC rows only: per row the chosen sentence under shipping, the P4d cell and --cell."""
    cell = parse_cell(a.cell)
    chs = channel(terms, cell[1])
    for i, (r, sc, col, ch) in enumerate(zip(rows, scores, cols, chs), 1):
        pick = lambda nu, q, ch=ch: final_index(sc, col, ch, nu, q)
        out = [r["cands"][pick(nu, q)][0] for nu, q in ((0.0, False), (0.0, True), (cell[0], cell[2]))]
        print(f"row {i} ship {out[0]} | p4d {out[1]} | cell {out[2]}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for k in ("dump", "dump-words", "rowstats", "tinylm-scores", "t2"):
        ap.add_argument("--" + k, required=True)
    ap.add_argument("--rows")
    ap.add_argument("--probe", metavar="ROWS", help="PUBLIC row files only: print per row the chosen sentences (ship, P4d cell, --cell); the only mode that prints text")
    ap.add_argument("--out")
    ap.add_argument("--name", choices=NAMES)
    ap.add_argument("--select", action="store_true")
    ap.add_argument("--cell")
    ap.add_argument("--cell-from")
    ap.add_argument("--clause", choices=("1", "2a", "2b", "3", "report"), default="report")
    ap.add_argument("--out-rowstats")
    ap.add_argument("--out-picks")
    a = ap.parse_args(argv)
    if a.probe:
        if not a.cell:
            raise SystemExit("--probe needs --cell")
        a.rows = a.probe
    else:
        if not (a.rows and a.out and a.name):
            raise SystemExit("--rows, --out and --name are required (except with --probe)")
        if sum(map(bool, (a.select, a.cell, a.cell_from))) != 1:
            raise SystemExit("give exactly one of --select, --cell, --cell-from")
        if a.select and a.name != "chatclause":
            raise SystemExit("--select is the chatclause step")
        if a.clause == "1" and a.name != "discordtune":
            raise SystemExit("--clause 1 is the discordtune clause")
        outf = open(a.out, "w", encoding="utf-8")   # fail now, not after the load
    rows, scores, cols, base_ok, base = P.load_full(a)
    import d23 as D
    terms = row_terms(rows, reading_logp(D.load_lex_scores()[0]))
    if a.probe:
        probe(a, rows, scores, cols, terms)
        return 0
    if a.select and len(base) != P.N_SELECT or a.clause == "1" and len(base) != P.N_DISCORD:
        raise SystemExit(f"{a.name}: unexpected row count {len(base)}")
    s0, s1, p4d_picks = check_controls(a.name, scores, cols, base_ok, base, terms)
    res = {"name": a.name, "n": len(base), "terms": term_counts(terms),
           "controls": {label(SHIP): p4c.jsonable(s0), label(P4D_CELL): p4c.jsonable(s1)}}
    for k, s in res["controls"].items():
        print(f"control {k} fixes {s['fixes']} breaks {s['breaks']} net {s['net']} u2 {s['u2']}")
    print("terms " + " ".join(f"{k} {v}" for k, v in res["terms"].items()))
    by_c = {c: channel(terms, c) for c in CS}

    def run(cell):
        s, picks = evaluate(scores, cols, by_c[cell[1]], base_ok, cell)
        s["vs_p4d"] = vs_p4d(scores, picks, p4d_picks) if cell[2] else None
        return s, picks
    if a.select:
        cells = [(*c, run(c)[0]) for c in GRID]
        for nu, c, q, s in cells:
            print(f"cell {label((nu, c, q))} u2 {s['u2']} {p4c.kv(s)}" + (f" vs_p4d {s['vs_p4d']['fixes']}/{s['vs_p4d']['breaks']}" if q else ""))
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
    else:
        cell = parse_cell(a.cell)
    s, picks = run(cell)
    print(f"cell {label(cell)} u2 {s['u2']} {p4c.kv(s)}" + (f" vs_p4d {s['vs_p4d']['fixes']}/{s['vs_p4d']['breaks']}" if cell[2] else ""))
    res.update({"cell": label(cell), "clause": a.clause, **p4c.jsonable(s)})
    if a.clause == "2b":
        cand = [(bool(sc["ok"][j]), sc["ed"][j], sc["glen"]) for sc, j in zip(scores, picks)]
        (lo, _), _ = evalstats.paired_bootstrap(base, cand, level=0.90)
        pp = 100 * lo / len(base)
        res.update({"verdict": pp >= -1.0, "bootstrap90_lower_rows": lo, "bootstrap90_lower_pp": pp})
        print(f"clause 2b {'PASS' if res['verdict'] else 'FAIL'} lower90_rows {lo} lower90_pp {pp:.3f}")
    elif a.clause != "report":
        res["verdict"] = verdict(a.clause, s)
        print(f"clause {a.clause} {'PASS' if res['verdict'] else 'FAIL'}")
    if a.out_rowstats:
        with open(a.out_rowstats, "w", encoding="utf-8") as f:
            for i, (sc, j) in enumerate(zip(scores, picks), 1):
                f.write(f"{i}\t{int(bool(sc['ok'][j]))}\t{sc['ed'][j]}\t{sc['glen']}\n")
    if a.out_picks:
        with open(a.out_picks, "w", encoding="utf-8") as f:
            for i, j in enumerate(picks, 1):
                f.write(f"{i}\t{j}\n")
    with outf:
        json.dump(res, outf, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
