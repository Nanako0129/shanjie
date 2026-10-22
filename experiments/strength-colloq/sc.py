#!/usr/bin/env python3
"""strength-colloq offline tool (README of this directory, registered 5296ae0c before any number): the P4d colloquial term and the P5
reading-channel term at the chat conservative and aggressive levels. Counts, numbers and per-row 0/1 only, on stdout and in the JSON: never
a sentence, context, word or character of a row (row numbers are allowed).

  sc.py run --name SET --rows R --sjtl data/lm/tinylm.sjtl --t2 data/lm/colloquial.sjlm
            --dump D --dump-words W --rowstats RS --tinylm-scores SC --summary SUM            (the chat standard shipping run)
            --cons-rowstats RS --cons-scores SC --cons-summary SUM --aggr-rowstats RS --aggr-scores SC --aggr-summary SUM
            (--cells | --sel SEL.json) --out OUT.json
  sc.py select CHATCLAUSE_CELLS.json --out SEL.json
  sc.py final  SEL.json RUN.json ... [--out FINAL.json]       (discordtune typing76 cvtune-clean user-reported dev302)

Three CLI runs of one set: chat standard (the shipping setting: --colloquial-core, so the colloquial and reading-channel terms ride on it) and
chat conservative / aggressive (--rerank-level; there `colloquial::applies` is false, so the CLI's pick is the plain `pick` with the .sjtl
header triple). The candidates and the small model's ll do not depend on the level, so all 15 cells (14 grid cells plus the standard cell)
are computed offline from the standard run's first 8 candidates (ng, ll, ok, ed, the colloquial col and the reading-channel ch). The fusion
of every cell is the shipping one with the cell's own (alpha, tau, delta):
  s_i = ng_i + alpha ll_i + g_i mu (col_i - col_0) + nu (ch_i - ch_0),   mu 0.5, soft gate T 1.0, nu 1.0, c 4 fixed;
so in `P.eff_ll` / the channel line the coefficients are mu / alpha and nu / alpha with the CELL's alpha. tau "inf" (no limit) always reranks.
A non-finite col drops both terms and takes the plain pick with the level's header triple (core `guarded_pick`, whose `plain` is the file's
chat standard triple at the standard level).

`run` STOPs (exit 1) on: a row count other than the registered one (rows, rowstats, scores, summary, dump), a rows file or a model
(.sjtl, colloquial table) whose sha256 is not the registered one, a summary tag that is not the run's setting, a scores record that
disagrees with the dump, the two other levels' candidates / ll not being the standard run's, a header triple that is not the README's
shipping value of the level, anchor 1 (the standard shipping cell offline equals the run's `c` and rowstats ok, every row) or anchor 2
(the plain pick with the level's header triple, on the standard run's ng / ll, equals that level's run, every row).
`--cells` (any set; `select` takes chatclause only): every cell's fixes / breaks against the n-gram first. `--sel`: for each level the standard cell and the
chosen cell: ok per row, fixes / breaks against the n-gram first (the order gates) and against the level's CLI shipping value (the four
clauses), the clause verdict of the set. user-reported: the registered clause pools chat and formal over the 157 rows; formal is
unchanged (a plain pick at every level, no term, no cell), so its half has no fix and no break and the pooled fixes, breaks, net, p and
ratio equal the chat ones: only the chat half is computed, and the JSON says so.
Exit codes: 0 ok, 1 STOP or bad arguments, 2 argparse usage error.
"""
import argparse, hashlib, json, math, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "experiments", "p5-reading-channel"))
sys.path.insert(0, os.path.join(ROOT, "experiments", "s7a-tinylm"))
import p5 as Q  # noqa: E402  (puts p4d, p4, d23, tools, p4c on sys.path)
import bench  # noqa: E402
import evalstats  # noqa: E402
import triage as T  # noqa: E402
import p4c  # noqa: E402

P = Q.P
INF = math.inf
TOP_K = 8
MU, GATE, NU, CAP = P.CONFIRM_CELL[0], P.CONFIRM_CELL[2], 1.0, 4        # mu 0.5, gate T 1.0 (P4d), nu 1.0, c 4 (P5 lex,1.0,4): fixed
PAIRED_SJTL_SHA256 = "e7c179d85405ec42a40bb22adb58a7485c58f77aed89dfc2ca76a0d89568ed5e"   # core/src/colloquial.rs PAIRED_TINYLM_SHA256
RATIO = Q.RATIO                                                         # 0.6, the current shipping standard
CAP_DISCORD = Q.CAP_DISCORD                                             # 26

# ---------------------------------------------------------------- the registered grid (README "格點")
STD = (0.5, 2.0, 0.25)                                                  # the current shipping cell of the standard level
CONS = [(0.3, 2.0, 1.0), (0.3, 2.0, 1.5), (0.5, 2.0, 1.0), (0.5, 2.0, 1.5), (0.5, 2.0, 2.0), (1.0, 2.0, 3.0)]
AGGR = [(a, t, d) for a in (0.5, 0.7) for t in (2.0, INF) for d in (0.0, 0.1)]
LEVELS = ("conservative", "aggressive")
GRID = {"conservative": CONS, "aggressive": AGGR}
SHIP = {"conservative": (1.0, 2.0, 3.0), "aggressive": (0.3, 2.0, 0.25)}   # README "判讀": the header triples users see now
ARGS = {"conservative": "cons", "aggressive": "aggr"}

# ---------------------------------------------------------------- the registered sets (README "資料與工具")
ROW_COUNTS = {"chatclause": 5761, "discordtune": 4958, "cvtune-clean": 3286, "typing76": 76, "user-reported": 157, "dev302": 302}
# SHA-256 of each --rows file. The first five equal p5.ROW_SHA256 (test_sc checks); user-reported is the file at 157 rows.
ROW_SHA256 = {"chatclause": "81c035b086f91a0ff15143ed0b2e05f2d6af3b1947ebb2425fc467ff4f0e4203",
              "discordtune": "77e1c90a507fa7e6085c3fca7d377abfd527a5146f43f8c4388e72342627a775",
              "cvtune-clean": "d0960ebec0dc4562f104013951026d91950a73b33f85b10792d967185675d5fb",
              "typing76": "c061f66dfade21996803137320aa986a81a3e72e3e4e1dd559abd5dc04b68d08",
              "user-reported": "5659f5f60f1d9393696886eca9749972e5026547a157f4cc10d2f98f179f296e",
              "dev302": "603850e444efee38b8c121e3d666d6c540acd3a27d5a6a5070a78a962dd98456"}
ROLE = {"chatclause": "select", "discordtune": "1", "typing76": "2", "cvtune-clean": "3", "user-reported": "4", "dev302": "report"}
JUDGED = ("discordtune", "typing76", "cvtune-clean", "user-reported", "dev302")
GATE_SETS = ("discordtune", "cvtune-clean", "typing76", "user-reported")


def stop(msg):
    print(f"STOP: {msg}")
    raise SystemExit(1)


def label(cell):
    a, t, d = cell
    return f"{a},{'inf' if t == INF else int(t)},{d}"


def cell_of(text):
    for c in [STD] + CONS + AGGR:
        if label(c) == text:
            return c
    stop(f"off-grid cell refused: {text}")


sha_file = bench.sha_file


# ---------------------------------------------------------------- checks of what the run is

def check_rows_sha(name, path):
    if sha_file(path) != ROW_SHA256[name]:
        stop(f"{name}: the --rows file's sha256 is not the registered one")


def check_models(sjtl, t2):
    """STOP unless both models are the registered ones. -> the first 8 hex digits of the .sjtl's sha256 (the summary tags' +tl:), hashed once."""
    h = sha_file(sjtl)
    if h != PAIRED_SJTL_SHA256:
        stop("--sjtl is not the small model the terms were measured with (sha256)")
    if sha_file(t2) != P.T2_SHA256:
        stop("--t2 is not P4's colloquial table (sha256)")
    return h[:8]


def header_triples(path):
    """{standard, conservative, aggressive} -> the chat (alpha, tau, delta) of a version 3 or 4 .sjtl, read with export_sjtl's own structs
    (core/src/tinylm.rs `params`: the chat standard is the header's (alpha, tau) and the chat delta; the four level triples follow the deltas
    in the order chat conservative, chat aggressive, formal conservative, formal aggressive)."""
    import export_sjtl as X
    with open(path, "rb") as f:
        b = f.read(X.HEADER.size + X.DELTAS.size + X.LEVELS.size)
    magic, version, _, _, _, _, _, _, ca, ct, fa, ft, _ = X.HEADER.unpack_from(b, 0)
    if (magic, version) not in ((X.MAGIC_V3, 3), (X.MAGIC_V4, 4)):
        stop(f"{path} has no level triples (a version 3 or 4 .sjtl is needed)")
    delta = X.DELTAS.unpack_from(b, X.HEADER.size)[0]
    lv = X.LEVELS.unpack_from(b, X.HEADER.size + X.DELTAS.size)
    return {"standard": (ca, ct, delta), "conservative": tuple(lv[0:3]), "aggressive": tuple(lv[3:6])}


def check_headers(path):
    h = header_triples(path)
    for lv in LEVELS:
        if h[lv] != SHIP[lv]:
            stop(f"the .sjtl header's chat {lv} triple {h[lv]} is not the README's shipping value {SHIP[lv]}")
    return h


def read_summary(path):
    """-> (tag, n, top1) of the first `## name  TAG  {'n': N, 'top1': K, ...}` line of the CLI's stdout."""
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = re.match(r"^## .*?  (lm-\S+)  \{'n': (\d+), 'top1': (\d+),", line)
            if m:
                return m.group(1), int(m.group(2)), int(m.group(3))
    stop(f"{path}: no summary line")


def check_tag(tag, level, sjtl_sha8):
    """STOP unless the summary tag is the chat run of `level` (standard, conservative or aggressive) with the colloquial table; `+rc` (the
    reading-channel term applies) ends the tag at the standard level only, where `colloquial::applies` holds."""
    bad = []
    if not tag.startswith("lm-chat+ctx"):
        bad.append("profile / +ctx")
    bad += [x for x in ("-nodemote", "-noclasses", "-nonames", "+xo:") if x in tag]
    if f"+tl:{sjtl_sha8}" not in tag or "+acg" not in tag:
        bad.append("+tl: / +acg")
    if ("+rl:" in tag) != (level != "standard") or (level != "standard" and f"+rl:{level}" not in tag):
        bad.append("+rl:")
    if f"+colloq:{P.T2_SHA256[:8]}" not in tag or tag.endswith("+rc") != (level == "standard"):
        bad.append("+colloq / +rc")
    if bad:
        stop(f"{level}: summary tag {tag} does not match the setting ({', '.join(bad)})")


def check_count(what, n, want):
    if n != want:
        stop(f"{what}: {n} rows, expected {want}")


# ---------------------------------------------------------------- the fusion of a cell

def plain_pick(sc, params):
    """The CLI's pick where no term applies (`TinyLm::rerank`): `pick` over the first 8 with the header triple; 0 without `ll`."""
    ll = sc.get("ll")
    return 0 if ll is None or len(sc["ng"]) < 2 else T.pick(sc["ng"], ll, *params)


def cell_pick(r, cell, plain):
    """One cell's pick of row `r` ({"sc", "col", "ch"}): P4d's eff_ll with the cell's alpha (mu / alpha) plus (nu / alpha) (ch_i - ch_0), then
    `pick` with the cell's (alpha, tau, delta). A non-finite col: the plain pick with `plain`. tau INF never stops the rerank."""
    sc = r["sc"]
    ll = sc.get("ll")
    k = len(sc["ng"])
    if ll is None or k < 2:
        return 0
    if not all(math.isfinite(x) for x in r["col"][:k]):
        return T.pick(sc["ng"], ll, *plain)
    alpha, tau, delta = cell
    ng, eff = P.eff_ll(sc, r["col"], MU, alpha, GATE)
    eff = [x + (NU / alpha) * (r["ch"][i] - r["ch"][0]) for i, x in enumerate(eff)]
    return T.pick(ng, eff, alpha, tau, delta)


def ok_at(rows, picks):
    return Q.ok_of([r["sc"] for r in rows], picks)


def vs(base_ok, rows, picks):
    return p4c.stats(base_ok, ok_at(rows, picks))


short = Q.short


# ---------------------------------------------------------------- anchors

def anchor1(rows, base_ok, plain):
    """The standard shipping cell offline equals the run: the pick the CLI's `c`, the final ok rowstats, every row. -> the picks."""
    picks = [cell_pick(r, STD, plain) for r in rows]
    bad_c = [i for i, (r, j) in enumerate(zip(rows, picks), 1) if r["sc"]["c"] != j]
    bad_ok = [i for i, (r, j, o) in enumerate(zip(rows, picks, base_ok), 1) if bool(r["sc"]["ok"][j]) != o]
    if bad_c or bad_ok:
        stop(f"anchor 1 mismatch: pick differs on {len(bad_c)} rows, ok on {len(bad_ok)} rows, first rows {(bad_c + bad_ok)[:10]}")
    return picks


def anchor2(level, rows, recs, ship_ok, params):
    """The level's shipping value offline (the plain pick with the header triple on the standard run's ng / ll) equals that level's run: its
    `c` and its rowstats ok, every row. First the two runs must be the same candidates with the same ll."""
    if not len(rows) == len(recs) == len(ship_ok):
        stop(f"anchor 2 {level}: {len(recs)} scores records and {len(ship_ok)} rowstats rows for {len(rows)} rows")
    diff = [i for i, (r, rec) in enumerate(zip(rows, recs), 1)
            if rec["i"] != i or any(rec[k] != r["sc"][k] for k in ("n", "ng", "ok", "ed", "glen")) or rec.get("ll", [None])[:TOP_K] != r["sc"].get("ll", [None])[:TOP_K]]
    if diff:
        stop(f"anchor 2 {level}: candidates, ng or ll differ from the standard run on {len(diff)} rows, first rows {diff[:10]}")
    picks = [plain_pick(r["sc"], params) for r in rows]
    bad_c = [i for i, (rec, j) in enumerate(zip(recs, picks), 1) if rec["c"] != j]
    bad_ok = [i for i, (r, rec, o) in enumerate(zip(rows, recs, ship_ok), 1) if bool(r["sc"]["ok"][rec["c"]]) != o]
    if bad_c or bad_ok:
        stop(f"anchor 2 {level} mismatch: pick differs on {len(bad_c)} rows, ok on {len(bad_ok)} rows, first rows {(bad_c + bad_ok)[:10]}")


# ---------------------------------------------------------------- selection (README "選格") and gates (README "順序門檻")

def choose(level, stats, std):
    """stats: {label: {fixes, breaks}} of the level's grid cells against the n-gram first on chatclause; std: the standard cell's.
    conservative: breaks <= 0.5 std breaks and fixes > breaks, then the largest fixes - 4 breaks; ties larger delta, smaller alpha.
    aggressive: fixes > std fixes and net >= std net, then the largest fixes - 2 breaks; ties smaller alpha, tau 2 before inf, larger delta.
    -> the cell, or None (the standard cell then)."""
    f = lambda c: stats[label(c)]["fixes"]
    b = lambda c: stats[label(c)]["breaks"]
    if level == "conservative":
        ok = [c for c in CONS if 2 * b(c) <= std["breaks"] and f(c) > b(c)]
        key = lambda c: (-(f(c) - 4 * b(c)), -c[2], c[0])
    else:
        ok = [c for c in AGGR if f(c) > std["fixes"] and f(c) - b(c) >= std["fixes"] - std["breaks"]]
        key = lambda c: (-(f(c) - 2 * b(c)), c[0], c[1] == INF, -c[2])
    return min(ok, key=key) if ok else None


def gates(level, chosen, std):
    """chosen / std: {set: {fixes, breaks}} against the n-gram first on the GATE_SETS. -> {gate name: bool}.
    conservative: breaks(chosen) <= breaks(std) on all four sets, and discordtune net > 0.
    aggressive: on discordtune net(chosen) >= net(std), fixes(chosen) > fixes(std), and breaks / fixes <= 0.6."""
    if level == "conservative":
        out = {f"breaks_le_std:{n}": chosen[n]["breaks"] <= std[n]["breaks"] for n in GATE_SETS}
        out["discordtune_net_gt_0"] = chosen["discordtune"]["fixes"] - chosen["discordtune"]["breaks"] > 0
        return out
    c, s = chosen["discordtune"], std["discordtune"]
    return {"discordtune_net_ge_std": c["fixes"] - c["breaks"] >= s["fixes"] - s["breaks"], "discordtune_fixes_gt_std": c["fixes"] > s["fixes"],
            "discordtune_ratio_le_0.6": p4c.ratio_ok(c, RATIO)}


def decide(chosen, std, gates_pass, clauses_chosen, clauses_std):
    """README "判讀": the chosen cell ships when its gates and the four clauses (against the level's shipping value) all pass; any failure, or
    a chosen cell that is the standard one, takes the standard cell, which must pass the four clauses too, else nothing ships (the level
    stays as it is and is reported, not closed). -> (decision, shipped cell label or None)."""
    if chosen != std and gates_pass and all(clauses_chosen):
        return "chosen", chosen
    if all(clauses_std):
        return "standard", std
    return "keep-current", None


# ---------------------------------------------------------------- judging one arm

def verdict(role, s):
    """README clauses 1, 2, 4 on one set's stats against the level's shipping value (3, cvtune-clean, is the bootstrap)."""
    return bool({"1": lambda: Q.verdict("1", s), "2": lambda: Q.verdict("2a", s), "4": lambda: Q.verdict("3", s)}[role]())


def boot_ok(lo, n):
    """Clause 3: the 90% paired bootstrap lower bound of the top1 change, in percentage points of the set, is at least -1.0."""
    return 100 * lo / n >= -1.0


def arm_of(name, rows, ship_ok, ship_rows, ng_ok, cell, plain):
    """One cell on one set -> {cell, ok, vs_ngram, vs_ship, verdict ...}; every number against the n-gram first (gates) and against `ship_ok`
    (the level's CLI shipping value, the clauses). ship_rows: the rowstats (ok, errors, gold length) of that run, the bootstrap's base."""
    picks = [cell_pick(r, cell, plain) for r in rows]
    s = vs(ship_ok, rows, picks)
    arm = {"cell": label(cell), "ok": "".join(str(int(o)) for o in ok_at(rows, picks)), "vs_ngram": short(vs(ng_ok, rows, picks)), "vs_ship": p4c.jsonable(s)}
    role = ROLE[name]
    if role == "3":
        cand = [(int(r["sc"]["ok"][j]), r["sc"]["ed"][j], r["sc"]["glen"]) for r, j in zip(rows, picks)]
        (lo, _), _ = evalstats.paired_bootstrap(ship_rows, cand, level=0.90)
        arm.update({"bootstrap90_lower_rows": lo, "bootstrap90_lower_pp": 100 * lo / len(rows), "verdict": boot_ok(lo, len(rows))})
    elif role in ("1", "2", "4"):
        arm["verdict"] = verdict(role, s)
    if name == "dev302":      # chat: report only, the reader labels each break by category
        arm["fix_rows"] = [i for i, (o, b) in enumerate(zip(ok_at(rows, picks), ship_ok), 1) if o and not b]
        arm["break_rows"] = [i for i, (o, b) in enumerate(zip(ok_at(rows, picks), ship_ok), 1) if b and not o]
    return arm


# ---------------------------------------------------------------- loading

def load_standard(a, sjtl8):
    """The chat standard run -> (rows [{"sc", "col", "ch"}], base rowstats, summary tag). Everything is read for the first 8 candidates only."""
    import d23 as D
    import p4
    n_want = ROW_COUNTS[a.name]
    rows_in = T.parse_rows(T.read_text(a.rows))
    base = evalstats.read_rowstats(a.rowstats)
    tag, n_sum, top1 = read_summary(a.summary)
    check_tag(tag, "standard", sjtl8)
    cands, _, recs = T.read_cli_files(a.dump, a.dump_words, a.tinylm_scores, len(rows_in))
    for what, n in (("rows", len(rows_in)), ("rowstats", len(base)), ("summary", n_sum), ("scores", sum(r is not None for r in recs)),
                    ("dump", sum(bool(c) for c in cands))):
        check_count(f"{a.name} {what}", n, n_want)
    if sum(b[0] for b in base) != top1:
        stop(f"{a.name}: rowstats top1 {sum(b[0] for b in base)} != summary top1 {top1}")
    for i, (cs, sc) in enumerate(zip(cands, recs), 1):
        k = len(sc["ng"])
        if k != min(len(cs), TOP_K) or sc["ng"] != [c[1] for c in cs[:k]]:
            stop(f"row {i}: the scores record's ng is not the dump's first {TOP_K}")
        if any(c[2] is None for c in cs[:TOP_K]):
            stop(f"row {i}: a dumped candidate has no word split")
        if sc["glen"] != base[i - 1][2]:
            stop(f"row {i}: gold length differs between scores and rowstats")
    lex, ovl = D.load_lex_scores()
    logp = Q.lex_logp(lex)
    try:
        pre = p4.precompute([{"cands": cs[:TOP_K]} for cs in cands], {"T2": p4.Table(a.t2)}, ovl, D.T.Lex.load().base)
    except (p4.Stop, RuntimeError) as e:
        stop(str(e))
    cols = P.cols_from(pre)
    rows = [{"sc": sc, "col": col, "ch": [Q.ch_of(Q.word_terms(syls, c[2], logp), CAP) for c in cs[:TOP_K]]}
            for sc, col, cs, (_, _, syls) in zip(recs, cols, cands, rows_in)]
    return rows, base, tag


def load_level(a, level, n_want, sjtl8):
    """A level's run (rowstats, scores records) after its row counts and summary tag."""
    p = ARGS[level]
    rs, sc, sm = (getattr(a, f"{p}_{k}") for k in ("rowstats", "scores", "summary"))
    tag, n_sum, top1 = read_summary(sm)
    check_tag(tag, level, sjtl8)
    base = evalstats.read_rowstats(rs)
    recs = [json.loads(l) for l in T.lines_of(T.read_text(sc))]
    for what, n in (("rowstats", len(base)), ("scores", len(recs)), ("summary", n_sum)):
        check_count(f"{level} {what}", n, n_want)
    if sum(b[0] for b in base) != top1:
        stop(f"{level}: rowstats top1 != summary top1")
    return base, recs


# ---------------------------------------------------------------- commands

def cmd_run(a):
    if a.name == "chatclause" and a.sel:
        stop("chatclause is the selection set: it takes --cells, never --sel")
    Q.check_out(a.out, a.sel)
    check_rows_sha(a.name, a.rows)
    sjtl8 = check_models(a.sjtl, a.t2)
    hdr = check_headers(a.sjtl)
    rows, base, tag = load_standard(a, sjtl8)
    n = len(rows)
    base_ok = [bool(b[0]) for b in base]
    anchor1(rows, base_ok, hdr["standard"])
    ship = {}
    for lv in LEVELS:
        ship[lv] = load_level(a, lv, n, sjtl8)
        anchor2(lv, rows, ship[lv][1], [bool(b[0]) for b in ship[lv][0]], hdr[lv])
    ng_ok = [bool(r["sc"]["ok"][0]) for r in rows]
    res = {"name": a.name, "role": ROLE[a.name], "n": n, "anchor1": "ok", "anchor2": {lv: "ok" for lv in LEVELS}, "scores_sha256": sha_file(a.tinylm_scores),
           "shipping": {lv: list(SHIP[lv]) for lv in LEVELS}}
    print(f"{a.name} n {n} anchor 1 ok; anchor 2 ok at conservative and aggressive")
    if a.cells:
        # a cell at a level falls back (non-finite col) to that level's own header triple, the standard cell included
        stats = lambda lv, c: short(vs(ng_ok, rows, [cell_pick(r, c, hdr[lv]) for r in rows]))
        res["std"] = {lv: {label(STD): stats(lv, STD)} for lv in LEVELS}
        res["cells"] = {lv: {label(c): stats(lv, c) for c in GRID[lv]} for lv in LEVELS}
        for lv in LEVELS:
            s = res["std"][lv][label(STD)]
            print(f"cell {lv} {label(STD)} (standard) fixes {s['fixes']} breaks {s['breaks']}")
            for k, s in res["cells"][lv].items():
                print(f"cell {lv} {k} fixes {s['fixes']} breaks {s['breaks']} net {s['net']}")
        Q.write_atomic(a.out, json.dumps(res, indent=1))
        return 0
    with open(a.sel, encoding="utf-8") as f:
        sel = json.load(f)
    if "chosen" not in sel or set(sel["chosen"]) != set(LEVELS):
        stop(f"--sel: {a.sel} is not a selection")
    res["selection_sha256"] = sha_file(a.sel)
    res["levels"] = {}
    for lv in LEVELS:
        chosen = cell_of(sel["chosen"][lv])
        arms = {"std": arm_of(a.name, rows, [bool(b[0]) for b in ship[lv][0]], ship[lv][0], ng_ok, STD, hdr[lv])}
        arms["chosen"] = arms["std"] if chosen == STD else arm_of(a.name, rows, [bool(b[0]) for b in ship[lv][0]], ship[lv][0], ng_ok, chosen, hdr[lv])
        res["levels"][lv] = {"chosen": label(chosen), "ship_ok": "".join(str(int(b[0])) for b in ship[lv][0]), "arms": arms}
        for k in ("std", "chosen"):
            s = arms[k]["vs_ship"]
            print(f"{lv} {k} {arms[k]['cell']} vs ship fixes {s['fixes']} breaks {s['breaks']} net {s['net']} verdict {arms[k].get('verdict', 'report')}")
    res["ngram_ok"] = "".join(str(int(o)) for o in ng_ok)
    if a.name == "user-reported":
        res["pooling"] = ("chat rows only: the formal half is unchanged at every level, so it has no fix and no break; fixes, breaks, net, p and the "
                          f"error ratio equal the pooled ones (pooled n {2 * n})")
    Q.write_atomic(a.out, json.dumps(res, indent=1))
    return 0


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def cmd_select(a):
    Q.check_out(a.out, None)
    d = read_json(a.cells)
    if d.get("name") != "chatclause" or "cells" not in d or d.get("anchor1") != "ok" or any(v != "ok" for v in d.get("anchor2", {}).values()) \
            or set(d.get("anchor2", {})) != set(LEVELS):
        stop(f"{a.cells} is not the --cells output of chatclause with both anchors ok")
    chosen, fallback = {}, {}
    for lv in LEVELS:
        c = choose(lv, d["cells"][lv], d["std"][lv][label(STD)])
        chosen[lv], fallback[lv] = label(c or STD), c is None
        print(f"{lv} " + (label(c) if c else f"NO CELL QUALIFIES -> the standard cell {label(STD)}"))
    Q.write_atomic(a.out, json.dumps({"chosen": chosen, "selection_fallback": fallback, "std": d["std"], "cells": d["cells"]}, indent=1))
    return 0


def cmd_final(a):
    sel = read_json(a.sel)
    sel_sha = sha_file(a.sel)
    runs = {}
    for p in a.runs:
        d = read_json(p)
        if d.get("name") not in JUDGED or d.get("name") in runs or d.get("anchor1") != "ok" or any(d.get("anchor2", {}).get(lv) != "ok" for lv in LEVELS) \
                or "levels" not in d or d.get("selection_sha256") != sel_sha or d["n"] != ROW_COUNTS[d["name"]]:
            stop(f"{p}: not a distinct, anchored run of a judged set made with this selection")
        runs[d["name"]] = d
    if set(runs) != set(JUDGED):
        stop(f"judged runs missing: {sorted(set(JUDGED) - set(runs))}")
    res = {"levels": {}}
    for lv in LEVELS:
        chosen, std = sel["chosen"][lv], label(STD)
        arm = lambda n, k: runs[n]["levels"][lv]["arms"][k]
        if any(runs[n]["levels"][lv]["chosen"] != chosen for n in JUDGED):
            stop(f"{lv}: a run was made with another chosen cell than the selection's")
        g = None if chosen == std else gates(lv, {n: arm(n, "chosen")["vs_ngram"] for n in GATE_SETS}, {n: arm(n, "std")["vs_ngram"] for n in GATE_SETS})
        clauses = {k: {ROLE[n]: arm(n, k)["verdict"] for n in GATE_SETS} for k in ("chosen", "std")}
        decision, cell = decide(chosen, std, g is None or all(g.values()), list(clauses["chosen"].values()), list(clauses["std"].values()))
        res["levels"][lv] = {"chosen": chosen, "selection_fallback": sel["selection_fallback"][lv], "gates": g, "clauses_chosen": clauses["chosen"],
                             "clauses_std": clauses["std"], "decision": decision, "ship_cell": cell,
                             "numbers": {n: {k: {"vs_ngram": arm(n, k)["vs_ngram"], "vs_ship": {x: arm(n, k)["vs_ship"][x] for x in ("fixes", "breaks", "net", "p", "error_ratio")}}
                                         for k in ("chosen", "std")} for n in JUDGED}}
        print(f"{lv} chosen {chosen} gates {g if g is not None else 'n/a (the standard cell)'}")
        for k in ("chosen", "std"):
            print(f"{lv} {k} clauses " + " ".join(f"{r}:{'PASS' if v else 'FAIL'}" for r, v in clauses[k].items()))
        print(f"{lv} DECISION {decision} {cell if cell else '(the level stays as it is; report, do not close)'}")
    if a.out:
        Q.write_atomic(a.out, json.dumps(res, indent=1))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--name", required=True, choices=sorted(ROW_COUNTS))
    for k in ("rows", "sjtl", "t2", "dump", "dump-words", "rowstats", "tinylm-scores", "summary", "out"):
        r.add_argument("--" + k, required=True)
    for p in ARGS.values():
        for k in ("rowstats", "scores", "summary"):
            r.add_argument(f"--{p}-{k}", required=True)
    g = r.add_mutually_exclusive_group(required=True)
    g.add_argument("--cells", action="store_true")
    g.add_argument("--sel")
    s = sub.add_parser("select")
    s.add_argument("cells")
    s.add_argument("--out", required=True)
    f = sub.add_parser("final")
    f.add_argument("sel")
    f.add_argument("runs", nargs="+")
    f.add_argument("--out")
    a = ap.parse_args(argv)
    return {"run": cmd_run, "select": cmd_select, "final": cmd_final}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
