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

SUB-0 (experiments/sub0-subtitles/README.md; `--sub0`, its own argument set, see sub0_main; the modes above are unchanged):
  python3 p5.py --sub0 --arm T --name chatclause --rows R --dump D --dump-words W --rowstats RS --tinylm-scores S --t2 T2.sjlm \
      --table T3-half PATH SHA256 --table T3-1 PATH SHA256 --select --out SEL.json
  python3 p5.py --sub0 --arm A --arm-label A1 --name chatclause ... --exclude ROWS.txt \
      --cand A1 SEED D W S (x3) --cand A0 SEED D W S (x3) --select --out SEL.json
  python3 p5.py --sub0 --arm A --name discordtune ... --exclude ROWS.txt --cand A1 SEED D W S [--cand ...] --cell-from SEL.json --clause 1 --out OUT.json
The reading channel is fixed at lex / 1.0 / 4, the grid is P4d's 24 cells (mu, alpha, gate), delta 0.25, tau 2. Everything is compared with C0 =
the P4d cell (0.5, 0.5, T 1.0) + the channel, computed from the shipped-weight run with T2 (the anchors run only there). --exclude FILE (1-based
row numbers, digits and newlines only; its SHA-256 is printed) drops rows from selection, counts and rowstats; the anchors run on all rows first.
"""
import argparse, hashlib, json, math, os, re, subprocess, sys, tempfile

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
              "user-reported": "b78834cb3aab2ad2d37b66f887000bfa6948d7558a46c928bb62a879a95efeae",
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


# ---------------------------------------------------------------- SUB-0 (experiments/sub0-subtitles/README.md)

SUB0_CH = ("lex", 1.0, 4)                # the reading channel, fixed: estimator, nu, c
TABLE_LABELS = ("T3-half", "T3-1")       # README tie-break order: the half-subtitle table before the full one
A_LABELS = ("A0", "A1", "A2")            # A0 is A0': the noise control, never selected
N_SEEDS = 3
GATE_ORDER = {P.INF: 0, 1.0: 1, 0.5: 2}  # none, T 1.0, T 0.5
C0_ANCHORS = {"chatclause": (126, 27), "discordtune": (88, 34), "cvtune-clean": (49, 12)}   # README anchor 2 (P5's record): C0 against shipping
N_EXCLUDE_DISCORD = 865                  # README: discordtune's overlap with chat-a2.txt; anything else STOPs


def sub0_pick(sc, col, ch, cell):
    """One SUB-0 cell's final pick: P4d's eff_ll at (mu, alpha, gate) + the channel (nu 1.0 / alpha) (ch_i - ch_0), then the shipping pick."""
    mu, alpha, gate = cell
    e = P.eff_ll(sc, col, mu, alpha, gate)
    if e is None:
        return 0
    ng, eff = e
    eff = [x + (SUB0_CH[1] / alpha) * (ch[i] - ch[0]) for i, x in enumerate(eff)]
    return triage.pick(ng, eff, alpha, P.TAU, P.DELTA)


def sub0_picks(scores, cols, ch, cell):
    return [sub0_pick(sc, col, h, cell) for sc, col, h in zip(scores, cols, ch)]


def grid_stats(c0_ok, scores, cols, ch):
    """{cell: stats against C0 (with u2)} over P4d's 24 cells."""
    return {cell: stats_vs(c0_ok, scores, sub0_picks(scores, cols, ch, cell)) for cell in P.GRID}


def cell_key(cell):
    """Ties after U2 / net: mu small, gate none, T 1.0, T 0.5, then alpha 0.3 first."""
    return (cell[0], GATE_ORDER[cell[2]], abs(cell[1] - 0.3))


def choose_t(by_table):
    """by_table {label: {cell: stats}} -> (label, cell, stats) or None. Largest U2 among U2 > 0; ties larger net, T3-half before T3-1, then cell_key."""
    ok = [(t, c, s) for t, g in by_table.items() for c, s in g.items() if s["u2"] > 0]
    return min(ok, key=lambda x: (-x[2]["u2"], -x[2]["net"], TABLE_LABELS.index(x[0]), cell_key(x[1]))) if ok else None


def median_seed(u2_by_seed):
    """-> (the seed whose U2 is the median of the three, whether another seed has the same U2). The tie is not specified by the README: the
    lowest seed number wins and the tie is reported."""
    med = sorted(u2_by_seed.values())[1]
    seeds = sorted(k for k, v in u2_by_seed.items() if v == med)
    return seeds[0], len(seeds) > 1


def choose_a(by_seed):
    """by_seed {seed: {cell: stats}} (three seeds) -> (cell, median U2, min U2, judged seed, median tie) or None. Only cells whose three U2 are
    all > 0; the largest median U2; ties the larger minimum, then cell_key."""
    if len(by_seed) != N_SEEDS:
        stop(f"the A selection wants {N_SEEDS} seeds, got {len(by_seed)}")
    rows = []
    for cell in P.GRID:
        u = {sd: g[cell]["u2"] for sd, g in by_seed.items()}
        if min(u.values()) > 0:
            rows.append((cell, sorted(u.values())[1], min(u.values()), u))
    if not rows:
        return None
    cell, med, lo, u = min(rows, key=lambda r: (-r[1], -r[2], cell_key(r[0])))
    sd, tie = median_seed(u)
    return cell, med, lo, sd, tie


def condition0(a_u2, a0_u2):
    """README condition 0: the arm's smallest U2 in the cell beats A0''s largest in the same cell."""
    return min(a_u2.values()) > max(a0_u2.values())


def read_exclude(path, n):
    """-> (set of 0-based rows, sha256 of the file). The file is digits and newlines only: 1-based, distinct, within 1..n."""
    with open(path, "rb") as f:
        raw = f.read()
    if re.search(rb"[^0-9\n]", raw):
        stop("--exclude: the file has bytes other than digits and newlines")
    nums = [int(x) for x in raw.split()]
    if len(set(nums)) != len(nums) or any(not 1 <= x <= n for x in nums):
        stop(f"--exclude: row numbers must be distinct and within 1..{n}")
    return {x - 1 for x in nums}, hashlib.sha256(raw).hexdigest()


def check_exclude(name, drop):
    if name == "discordtune" and len(drop) != N_EXCLUDE_DISCORD:
        stop(f"--exclude on discordtune has {len(drop)} rows, expected {N_EXCLUDE_DISCORD}")


def keep_rows(lst, drop):
    return [x for i, x in enumerate(lst) if i not in drop]


def sha_of(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def load_table_cols(ns, path, sha):
    """The colloquial column of the shipped run under another table: STOP unless the file's SHA-256 is the one given on the command line."""
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        stop("a table's SHA-256 must be 64 lowercase hex digits")
    if sha_of(path) != sha:
        stop(f"the table {os.path.basename(path)} does not have the SHA-256 given on the command line")
    return P.load_full(argparse.Namespace(**{**vars(ns), "t2": path}), t2_sha256=sha)[2]


def load_cand(ns, dump, words, scores_path, ship_scores):
    """A candidate-weight run's score records. STOP unless its dump and dump-words equal the shipped run's byte for byte (so the candidates, the
    n-gram scores and the right / wrong marks are the same and only ll differs)."""
    for mine, ship, what in ((dump, ns.dump, "dump"), (words, ns.dump_words, "dump-words")):
        if sha_of(mine) != sha_of(ship):
            stop(f"the candidate run's {what} differs from the shipped run's")
    recs = triage.read_cli_files(dump, words, scores_path, len(ship_scores))[2]
    for i, (r, s) in enumerate(zip(recs, ship_scores), 1):
        if r is None or ("ll" in r) != ("ll" in s) or any(r.get(k) != s.get(k) for k in ("ng", "ok", "ed", "glen")):
            stop(f"row {i}: the candidate run's scores disagree with the shipped run beyond ll")
    return recs


def sub0_clause(clause, s, scores, c0_picks, picks):
    if clause == "2b":
        v, lo, pp = clause_2b(scores, c0_picks, picks)
        return {"verdict": v, "bootstrap90_lower_rows": lo, "bootstrap90_lower_pp": pp}
    return {} if clause == "report" else {"verdict": verdict(clause, s)}


def read_sub0_selection(path, arm, exclude_sha):
    with open(path, encoding="utf-8") as f:
        sel = json.load(f)
    if sel.get("name") != "chatclause" or sel.get("sub0_arm") != arm:
        stop(f"--cell-from: {path} is not the chatclause selection of arm {arm}")
    if not sel.get("chosen"):
        stop("--cell-from: the selection chose no cell")
    if sel.get("exclude_sha256") != exclude_sha:
        stop("--cell-from: the selection was made with another --exclude file")
    return sel["chosen"]


def sub0_parser():
    ap = argparse.ArgumentParser(description="SUB-0 modes of p5.py (see the module docstring)")
    ap.add_argument("--sub0", action="store_true", required=True)
    ap.add_argument("--arm", choices=("T", "A"), required=True)
    ap.add_argument("--arm-label", choices=("A1", "A2"), help="A arm, --select: the arm being selected (A0 is its noise control)")
    for k in ("name", "rows", "out", "dump", "dump-words", "rowstats", "tinylm-scores", "t2"):
        ap.add_argument("--" + k, required=True)
    ap.add_argument("--table", nargs=3, action="append", default=[], metavar=("LABEL", "PATH", "SHA256"))
    ap.add_argument("--cand", nargs=5, action="append", default=[], metavar=("LABEL", "SEED", "DUMP", "WORDS", "SCORES"))
    ap.add_argument("--exclude")
    ap.add_argument("--select", action="store_true")
    ap.add_argument("--cell-from")
    ap.add_argument("--clause", choices=("1", "2a", "2b", "3", "report"), default="report")
    ap.add_argument("--out-rowstats")
    ap.add_argument("--out-picks")
    return ap


def sub0_args(a):
    """Argument rules (SystemExit with a message, or a STOP). -> {label: path, sha} of the tables / {(label, seed): (dump, words, scores)} of the candidates."""
    if a.name not in NAMES:
        raise SystemExit(f"--name: one of {', '.join(NAMES)}")
    if bool(a.select) == bool(a.cell_from):
        raise SystemExit("give exactly one of --select, --cell-from")
    if a.select and (a.name != "chatclause" or a.clause != "report" or a.out_rowstats or a.out_picks):
        raise SystemExit("--select is the chatclause step and takes no --clause, --out-rowstats or --out-picks")
    check_clause(a.clause, a.name)
    tables, cands = {}, {}
    for label, path, sha in a.table:
        if label not in TABLE_LABELS or label in tables:
            raise SystemExit(f"--table: label {label} is unknown or given twice")
        tables[label] = (path, sha)
    for label, seed, d, w, s in a.cand:
        if label not in A_LABELS or not seed.isdigit() or (label, int(seed)) in cands:
            raise SystemExit(f"--cand: {label} {seed} is unknown or given twice")
        cands[label, int(seed)] = (d, w, s)
    if a.arm == "T" and (cands or a.exclude or not tables or a.arm_label):
        raise SystemExit("arm T takes --table (and no --cand, --exclude or --arm-label)")
    if a.arm == "A" and (tables or not cands):
        raise SystemExit("arm A takes --cand (and no --table)")
    if a.select and a.arm == "T" and set(tables) != set(TABLE_LABELS):
        raise SystemExit("the T selection wants both tables")
    if a.select and a.arm == "A":
        if not a.arm_label:
            raise SystemExit("the A selection wants --arm-label A1 or A2")
        for label in (a.arm_label, "A0"):
            if len([k for k in cands if k[0] == label]) != N_SEEDS:
                raise SystemExit(f"the A selection wants {N_SEEDS} seeds of {label}")
        if any(k[0] not in (a.arm_label, "A0") for k in cands):
            raise SystemExit("the A selection takes only the arm and A0 candidates")
    return tables, cands


def run_inputs(ctx, tag):
    """(score records, colloquial columns) of one run over all rows: a T3 table's columns on the shipped scores, or a candidate's scores."""
    if ctx.a.arm == "T":
        return ctx.scores, load_table_cols(ctx.ns, *ctx.tables[tag])
    return load_cand(ctx.ns, *ctx.cands[tag], ctx.scores), ctx.cols


def tag_name(tag):
    return tag if isinstance(tag, str) else f"{tag[0]}/{tag[1]}"


def sub0_main(argv):
    a = sub0_parser().parse_args(argv)
    tables, cands = sub0_args(a)
    check_out(a.out, a.cell_from)
    check_rows_sha(a.name, a.rows)
    ns = argparse.Namespace(rows=a.rows, dump=a.dump, dump_words=a.dump_words, rowstats=a.rowstats, tinylm_scores=a.tinylm_scores, t2=a.t2)
    rows, scores, cols, base_ok, base = P.load_full(ns)
    check_count(a.name, len(base))
    s_p4d, _ = check_controls(a.name, scores, cols, base_ok)          # the anchors: shipped weights, T2, all rows
    import d23 as D
    ch = channel(row_terms(rows, lex_logp(D.load_lex_scores()[0])), SUB0_CH[2])
    c0_picks = sub0_picks(scores, cols, ch, P.CONFIRM_CELL)
    c0_ok = ok_of(scores, c0_picks)
    c0 = stats_vs(base_ok, scores, c0_picks)
    if a.name in C0_ANCHORS and (c0["fixes"], c0["breaks"]) != C0_ANCHORS[a.name]:
        stop(f"C0 on {a.name}: {c0['fixes']}/{c0['breaks']} against shipping, expected {C0_ANCHORS[a.name]}")
    drop, ex_sha = set(), None
    if a.exclude:
        drop, ex_sha = read_exclude(a.exclude, len(base))
        check_exclude(a.name, drop)
        print(f"exclude sha256 {ex_sha} rows {len(drop)}")
    ctx = argparse.Namespace(a=a, ns=ns, tables=tables, cands=cands, scores=scores, cols=cols, ch=ch, c0_ok=c0_ok, drop=drop, ex_sha=ex_sha,
                             K=lambda lst: keep_rows(lst, drop))
    ctx.ch_k, ctx.c0_ok_k, ctx.c0_picks_k = ctx.K(ch), ctx.K(c0_ok), ctx.K(c0_picks)
    ctx.res = {"sub0": True, "sub0_arm": a.arm, "name": a.name, "n": len(base), "n_kept": len(ctx.ch_k), "exclude_sha256": ex_sha,
               "c0_vs_ship": p4c.jsonable(c0), "controls": {"P4D": p4c.jsonable(s_p4d)}}
    print(f"n {len(base)} kept {len(ctx.ch_k)} C0 (vs ship) fixes {c0['fixes']} breaks {c0['breaks']} net {c0['net']}; "
          f"P4D fixes {s_p4d['fixes']} breaks {s_p4d['breaks']}")
    return (sub0_select if a.select else sub0_judge)(ctx)


def cell_line(tag, cell, s):
    return f"{tag_name(tag)} cell {P.label(cell)} u2 {s['u2']} {p4c.kv(s)}"


def sub0_select(ctx):
    """The chatclause selection of one arm: T over both tables, or A1 / A2 over its three seeds with condition 0 against A0'."""
    a, res = ctx.a, ctx.res
    grids = {}
    for tag in (TABLE_LABELS if a.arm == "T" else sorted(ctx.cands)):
        sc, cl = run_inputs(ctx, tag)
        grids[tag] = grid_stats(ctx.c0_ok_k, ctx.K(sc), ctx.K(cl), ctx.ch_k)
        for cell, s in grids[tag].items():
            print(cell_line(tag, cell, s))
    res["cells"] = {tag_name(t): {P.label(c): p4c.jsonable(s) for c, s in g.items()} for t, g in grids.items()}
    chosen = None
    if a.arm == "T":
        best = choose_t(grids)
        if best:
            chosen = {"arm": "T", "table": best[0], "table_sha256": ctx.tables[best[0]][1], "cell": P.label(best[1])}
            print(f"chosen {best[0]} {P.label(best[1])} u2 {best[2]['u2']}")
    else:
        best = choose_a({sd: g for (lab, sd), g in grids.items() if lab == a.arm_label})
        if best:
            cell, med, lo, sd, tie = best
            a0 = {sd2: g[cell]["u2"] for (lab, sd2), g in grids.items() if lab == "A0"}
            arm_u2 = {sd2: g[cell]["u2"] for (lab, sd2), g in grids.items() if lab == a.arm_label}
            res["condition0"] = {"arm_u2": arm_u2, "a0_u2": a0, "pass": condition0(arm_u2, a0)}
            print(f"best cell {P.label(cell)} median u2 {med} min u2 {lo} judged seed {sd}{' (median tie: lowest seed)' if tie else ''}; "
                  f"condition 0 {'PASS' if res['condition0']['pass'] else 'FAIL'} (arm min {min(arm_u2.values())}, A0' max {max(a0.values())})")
            if res["condition0"]["pass"]:
                chosen = {"arm": "A", "label": a.arm_label, "seed": sd, "cell": P.label(cell), "median_tie": tie}
    res["chosen"] = chosen
    print("NO CELL QUALIFIES" if not chosen else f"chosen {chosen['cell']}")
    write_atomic(a.out, json.dumps(res, indent=1))
    return 0 if chosen else 3


def sub0_judge(ctx):
    """One set, the selected cell, every clause against C0. A arm: the judged seed plus every other --cand run (A0', other seeds) as report only."""
    a, res = ctx.a, ctx.res
    sel = read_sub0_selection(a.cell_from, a.arm, ctx.ex_sha)
    cell = P.parse_cell(sel["cell"])
    if a.arm == "T":
        if sel["table"] not in ctx.tables or ctx.tables[sel["table"]][1] != sel["table_sha256"]:
            stop(f"the selection's table {sel['table']} is not given, or with another SHA-256")
        tags, judged = [sel["table"]], sel["table"]
    else:
        judged = (sel["label"], sel["seed"])
        if judged not in ctx.cands:
            stop(f"the selection's candidate {sel['label']} {sel['seed']} is not given")
        tags = sorted(ctx.cands)
    res.update({"cell": sel["cell"], "clause": a.clause, "chosen": sel, "runs": {}})
    for tag in tags:
        sc, cl = run_inputs(ctx, tag)
        sc_k, cl_k = ctx.K(sc), ctx.K(cl)
        picks = sub0_picks(sc_k, cl_k, ctx.ch_k, cell)
        s = stats_vs(ctx.c0_ok_k, sc_k, picks)
        r = {**p4c.jsonable(s), "judged": tag == judged, **sub0_clause(a.clause, s, sc_k, ctx.c0_picks_k, picks)}
        if ctx.drop:                      # the whole set, overlap rows included: reported only
            r["whole_set"] = short(stats_vs(ctx.c0_ok, sc, sub0_picks(sc, cl, ctx.ch, cell)))
        res["runs"][tag_name(tag)] = r
        text = cell_line(tag, cell, s) + (" JUDGED" if tag == judged else " report")
        if "verdict" in r:
            text += f" clause {a.clause} {'PASS' if r['verdict'] else 'FAIL'}" + (f" lower90_pp {r['bootstrap90_lower_pp']:.3f}" if a.clause == "2b" else "")
        print(text)
        if tag == judged:
            if a.out_rowstats:
                write_atomic(a.out_rowstats, "".join(f"{i}\t{int(bool(x['ok'][j]))}\t{x['ed'][j]}\t{x['glen']}\n" for i, (x, j) in enumerate(zip(sc_k, picks), 1)))
            if a.out_picks:
                write_atomic(a.out_picks, picks_text(ctx.c0_picks_k, picks))
    write_atomic(a.out, json.dumps(res, indent=1))
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if "--sub0" in argv:
        return sub0_main(argv)
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
