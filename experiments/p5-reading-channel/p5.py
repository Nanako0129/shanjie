#!/usr/bin/env python3
"""P5 analysis (README of this directory, registration revision 1): a reading-channel term P(reading | word) from the lexicon, added to
the small model's layer on top of P4d's fusion. Counts only on stdout and in the JSON: never a sentence, context, word or character of a
row (only --probe prints sentences, and it is for PUBLIC row files under eval/ or experiments/ only).

  python3 p5.py --name chatclause --rows ROWS --dump D --dump-words W --rowstats R --tinylm-scores S --t2 T2.sjlm --select --out SEL.json
  python3 p5.py --name discordtune ... --cell-from SEL.json --clause 1 [--out-rowstats CELL.rs] [--out-picks PICKS.tsv] --out OUT.json
  python3 p5.py --name dev302 ... --cell EST,NU,C --out OUT.json            (report only: --cell takes no --clause; judging is for the selected cell)
  python3 p5.py --probe PUBLIC_ROWS --dump D --dump-words W --rowstats R --tinylm-scores S --t2 T2.sjlm --cell EST,NU,C

Inputs are one shipping-setting CLI run (--profile chat --context --packs acg --tinylm, plus --dump --dump-words --rowstats
--tinylm-scores), loaded by p4d.load_full (d23 consistency checks, P4's T2 colloquial terms).

Channel term (single-character words only; a multi-character word contributes 0.0 and is not counted). For a candidate, the row's
syllables are cut over its words in order; for a single-character word w read r, both estimators give log10 P(r | w) <= 0 relative to w's
most common reading (that reading and every reading tied with it: exactly 0). A single-character (r, w) the estimator has no entry for
is missing: it counts 0 and is counted. A candidate whose words do not cover the syllables is uncut: ch 0, counted.
  lex: s(r,w) - max_r' s(r',w), s from d23.load_lex_scores (the engine's lexicon), r' over w's readings there.
  mc:  M(r,w) = 10^s(r,w) + sum 10^s(u) over every word u (len >= 2) of data/lexicon/mcbpmf-data.txt, once per position k with u[k] == w and
       u's k-th syllable == r (each (reading, word) of that file once, at its max score, as d23 reads it);
       log10 M(r,w) - max_r' log10 M(r',w), r' over w's readings in the engine lexicon.
  ch_i     = sum over the words of candidate i of max(log10 P, -c).
  ll_eff_i = P4d's eff_ll (ll_i + the colloquial term of the P4d cell, alpha 0.5 mu 0.5 gate 1.0) + (nu / alpha) (ch_i - ch_0),
             then the shipping pick (tau 2, delta 0.25) with alpha 0.5. Candidate 0 is the n-gram first.
Grid: estimator {lex, mc} x nu {0.1, 0.25, 0.5, 1.0} x c {1, 2, 4} = 24 cells, label "EST,NU,C" (e.g. "mc,0.25,2"), all with the colloquial term on.
Controls (not selectable): SHIP = nu 0, colloquial off (alpha 0.3, the shipping fusion); P4D = nu 0, colloquial on.
Baseline: every cell is judged and selected against the P4D control's final ok (p4c.stats(p4d_ok, cell_ok)); the same cell against shipping is
"vs_ship". Selection (chatclause): largest U2 = fixes - 2 breaks against P4D among cells with U2 > 0; ties larger net, smaller nu, larger c,
lex before mc. Judging (--cell-from, --clause) uses the README table with ratio limit 0.6, against P4D; 2b's paired baseline is the P4D
control's (ok, ed, glen) per row. Each clause is bound to its set (1 discordtune, 2a typing76, 2b cvtune-clean, 3 user-reported) and each set
name to its row count; a mismatch STOPs. --cell-from also reports the chosen cell with the colloquial term off against shipping ("off_vs_ship").
Each set name is bound to the SHA-256 of its --rows file (ROW_SHA256) as well as to its row count; the cell label is validated before the load.
--out is checked early (directory writable, not the --cell-from file) and written only at the end, through a temp file and os.replace, so a STOP
never truncates an earlier good JSON; --out-rowstats and --out-picks are written the same way. --out-picks has three columns per row: row,
the P4D control's pick, the cell's pick. The JSON's "cell_source" is "cell" or "cell-from".
--probe takes only files tracked by git under eval/ or experiments/ (never eval/holdout); anything else STOPs.
Exit codes: 0 ok, 1 STOP or bad arguments (a refused --probe file included), 2 argparse usage error, 3 NO CELL QUALIFIES.
"""
import argparse, hashlib, json, math, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "experiments", "p4d-colloquial"))
import p4d as P  # noqa: E402  (puts tools, reference/proto, diag-near-miss, p4c ... on sys.path)
import evalstats  # noqa: E402
import triage  # noqa: E402
import p4c  # noqa: E402

ESTS, NUS, CS = ("lex", "mc"), (0.1, 0.25, 0.5, 1.0), (1, 2, 4)
GRID = [(e, nu, c) for e in ESTS for nu in NUS for c in CS]                              # 24 cells, all with the colloquial term on
ON = P.CONFIRM_CELL                      # colloquial on: P4d's cell (mu 0.5, alpha 0.5, gate 1.0)
OFF = (0.0, 0.3, math.inf)               # colloquial off: (mu, alpha, gate), the shipping fusion
RATIO = 0.6                              # the current standard's breaks / fixes limit (p4c's is 0.5)
CAP_DISCORD = p4c.CAP_DISCORD
ROW_COUNTS = {"chatclause": p4c.N_SELECT, "discordtune": p4c.N_DISCORD, "cvtune-clean": 3286, "typing76": 76, "user-reported": 154, "dev302": 302}
NAMES = tuple(ROW_COUNTS)
CLAUSE_SET = {"1": "discordtune", "2a": "typing76", "2b": "cvtune-clean", "3": "user-reported"}
# SHA-256 of each set's --rows file, so a name cannot be run on another file of the same length. user-reported only grows: the judgement is
# fixed to its first 154 rows, so a later file with more rows STOPs here until the hash is registered anew.
ROW_SHA256 = {"chatclause": "81c035b086f91a0ff15143ed0b2e05f2d6af3b1947ebb2425fc467ff4f0e4203",
              "discordtune": "77e1c90a507fa7e6085c3fca7d377abfd527a5146f43f8c4388e72342627a775",
              "cvtune-clean": "d0960ebec0dc4562f104013951026d91950a73b33f85b10792d967185675d5fb",
              "typing76": "c061f66dfade21996803137320aa986a81a3e72e3e4e1dd559abd5dc04b68d08",
              # 2026-10-22 (main c8842c8e): row 3 reads 一 as ㄧˊ; P5 ran on b78834cb… (reproduce it from a commit before c8842c8e)
              "user-reported": "63f67b724e877bba26b8ddc307f56360dbd599823924303639d366e711a11cef",
              "dev302": "603850e444efee38b8c121e3d666d6c540acd3a27d5a6a5070a78a962dd98456"}
P4D_ANCHORS = {"chatclause": (113, 37), "discordtune": (74, 43), "cvtune-clean": (45, 17), "typing76": (0, 0), "user-reported": (17, 4),
               "dev302": (9, 2)}   # README: the P4d cell against shipping, fixes / breaks
TOP_K = 8


def stop(msg):
    print(f"STOP: {msg}")
    raise SystemExit(1)


# ---------------------------------------------------------------- estimators (single-character words only)

def lex_logp(lex):
    """{((r,), w): log10 P(r|w)} for single-character w from d23.load_lex_scores' {(reading tuple, word): score}: s(r,w) - max_r' s(r',w)."""
    by_word = {}
    for (r, w), s in lex.items():
        if len(w) == 1:
            by_word.setdefault(w, []).append((r, s))
    return {(r, w): s - max(t for _, t in rs) for w, rs in by_word.items() for r, s in rs}


def mc_logp(lex, mcb):
    """{((r,), w): log10 P(r|w)} for single-character w: M(r,w) = 10^s(r,w) + the mass of the multi-character words of `mcb` (the
    {(reading tuple, word): score} of mcbpmf-data.txt alone) that use w at a position read r, relative to w's best M over its readings in `lex`."""
    extra = {}
    for (r, u), s in mcb.items():
        if len(u) >= 2:
            for k, ch in enumerate(u):
                extra[(r[k], ch)] = extra.get((r[k], ch), 0.0) + 10.0 ** s
    by_word = {}
    for (r, w), s in lex.items():
        if len(w) == 1:
            by_word.setdefault(w, []).append((r, math.log10(10.0 ** s + extra.get((r[0], w), 0.0))))
    return {(r, w): m - max(t for _, t in rs) for w, rs in by_word.items() for r, m in rs}


def load_mcbpmf():
    """{(reading tuple, word): max score} of data/lexicon/mcbpmf-data.txt, parsed by d23.mcbpmf_entries (the parser load_lex_scores uses)."""
    import d23 as D
    out = {}
    for reading, word, sc in D.mcbpmf_entries():
        k = (tuple(reading.split("-")), word)
        out[k] = max(out.get(k, -1e9), sc)
    return out


def word_terms(syls, words, logp):
    """[term per word of one candidate], the syllables cut in order (one per character): 0.0 for a multi-character word, log10 P(r|w) for a
    single-character one, None for a single-character (r, w) the estimator has no entry for. None (not a list) when the words do not cover
    the row's syllables exactly (an uncut candidate: ch 0)."""
    if words is None or sum(len(w) for w in words) != len(syls):
        return None
    out, p = [], 0
    for w in words:
        out.append(logp.get(((syls[p],), w)) if len(w) == 1 else 0.0)
        p += len(w)
    return out


def ch_of(terms, c):
    """Sum of max(term, -c); a missing pair counts 0, an uncut candidate (None) is 0."""
    return 0.0 if terms is None else sum(max(t, -c) for t in terms if t is not None)


def row_terms(rows, logp):
    """[[word_terms of candidate j for j < 8] per row], from each candidate's own words (cand[2])."""
    return [[word_terms(r["syls"], cand[2], logp) for cand in r["cands"][:TOP_K]] for r in rows]


def channel(terms, c):
    return [[ch_of(t, c) for t in row] for row in terms]


def term_counts(rows, scores, terms):
    """Report only, over the candidates that enter the pick (rows with `ll`, candidates j < min(8, len(ng), len(ll))): candidates, uncut
    candidates, single-character slots, single-character pairs missing from the estimator, and for each c the slots below -c."""
    cands = uncut = slots = missing = 0
    clipped = dict.fromkeys(CS, 0)
    for r, sc, trow in zip(rows, scores, terms):
        if "ll" not in sc:
            continue
        for j in range(min(TOP_K, len(sc["ng"]), len(sc["ll"]), len(trow))):
            cands += 1
            if trow[j] is None:
                uncut += 1
                continue
            for w, x in zip(r["cands"][j][2], trow[j]):
                if len(w) != 1:
                    continue
                slots += 1
                if x is None:
                    missing += 1
                else:
                    for c in CS:
                        clipped[c] += x < -c
    return {"candidates": cands, "uncut_candidates": uncut, "single_char_slots": slots, "missing_pairs": missing,
            "below_minus_c": {str(c): v for c, v in clipped.items()}}


# ---------------------------------------------------------------- fusion and picks

def fused(sc, col, ch, nu, q):
    """-> (ng, ll_eff, alpha) of one cell, or None for a row without `ll`: P4d's eff_ll (the colloquial term when q) + (nu / alpha) (ch_i - ch_0).
    Relative to the n-gram first, so candidate 0 keeps its own ll."""
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


def ok_of(scores, picks):
    return [bool(sc["ok"][j]) for sc, j in zip(scores, picks)]


def stats_vs(base_ok, scores, picks):
    s = p4c.stats(base_ok, ok_of(scores, picks))
    s["u2"] = P.u2(s)
    return s


def short(s):
    return {k: s[k] for k in ("fixes", "breaks", "net")}


def choose(cells):
    """cells: list of (est, nu, c, stats against P4D). Largest U2 among U2 > 0; ties larger net, smaller nu, larger c, lex before mc."""
    ok = [c for c in cells if P.u2(c[3]) > 0]
    return min(ok, key=lambda c: (-P.u2(c[3]), -c[3]["net"], c[1], -c[2], ESTS.index(c[0]))) if ok else None


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
    return f"{c[0]},{c[1]},{c[2]}"


def parse_cell(text):
    try:
        e, nu, c = text.split(",")
        cell = (e, float(nu), int(c))
    except ValueError:
        raise SystemExit("--cell wants EST,NU,C (e.g. mc,0.25,2)")
    if cell not in GRID:
        raise SystemExit("off-grid cell refused")
    return cell


# ---------------------------------------------------------------- checks

def check_count(name, n):
    if n != ROW_COUNTS[name]:
        stop(f"{name}: {n} rows, expected {ROW_COUNTS[name]}")


def check_clause(clause, name):
    if clause in CLAUSE_SET and CLAUSE_SET[clause] != name:
        stop(f"clause {clause} is the {CLAUSE_SET[clause]} clause, not {name}")


def check_controls(name, scores, cols, base_ok):
    """STOP unless the shipping setting reproduces the CLI (P4d's check_anchor: pick and rowstats, and the setting anchors it knows) and the
    P4D control's fixes / breaks against shipping equal P4D_ANCHORS. -> (P4D stats, P4D picks)."""
    P.check_anchor(scores, cols, base_ok, name)
    s, picks = P.evaluate(scores, cols, base_ok, ON)
    if (s["fixes"], s["breaks"]) != P4D_ANCHORS[name]:
        stop(f"the P4D control on {name}: {s['fixes']}/{s['breaks']}, expected {P4D_ANCHORS[name]}")
    return s, picks


def check_rows_sha(name, path):
    with open(path, "rb") as f:
        h = hashlib.sha256(f.read()).hexdigest()
    if h != ROW_SHA256[name]:
        stop(f"{name}: the --rows file's sha256 is not the registered one")


def read_selection(path):
    """The chosen cell of a --select JSON; STOP unless it is the chatclause selection and chose a cell."""
    with open(path, encoding="utf-8") as f:
        sel = json.load(f)
    if sel.get("name") != "chatclause":
        stop(f"--cell-from: {path} is not the chatclause selection")
    if not sel.get("chosen"):
        stop("--cell-from: the selection chose no cell")
    return sel["chosen"]


def public_path(path):
    """--probe prints sentences: only files tracked by git under eval/ or experiments/, never eval/holdout; anything else is a STOP."""
    p, root = os.path.realpath(path), os.path.realpath(ROOT)
    under = lambda d: os.path.commonpath([p, os.path.join(root, d)]) == os.path.join(root, d)
    if under("eval/holdout"):
        stop(f"--probe refuses the holdout: {p}")
    if not (under("eval") or under("experiments")):
        stop(f"--probe prints sentences and takes only public row files under {root}/eval or {root}/experiments, not {p}")
    if subprocess.run(["git", "-C", root, "ls-files", "--error-unmatch", os.path.relpath(p, root)], capture_output=True).returncode:
        stop(f"--probe takes only files tracked by git, not {p}")


def check_out(out, cell_from):
    """Fail now: --out's directory must exist and be writable, and it must not be the --cell-from file. Nothing is written yet."""
    d = os.path.dirname(os.path.abspath(out))
    if not (os.path.isdir(d) and os.access(d, os.W_OK)):
        raise SystemExit(f"--out: {d} is not a writable directory")
    if cell_from and os.path.realpath(out) == os.path.realpath(cell_from):
        raise SystemExit("--out is the --cell-from file")


def write_atomic(path, text):
    """Temp file in the same directory, then os.replace: a STOP or crash before this leaves an earlier file untouched."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), prefix=".p5-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def check_args(a):
    """Argument rules of the non-probe modes (SystemExit with a message, or a STOP)."""
    if not (a.rows and a.out and a.name):
        raise SystemExit("--rows, --out and --name are required (except with --probe)")
    if sum(map(bool, (a.select, a.cell, a.cell_from))) != 1:
        raise SystemExit("give exactly one of --select, --cell, --cell-from")
    if a.select:
        if a.name != "chatclause":
            raise SystemExit("--select is the chatclause step")
        if a.clause != "report" or a.out_rowstats or a.out_picks:
            raise SystemExit("--select takes no --clause, --out-rowstats or --out-picks")
    if a.cell and a.clause != "report":
        raise SystemExit("--cell is report only; judging (--clause) is for the selected cell (--cell-from)")
    check_clause(a.clause, a.name)


def run_cell(scores, cols, by_c, p4d_ok, base_ok, cell, q=True):
    """-> (stats of the cell against the P4D control's final ok (p4d_ok) with `vs_ship` (against base_ok, the shipping ok), picks).
    q False: the colloquial term off (the report-only off_vs_ship)."""
    e, nu, c = cell
    picks = [final_index(sc, col, ch, nu, q) for sc, col, ch in zip(scores, cols, by_c[e, c])]
    s = stats_vs(p4d_ok, scores, picks)
    s["vs_ship"] = short(stats_vs(base_ok, scores, picks))
    return s, picks


def off_vs_ship(scores, cols, by_c, p4d_ok, base_ok, cell):
    """Report only: the cell with the colloquial term OFF against shipping."""
    return run_cell(scores, cols, by_c, p4d_ok, base_ok, cell, q=False)[0]["vs_ship"]


def bootstrap_rows(scores, picks):
    """[(ok, errors, gold length)] per row of the final picks: the rowstats shape paired_bootstrap reads."""
    return [(int(bool(sc["ok"][j])), sc["ed"][j], sc["glen"]) for sc, j in zip(scores, picks)]


def clause_2b(scores, p4d_picks, picks):
    """Clause 2b: the 90% paired bootstrap lower bound of the cell's top1 change against the P4D control (not shipping). -> (verdict, rows, pp)."""
    (lo, _), _ = evalstats.paired_bootstrap(bootstrap_rows(scores, p4d_picks), bootstrap_rows(scores, picks), level=0.90)
    pp = 100 * lo / len(scores)
    return pp >= -1.0, lo, pp


def picks_text(p4d_picks, picks):
    """--out-picks: row, the P4D control's pick, the cell's pick (so the cell's breaks against P4D can be labelled)."""
    return "".join(f"{i}\t{p}\t{j}\n" for i, (p, j) in enumerate(zip(p4d_picks, picks), 1))


def probe(a, rows, scores, cols, terms, base_ok):
    """PUBLIC rows only: per row the chosen sentence under shipping, the P4D control and --cell."""
    est, nu, c = parse_cell(a.cell)
    P.check_anchor(scores, cols, base_ok)
    ch = channel(terms[est], c)
    for i, (r, sc, col, h) in enumerate(zip(rows, scores, cols, ch), 1):
        out = [r["cands"][final_index(sc, col, h, n, q)][0] for n, q in ((0.0, False), (0.0, True), (nu, True))]
        print(f"row {i} ship {out[0]} | p4d {out[1]} | cell {out[2]}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for k in ("dump", "dump-words", "rowstats", "tinylm-scores", "t2"):
        ap.add_argument("--" + k, required=True)
    ap.add_argument("--rows")
    ap.add_argument("--probe", metavar="ROWS", help="PUBLIC row files only: print per row the chosen sentences (ship, P4D, --cell); the only mode that prints text")
    ap.add_argument("--out")
    ap.add_argument("--name", choices=NAMES)
    ap.add_argument("--select", action="store_true")
    ap.add_argument("--cell")
    ap.add_argument("--cell-from")
    ap.add_argument("--clause", choices=("1", "2a", "2b", "3", "report"), default="report")
    ap.add_argument("--out-rowstats")
    ap.add_argument("--out-picks")
    a = ap.parse_args(argv)
    cell = None
    if a.probe:
        if not a.cell:
            raise SystemExit("--probe needs --cell")
        parse_cell(a.cell)
        public_path(a.probe)
        a.rows = a.probe
    else:
        check_args(a)
        if not a.select:
            cell = parse_cell(read_selection(a.cell_from) if a.cell_from else a.cell)
        check_out(a.out, a.cell_from)
        check_rows_sha(a.name, a.rows)
    rows, scores, cols, base_ok, base = P.load_full(a)
    import d23 as D
    lex = D.load_lex_scores()[0]
    logp = {"lex": lex_logp(lex), "mc": mc_logp(lex, load_mcbpmf())}
    terms = {e: row_terms(rows, logp[e]) for e in ESTS}
    if a.probe:
        probe(a, rows, scores, cols, terms, base_ok)
        return 0
    check_count(a.name, len(base))
    s_p4d, p4d_picks = check_controls(a.name, scores, cols, base_ok)
    p4d_ok = ok_of(scores, p4d_picks)
    res = {"name": a.name, "n": len(base), "controls": {"P4D": p4c.jsonable(s_p4d)},
           "terms": {e: term_counts(rows, scores, terms[e]) for e in ESTS}}
    print(f"control P4D (vs ship) fixes {s_p4d['fixes']} breaks {s_p4d['breaks']} net {s_p4d['net']} u2 {s_p4d['u2']}")
    for e, t in res["terms"].items():
        print(f"terms {e} " + " ".join(f"{k} {v}" for k, v in t.items()))
    by_c = {(e, c): channel(terms[e], c) for e in ESTS for c in CS}
    run = lambda c: run_cell(scores, cols, by_c, p4d_ok, base_ok, c)

    def line(c, s):
        return f"cell {label(c)} u2 {s['u2']} {p4c.kv(s)} vs_ship {s['vs_ship']['fixes']}/{s['vs_ship']['breaks']}"
    if a.select:
        cells = [(*c, run(c)[0]) for c in GRID]
        for e, nu, c, s in cells:
            print(line((e, nu, c), s))
        best = choose(cells)
        print(label(best[:3]) if best else "NO CELL QUALIFIES")
        res["cells"] = {label(c[:3]): p4c.jsonable(c[3]) for c in cells}
        res["chosen"] = label(best[:3]) if best else None
        write_atomic(a.out, json.dumps(res, indent=1))
        return 0 if best else 3
    s, picks = run(cell)
    print(line(cell, s))
    res.update({"cell": label(cell), "cell_source": "cell-from" if a.cell_from else "cell", "clause": a.clause, **p4c.jsonable(s)})
    res["off_vs_ship"] = off = off_vs_ship(scores, cols, by_c, p4d_ok, base_ok, cell)
    print(f"off_vs_ship (colloquial off, 只報) {off['fixes']}/{off['breaks']} net {off['net']}")
    if a.clause == "2b":
        res["verdict"], lo, pp = clause_2b(scores, p4d_picks, picks)
        res.update({"bootstrap90_lower_rows": lo, "bootstrap90_lower_pp": pp})
        print(f"clause 2b {'PASS' if res['verdict'] else 'FAIL'} lower90_rows {lo} lower90_pp {pp:.3f}")
    elif a.clause != "report":
        res["verdict"] = verdict(a.clause, s)
        print(f"clause {a.clause} {'PASS' if res['verdict'] else 'FAIL'}")
    if a.out_rowstats:
        write_atomic(a.out_rowstats, "".join(f"{i}\t{int(bool(sc['ok'][j]))}\t{sc['ed'][j]}\t{sc['glen']}\n" for i, (sc, j) in enumerate(zip(scores, picks), 1)))
    if a.out_picks:
        write_atomic(a.out_picks, picks_text(p4d_picks, picks))
    write_atomic(a.out, json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
