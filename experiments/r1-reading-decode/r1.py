#!/usr/bin/env python3
"""R1 offline recomputation (README of this directory, registration revision 1): P5's reading term moved from the fusion onto the n-gram
score, s_j = ng_j + lambda * ch_j over the CLI's first 64 candidates, the new first 8 into the shipping fusion of each setting. Counts and
numbers only on stdout and in the JSON: never a sentence, context, word or character of a row (row numbers are allowed).

  r1.py run     --setting S --name SET --rows R --dump D --dump-words W --rowstats RS --tinylm-scores SC --summary STDOUT_OF_THE_CLI
                --sjtl data/lm/tinylm.sjtl [--t2 data/lm/colloquial.sjlm]  (--cells | --cell-from SEL.json)  --out OUT.json
                [--norc-scores SC2 --norc-rowstats RS2 --norc-summary SUM2]    (anchor 2: ANCHOR2 below)
  r1.py select  CHATCLAUSE_CELLS.json WIKITUNE_CLEAN_CELLS.json --out SEL.json
  r1.py anchor3 --setting S --n64 SCORES_N64 --n8 SCORES_N8 --out A3.json                                 (chat-std and formal-std cvtune-clean)
  r1.py final   SEL.json JUDGE.json ...                                          (the four clauses from the 17 judged runs)

S is one of chat-std chat-cons chat-aggr formal-std formal-cons formal-aggr (README table). `run` needs the CLI's stdout (the summary line)
for the setting tag. Before any cell it STOPs (exit 1) on: a row count other than the registered one (rows, dump, scores, rowstats,
summary), a rows file whose sha256 is not the registered one, a summary tag that is not the setting's (profile, +tl: of --sjtl, +acg,
+rl:, +colloq / +rc, +tri), a scores record whose ng / ok differ from the dump or from the lenient comparison, anchor 1 (the shipping cell
offline, lambda 0: the pick equals the run's `c` and the final ok equals rowstats, every row), anchor 2 (the level's colloquial cell
offline, nu 0, equals the --no-reading-channel run), or the lambda 0 reordering not reproducing the shipping cell (the dump is then not in
n-gram order). The fusion of each setting: chat at every level is the colloquial cell of core/src/colloquial.rs `cell(level)` (PR #271:
the level's own alpha, tau, delta, P4d's mu and gate) plus P5's lex,1.0,4 for the shipping cell, the R1 cells dropping the P5 term;
formal is the .sjtl header's triple at its level. The re-measure of the product contract (docs/contracts/reading-decode.md section 2.1)
also writes each row's pick as a dump index (`pick_idx`, numbers only) for the core parity.
Exit codes: 0 ok, 1 STOP or bad arguments, 3 NO CELL QUALIFIES (select).
"""
import argparse, hashlib, json, math, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "experiments", "p5-reading-channel"))
sys.path.insert(0, os.path.join(ROOT, "experiments", "s7a-tinylm"))
import p5 as Q  # noqa: E402  (puts p4d, p4, d23, tools, p4c on sys.path)
import evalstats  # noqa: E402
import triage as T  # noqa: E402
import p4c  # noqa: E402

P = Q.P
TOP_K = 8
LAMBDAS, CS = (0.25, 0.5, 1.0, 2.0), (1, 2, 4)
GRID = [(lam, c) for lam in LAMBDAS for c in CS]        # 12 cells, estimator lex only
SHIP_NU, SHIP_C = 1.0, 4                                 # P5's registered cell lex,1.0,4 (the shipping chat cell of every level, with its colloquial cell)
TRI8, COLLOQ8 = "fdd3b05d", P.T2_SHA256[:8]
LEVELS = {"conservative": 0, "standard": 1, "aggressive": 2}
# core/src/colloquial.rs `cell(level)` (STANDARD, CONSERVATIVE, AGGRESSIVE): the chat (alpha, tau, delta) the colloquial term fuses with
COLLOQ_CELLS = {"standard": (0.5, 2.0, 0.25), "conservative": (1.0, 2.0, 3.0), "aggressive": (0.5, 2.0, 0.1)}

# setting -> (profile, level). The roles below are the README tables: select (the selection set), 1 / 2a / 2b / 3 (clauses), report.
SETTINGS = {"chat-std": ("chat", "standard"), "chat-cons": ("chat", "conservative"), "chat-aggr": ("chat", "aggressive"),
            "formal-std": ("formal", "standard"), "formal-cons": ("formal", "conservative"), "formal-aggr": ("formal", "aggressive")}
ROLES = {"chat-std": {"chatclause": "select", "discordtune": "1", "cvtune-clean": "2b", "typing76": "2a", "user-reported": "3", "dev302": "report"},
         "chat-cons": {"discordtune": "1", "typing76": "2a"},
         "chat-aggr": {"discordtune": "1", "typing76": "2a"},
         "formal-std": {"wikitune-clean": "select", "cvtune-clean": "2b", "typing76": "2a", "user-reported": "3", "dev302": "2a"},
         "formal-cons": {"wikitune-clean": "2b", "typing76": "2a"},
         "formal-aggr": {"wikitune-clean": "2b", "typing76": "2a"}}
SELECT_SETS = {("chat-std", "chatclause"), ("formal-std", "wikitune-clean")}
# (setting, set) runs that have a --no-reading-channel twin (README: R1c's two plus the product contract's two)
ANCHOR2 = {("chat-std", "chatclause"), ("chat-std", "discordtune"), ("chat-cons", "discordtune"), ("chat-aggr", "discordtune")}
ROW_COUNTS = {**Q.ROW_COUNTS, "wikitune-clean": 2181}
# p5's table and its check_rows_sha (a set without a hash is a STOP there: KeyError is not a pass), wikitune-clean added
Q.ROW_SHA256["wikitune-clean"] = "d576ea941242e04d718c3d5cf4711c43693588d1089461970293edcec9c5db92"  # eval/bench/kpi/*.json wikitune_clean.rows_sha256
# R1c: the first 154 rows of eval/dev/user-reported.txt after main c8842c8e (row 3 reads 一 as ㄧˊ); R1 and R1b used b78834cb…
Q.ROW_SHA256["user-reported"] = "63f67b724e877bba26b8ddc307f56360dbd599823924303639d366e711a11cef"
# final: the judged runs the four clauses are read from (every (setting, set) of the table that is not a selection set)
JUDGED = sorted((s, n) for s, d in ROLES.items() for n in d if (s, n) not in SELECT_SETS)


stop = Q.stop


# ---------------------------------------------------------------- the .sjtl header, summary line, row files

def header_params(path, profile, level):
    """(alpha, tau, delta) of `profile` at `level`, read from the .sjtl header with export_sjtl's own structs (core/src/tinylm.rs `params`):
    the standard triple is (alpha, tau) of the header and the profile's delta; versions 3 and 4 add four triples after the deltas in the
    order chat conservative, chat aggressive, formal conservative, formal aggressive; before version 3 every level gets the standard one."""
    import export_sjtl as X
    with open(path, "rb") as f:
        b = f.read(X.HEADER.size + X.DELTAS.size + X.LEVELS.size)
    magic, version, _, _, _, _, _, _, ca, ct, fa, ft, _ = X.HEADER.unpack_from(b, 0)
    if (magic, version) not in ((X.MAGIC, 1), (X.MAGIC_V2, 2), (X.MAGIC_V3, 3), (X.MAGIC_V4, 4)):
        stop(f"{path} is not a .sjtl file")
    deltas = X.DELTAS.unpack_from(b, X.HEADER.size) if version >= 2 else (0.0, 0.0)
    pi = 0 if profile == "chat" else 1
    std = ((ca, ct, deltas[0]), (fa, ft, deltas[1]))[pi]
    if level == "standard" or version < 3:
        return std
    lv = X.LEVELS.unpack_from(b, X.HEADER.size + X.DELTAS.size)
    k = 3 * (2 * pi + (0 if level == "conservative" else 1))
    return tuple(lv[k:k + 3])


def sha_file(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def read_summary(path):
    """-> (tag, n, top1) of the first `## name  TAG  {'n': N, 'top1': K, ...}` line of the CLI's stdout."""
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = re.match(r"^## .*?  (lm-\S+)  \{'n': (\d+), 'top1': (\d+),", line)
            if m:
                return m.group(1), int(m.group(2)), int(m.group(3))
    stop(f"{path}: no summary line")


def check_tag(tag, setting, sjtl_sha8, rc):
    """STOP unless the summary tag is the setting's (README table): rc says whether `+rc` must end the colloquial part."""
    profile, level = SETTINGS[setting]
    bad = []
    if not tag.startswith(f"lm-{profile}+ctx"):
        bad.append("profile / +ctx")
    bad += [x for x in ("-nodemote", "-noclasses", "-nonames", "+xo:") if x in tag]
    if f"+tl:{sjtl_sha8}" not in tag or "+acg" not in tag:
        bad.append("+tl: / +acg")
    if ("+rl:" in tag) != (level != "standard") or (level != "standard" and f"+rl:{level}" not in tag):
        bad.append("+rl:")
    if profile == "chat":
        if f"+colloq:{COLLOQ8}" not in tag or tag.endswith("+rc") != rc:
            bad.append("+colloq / +rc")
    elif "+colloq" in tag or f"+tri:{TRI8}" not in tag:
        bad.append("+colloq / +tri")
    if bad:
        stop(f"{setting}: summary tag {tag} does not match the setting ({', '.join(bad)})")


# ---------------------------------------------------------------- loading

def load_run(a, setting, name):
    """Everything one CLI run gives, checked: per row the 64 candidates (surface, ng, words), ok / ed of all 64, the scores record,
    P5's terms, and for chat the colloquial cols of all 64. Anchors are checked by the caller."""
    import d23 as D
    profile, level = SETTINGS[setting]
    n_want = ROW_COUNTS[name]
    if name not in Q.ROW_SHA256:
        stop(f"{name}: no registered rows hash")
    if getattr(a, "sandhi_of", None):         # R1c report-only: the rows are sandhi.py of the registered file, line for line
        import sandhi
        Q.check_rows_sha(name, a.sandhi_of)
        if sandhi.sandhi_rows(T.read_text(a.sandhi_of))[0] != T.read_text(a.rows):
            stop(f"{name}: --rows is not sandhi.py of --sandhi-of")
    else:
        Q.check_rows_sha(name, a.rows)
    rows = T.parse_rows(T.read_text(a.rows))
    base = evalstats.read_rowstats(a.rowstats)
    tag, n_sum, top1 = read_summary(a.summary)
    check_tag(tag, setting, sha_file(a.sjtl)[:8], rc=(profile == "chat"))
    cands, _, recs = T.read_cli_files(a.dump, a.dump_words, a.tinylm_scores, len(rows))
    for what, n in (("rows", len(rows)), ("rowstats", len(base)), ("summary", n_sum)):
        if n != n_want:
            stop(f"{name}: {what} has {n} rows, expected {n_want}")
    if sum(b[0] for b in base) != top1:
        stop(f"{name}: rowstats top1 {sum(b[0] for b in base)} != summary top1 {top1}")
    if any(r is None for r in recs) or any(c[2] is None for cs in cands for c in cs):
        stop(f"{name}: a row has no scores record or a dumped candidate has no word split")
    lenient = T.load_lenient()
    out = []
    for i, ((ctx, truth, syls), cs, sc) in enumerate(zip(rows, cands, recs), 1):
        t = lenient(truth)
        ok64 = [lenient(c[0]) == t for c in cs]
        ed64 = [0 if o else evalstats.levenshtein(c[0], truth) for c, o in zip(cs, ok64)]
        k = len(sc["ng"])
        if k != min(len(cs), TOP_K) or sc["ng"] != [c[1] for c in cs[:k]] or [bool(x) for x in sc["ok"]] != ok64[:k] or sc["ed"] != ed64[:k]:
            stop(f"row {i}: the scores record's ng / ok / ed are not the dump's and the lenient comparison's")
        if "ll" in sc and len(sc["ll"]) != min(64, len(cs)):
            stop(f"row {i}: {len(sc['ll'])} ll for {len(cs)} candidates: --tinylm-scores-topk 64 is required (an N 8 scores file is refused)")
        if sc["glen"] != base[i - 1][2]:
            stop(f"row {i}: gold length differs between scores and rowstats")
        out.append({"syls": syls, "cands": cs, "sc": sc, "ok64": ok64, "ed64": ed64, "ng64": [c[1] for c in cs]})
    lex, ovl = D.load_lex_scores()
    logp = Q.lex_logp(lex)
    for r in out:
        r["terms"] = [Q.word_terms(r["syls"], c[2], logp) for c in r["cands"]]
        r["ch"] = {c: [Q.ch_of(t, c) for t in r["terms"]] for c in CS}
    # R1b arm B (README, R1b): the R1 term only, never P5's shipping term (anchor 1 reproduces shipping with r["ch"]), counts the
    # tone-sandhi readings of 一 and 不 as 0. Without the flag r["ch_r1"] is r["ch"], so R1 is unchanged.
    rlogp = sandhi_exempt(logp) if getattr(a, "sandhi_exempt", False) else logp
    if getattr(a, "floor_mc", False):         # R1c: the R1 term only, like the sandhi flag
        rlogp = floor_mc(rlogp, lex, Q.mc_logp(lex, Q.load_mcbpmf()))
    for r in out:
        if rlogp is logp:
            r["terms_r1"], r["ch_r1"] = r["terms"], r["ch"]
        else:
            r["terms_r1"] = [Q.word_terms(r["syls"], cand[2], rlogp) for cand in r["cands"]]
            r["ch_r1"] = {c: [Q.ch_of(t, c) for t in r["terms_r1"]] for c in CS}
    if profile == "chat":
        import p4
        if sha_file(a.t2) != P.T2_SHA256:
            stop("--t2 is not P4's T2 table (sha256)")
        try:
            pre = p4.precompute(out, {"T2": p4.Table(a.t2)}, ovl, D.T.Lex.load().base)
        except (p4.Stop, RuntimeError) as e:
            stop(str(e))
        for r, p in zip(out, pre):
            r["col"] = [lc - P.GAMMA_PER_MU * k for lc, k in zip(p["lc"]["T2"], p["k"])]
    return out, [bool(b[0]) for b in base], base


# ---------------------------------------------------------------- the fusion of one setting, and the R1 cells

def make_cfg(a_sjtl, setting, nu):
    """The fusion of a setting: chat is the level's colloquial cell (COLLOQ_CELLS) with the reading term weight `nu` (SHIP_NU shipping, 0
    the colloquial cell alone) and, for a non-finite col, the file's plain chat triple of the level; formal is the plain rerank with the
    header's triple of its level."""
    profile, level = SETTINGS[setting]
    plain = header_params(a_sjtl, profile, level)
    if profile == "chat":
        return {"kind": "colloq", "nu": nu, "plain": plain, "cell": COLLOQ_CELLS[level]}
    return {"kind": "plain", "plain": plain}


def fuse_index(cfg, ng, ll, col, ch):
    """Index (into the given lists, at most 8, in the order given) of the fused pick; 0 for no `ll`. `ch` is the reading term of the same
    candidates (only used with nu)."""
    if ll is None or len(ng) < 2:
        return 0
    if cfg["kind"] == "plain":
        return T.pick(ng, ll, *cfg["plain"])
    if not all(math.isfinite(x) for x in col):                 # core guarded_pick: both terms dropped, the plain chat triple of the level
        return T.pick(ng, ll, *cfg["plain"])
    mu, gate = P.CONFIRM_CELL[0], P.CONFIRM_CELL[2]
    alpha, tau, delta = cfg["cell"]                              # mu / alpha and nu / alpha with the level's own alpha (strength-colloq)
    ng, eff = P.eff_ll({"ng": ng, "ll": ll}, col, mu, alpha, gate)
    if cfg["nu"]:
        eff = [x + (cfg["nu"] / alpha) * (ch[i] - ch[0]) for i, x in enumerate(eff)]
    return T.pick(ng, eff, alpha, tau, delta)


def ship_picks(cfg, rows):
    """The shipping cell offline on the first 8 candidates in the dump's order: per row the chosen index (the CLI's `c`)."""
    out = []
    for r in rows:
        sc, ll = r["sc"], r["sc"].get("ll")
        k = len(sc["ng"])
        out.append(fuse_index(cfg, sc["ng"], None if ll is None else ll[:k], r.get("col", [])[:k], r["ch"][SHIP_C][:k]))
    return out


SANDHI = ((("ㄧˊ",), "一"), (("ㄧˋ",), "一"), (("ㄅㄨˊ",), "不"))


def sandhi_exempt(logp):
    """A copy of `logp` where the sandhi readings of 一 (ㄧˊ, ㄧˋ) and 不 (ㄅㄨˊ) cost 0: they are the standard readings before the right
    tones (docs/contracts/s2r), not rare ones. ponytail: exempt whatever the next syllable, the rule that picks the sandhi tone is not checked."""
    out = dict(logp)
    for k in SANDHI:
        if k in out:
            out[k] = 0.0
    return out


LEX_FLOOR = -6.8   # the score data/lexicon/mcbpmf-data.txt gives a single character's secondary reading it has no count for


def floor_mc(logp, lex, mc):
    """R1c: a copy of `logp` where each single-character pair whose lexicon score is the floor takes P5's mc value (the mass of the
    multi-character words that read the character that way) instead. No sandhi pair (SANDHI) is a floor pair, so the order of the
    two R1 flags does not matter."""
    out = dict(logp)
    for k, s in lex.items():
        if len(k[1]) == 1 and abs(s - LEX_FLOOR) < 1e-9 and k in mc:
            out[k] = mc[k]
    return out


def r1_pick(cfg, r, lam, c):
    """-> (chosen candidate index among the 64, order of the 64 by s, 'ok' | 'none' | 'nollcand'): the new top 8 by s = ng + lam * ch go
    into the fusion with their own ll; a row without `ll`, or whose new top 8 has a candidate without one (null: over the model's context), is decided
    by the first of s (README, registered rule: core skips the rerank when a top-8 candidate is too long)."""
    ch = r["ch_r1"][c]
    s = [g + lam * h for g, h in zip(r["ng64"], ch)]
    order = sorted(range(len(s)), key=lambda j: (-s[j], j))
    top = order[:TOP_K]
    ll = r["sc"].get("ll")
    if ll is None:
        return top[0], order, "none"
    if any(ll[j] is None for j in top):
        return top[0], order, "nollcand"
    col = [r["col"][j] for j in top] if "col" in r else []
    return top[fuse_index(cfg, [s[j] for j in top], [ll[j] for j in top], col, None)], order, "ok"


def stats_of(base_ok, rows, picks):
    s = p4c.stats(base_ok, [r["ok64"][j] for r, j in zip(rows, picks)])
    s["u2"] = P.u2(s)
    return s


def label(cell):
    return f"{cell[0]},{cell[1]}"


# ---------------------------------------------------------------- anchors

def anchor1(cfg, rows, base_ok):
    """The shipping cell offline equals the run: the pick the CLI's `c`, the final ok rowstats. -> the picks."""
    picks = ship_picks(cfg, rows)
    bad_c = [i for i, (r, j) in enumerate(zip(rows, picks), 1) if r["sc"]["c"] != j]
    bad_ok = [i for i, (r, j, o) in enumerate(zip(rows, picks, base_ok), 1) if r["ok64"][j] != o]
    if bad_c or bad_ok:
        stop(f"anchor 1 mismatch: pick differs on {len(bad_c)} rows, ok on {len(bad_ok)} rows, first rows {(bad_c + bad_ok)[:10]}")
    return picks


def anchor2(a, p4d, rows, setting, name):
    """The level's colloquial cell offline (`p4d`, nu 0, the first 8 in order) equals the --no-reading-channel run: pick and ok, every row."""
    if not (a.norc_scores and a.norc_rowstats and a.norc_summary):
        stop(f"anchor 2: --norc-scores, --norc-rowstats and --norc-summary are required for {setting} on {name}")
    tag, n, _ = read_summary(a.norc_summary)
    check_tag(tag, setting, sha_file(a.sjtl)[:8], rc=False)
    recs = [json.loads(l) for l in T.lines_of(T.read_text(a.norc_scores))]
    ok2 = [bool(b[0]) for b in evalstats.read_rowstats(a.norc_rowstats)]
    if not (n == len(recs) == len(ok2) == len(rows) == ROW_COUNTS[name]):
        stop(f"anchor 2: the --no-reading-channel run has other row counts than {name}")
    bad = [i for i, (r, rec, j, o) in enumerate(zip(rows, recs, p4d, ok2), 1) if rec["i"] != i or rec["c"] != j or r["ok64"][j] != o]
    if bad:
        stop(f"anchor 2 mismatch: the colloquial cell of {setting} offline differs from the --no-reading-channel run on {len(bad)} rows, first rows {bad[:10]}")


def check_lambda0(cfg, rows, ship):
    """The R1 machinery at lambda 0 (sorted by s = ng, top 8, the same fusion) must be the shipping cell of the cfg: else the dump is not in
    n-gram order and the offline sorting is not the decoder's."""
    bad = [i for i, (r, j) in enumerate(zip(rows, ship), 1) if r1_pick(cfg, r, 0.0, SHIP_C)[0] != j]
    if bad:
        stop(f"lambda 0 reordering differs from the shipping cell on {len(bad)} rows (dump not in n-gram order?), first rows {bad[:10]}")


def write_anchor3(a, res):
    Q.write_atomic(a.out, json.dumps(res, indent=1))


def anchor3(a):
    """The first 8 `ll` of the N 64 scores file equal the N 8 file's, byte for byte (as text), and nothing else in a record differs."""
    n64, n8 = (T.lines_of(T.read_text(p)) for p in (a.n64, a.n8))
    if len(n64) != len(n8):
        stop(f"anchor 3: {len(n64)} rows in the N 64 file, {len(n8)} in the N 8 file")
    bad, longer = [], 0
    for i, (x, y) in enumerate(zip(n64, n8), 1):
        m = re.search(r'"ll": \[([^\]]*)\]', x)
        cut = x
        if m:
            toks = m.group(1).split(", ")
            longer += len(toks) > TOP_K
            cut = x[:m.start(1)] + ", ".join(toks[:TOP_K]) + x[m.end(1):]
        if cut != y:
            bad.append(i)
    if bad:
        write_anchor3(a, {"setting": a.setting, "name": "cvtune-clean", "pass": False, "rows": len(n64), "mismatched_rows": len(bad),
                          "n64_sha256": sha_file(a.n64), "n8_sha256": sha_file(a.n8)})
        stop(f"anchor 3 mismatch: the first 8 ll (or another field) differ on {len(bad)} rows, first rows {bad[:10]}")
    print(f"anchor 3 OK rows {len(n64)} rows_with_more_than_8_ll {longer}")
    return {"setting": a.setting, "name": "cvtune-clean", "pass": True, "rows": len(n64), "rows_with_more_than_8_ll": longer,
            "n64_sha256": sha_file(a.n64), "n8_sha256": sha_file(a.n8)}


# ---------------------------------------------------------------- report-only numbers

def term_counts(rows):
    cands = uncut = slots = missing = 0
    for r in rows:
        for t, c in zip(r["terms_r1"], r["cands"]):
            cands += 1
            if t is None:
                uncut += 1
                continue
            for w, x in zip(c[2], t):
                if len(w) == 1:
                    slots += 1
                    missing += x is None
    return {"candidates": cands, "uncut_candidates": uncut, "single_char_slots": slots, "missing_pairs": missing}


def run_cell(cfg, rows, base_ok, cell):
    lam, c = cell
    picks, firsts, moved, beyond, no_ll, nollcand = [], [], 0, 0, 0, 0
    for r in rows:
        j, order, how = r1_pick(cfg, r, lam, c)
        picks.append(j)
        firsts.append(order[0])
        top = order[:TOP_K]
        moved += set(top) != set(range(min(len(order), TOP_K)))
        beyond += how != "none" and any(x >= TOP_K for x in top)
        no_ll += how == "none"
        nollcand += how == "nollcand"
    s = stats_of(base_ok, rows, picks)
    ng = p4c.stats([r["ok64"][0] for r in rows], [r["ok64"][j] for r, j in zip(rows, firsts)])
    s.update({"ngram_fixes": ng["fixes"], "ngram_breaks": ng["breaks"], "top8_changed_rows": moved, "top8_needs_beyond_8_rows": beyond,
              "rows_without_ll": no_ll, "rows_top8_candidate_without_ll": nollcand})
    return s, picks


def gold_deducted(rows):
    """user-reported only: the rows whose right candidate (the first of the 64 that is right) has a single-character word with a negative
    reading term (lex), as [row, slots below 0, lowest term]. Row numbers and numbers only (the file is public: look the rows up there)."""
    out = []
    for i, r in enumerate(rows, 1):
        j = next((j for j, o in enumerate(r["ok64"]) if o), None)
        t = None if j is None else r["terms_r1"][j]
        neg = [x for x in (t or []) if x is not None and x < 0]
        if neg:
            out.append([i, len(neg), round(min(neg), 4)])
    return out


# ---------------------------------------------------------------- commands

def check_pair(setting, name):
    if setting not in SETTINGS or name not in ROLES[setting]:
        stop(f"{name} is not a set of {setting} in the README table")


def parse_cell(text):
    """'L,C' -> (lambda, c), refused unless it is a cell of the registered grid."""
    try:
        lam, c = text.split(",")
        cell = (float(lam), int(c))
    except ValueError:
        stop(f"bad cell {text!r}: L,C")
    if cell not in GRID:
        stop("off-grid cell refused")
    return cell


def cmd_run(a):
    check_pair(a.setting, a.name)
    Q.check_out(a.out, a.cell_from)
    a.cell_label = getattr(a, "cell", None)
    role = ROLES[a.setting][a.name]
    if a.cells and (a.setting, a.name) not in SELECT_SETS:
        stop("--cells is for the selection sets (chat-std chatclause, formal-std wikitune-clean)")
    if SETTINGS[a.setting][0] == "chat" and not a.t2:
        stop("chat needs --t2")
    needs2 = (a.setting, a.name) in ANCHOR2
    if not needs2 and (a.norc_scores or a.norc_rowstats or a.norc_summary):
        stop("--norc-* belongs to the ANCHOR2 runs only")
    cell = None
    if a.cell_from or a.cell_label:
        chosen = a.cell_label
        if a.cell_from:
            with open(a.cell_from, encoding="utf-8") as f:
                chosen = json.load(f).get("chosen")
            if not chosen:
                stop("--cell-from: the selection chose no cell")
        cell = parse_cell(chosen)
    rows, base_ok, base = load_run(a, a.setting, a.name)
    cfg = make_cfg(a.sjtl, a.setting, SHIP_NU)
    cfg_r1 = make_cfg(a.sjtl, a.setting, 0.0)
    anchor1(cfg, rows, base_ok)
    res = {"setting": a.setting, "name": a.name, "role": role, "n": len(rows), "anchor1": "ok", "scores_sha256": sha_file(a.tinylm_scores),
           "gold_in_64": sum(any(r["ok64"]) for r in rows), "terms": term_counts(rows),
           "shipping_params": list(cfg.get("cell", cfg["plain"])), "fallback_params": list(cfg["plain"]), "sandhi_exempt": bool(getattr(a, "sandhi_exempt", False)),
           "floor_mc": bool(getattr(a, "floor_mc", False)), "sandhi_variant": bool(getattr(a, "sandhi_of", None))}
    p4d = ship_picks(cfg_r1, rows)        # chat: the level's colloquial cell (nu 0); formal: the shipping cell again
    if needs2:
        anchor2(a, p4d, rows, a.setting, a.name)
        res["anchor2"] = "ok"
    check_lambda0(cfg_r1, rows, p4d)
    print(f"{a.setting} {a.name} n {len(rows)} anchors ok; gold_in_64 {res['gold_in_64']}")
    if a.cells:
        res["cells"] = {}
        for g in GRID:
            s, _ = run_cell(cfg_r1, rows, base_ok, g)
            res["cells"][label(g)] = p4c.jsonable(s)
            print(f"cell {label(g)} u2 {s['u2']} {p4c.kv(s)}")
        Q.write_atomic(a.out, json.dumps(res, indent=1))
        return 0
    s, picks = run_cell(cfg_r1, rows, base_ok, cell)
    res.update({"cell": label(cell), "selection": os.path.basename(a.cell_from) if a.cell_from else "fixed", **p4c.jsonable(s)})
    print(f"cell {label(cell)} u2 {s['u2']} {p4c.kv(s)}")
    if role in ("1", "2a"):
        res["verdict"] = bool(Q.verdict(role, s))
    elif role == "2b":
        brow = lambda r, j: (int(r["ok64"][j]), r["ed64"][j], r["sc"]["glen"])
        (lo, _), _ = evalstats.paired_bootstrap(base, [brow(r, j) for r, j in zip(rows, picks)], level=0.90)
        res.update({"bootstrap90_lower_rows": lo, "bootstrap90_lower_pp": 100 * lo / len(rows), "verdict": 100 * lo / len(rows) >= -1.0})
    if a.name == "dev302":   # chat: report only, rows to be labelled by category by the reader
        res["fix_rows"] = [i for i, (r, j, o) in enumerate(zip(rows, picks, base_ok), 1) if r["ok64"][j] and not o]
        res["break_rows"] = [i for i, (r, j, o) in enumerate(zip(rows, picks, base_ok), 1) if o and not r["ok64"][j]]
    if a.name == "user-reported":
        res["gold_deducted"] = gold_deducted(rows)
    # per-row 0/1 (numbers only): clause 3 pools them, and R1c compares two arms row by row from them
    res["base_ok"] = "".join(str(int(o)) for o in base_ok)
    res["cell_ok"] = "".join(str(int(r["ok64"][j])) for r, j in zip(rows, picks))
    res["pick_idx"] = picks               # the cell's pick per row as a dump index (0-63): the core parity compares it with the CLI's `c`
    if "verdict" in res:
        print(f"clause {role} {'PASS' if res['verdict'] else 'FAIL'}")
    Q.write_atomic(a.out, json.dumps(res, indent=1))
    return 0


def choose(ca, cw):
    """ca / cw: {label: stats} of chatclause (chat-std) and wikitune-clean (formal-std). Both U2 > 0, the largest sum; ties the larger of the
    smaller U2, smaller lambda, larger c (README)."""
    ok = [g for g in GRID if ca[label(g)]["u2"] > 0 and cw[label(g)]["u2"] > 0]
    u = lambda g: (ca[label(g)]["u2"], cw[label(g)]["u2"])
    return min(ok, key=lambda g: (-sum(u(g)), -min(u(g)), g[0], -g[1])) if ok else None


def cmd_select(a):
    Q.check_out(a.out, None)
    docs = []
    for p, want in ((a.chatclause, ("chat-std", "chatclause")), (a.wikitune, ("formal-std", "wikitune-clean"))):
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        if (d.get("setting"), d.get("name")) != want or "cells" not in d or d.get("anchor1") != "ok":
            stop(f"{p} is not the --cells output of {want}")
        if want[0] == "chat-std" and d.get("anchor2") != "ok":
            stop(f"{p}: anchor 2 was not checked")
        docs.append(d["cells"])
    best = choose(*docs)
    for g in GRID:
        print(f"cell {label(g)} u2 chatclause {docs[0][label(g)]['u2']} wikitune-clean {docs[1][label(g)]['u2']}")
    print(label(best) if best else "NO CELL QUALIFIES")
    Q.write_atomic(a.out, json.dumps({"chosen": label(best) if best else None, "cells": {label(g): [docs[0][label(g)]["u2"], docs[1][label(g)]["u2"]] for g in GRID}}, indent=1))
    return 0 if best else 3


def cmd_final(a):
    if a.sel.endswith(".json"):
        with open(a.sel, encoding="utf-8") as f:
            sel = json.load(f)
        if not sel.get("chosen"):
            stop("the selection chose no cell")
    else:                                   # R1b: a fixed cell, no selection
        sel = {"chosen": label(parse_cell(a.sel))}
    runs = {}
    for p in a.judged:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        if d.get("sandhi_variant"):
            stop(f"{p}: a sandhi-variant run is report only, never judged")
        if d.get("cell") != sel["chosen"] or d.get("anchor1") != "ok" or "verdict" not in d and d["role"] in ("1", "2a", "2b"):
            stop(f"{p}: not a judged run of the chosen cell {sel['chosen']}")
        if (d["setting"], d["name"]) in ANCHOR2 and d.get("anchor2") != "ok":
            stop(f"{p}: anchor 2 was not checked")
        runs[d["setting"], d["name"]] = d
    flags = {(bool(d.get("sandhi_exempt", False)), bool(d.get("floor_mc", False))) for d in runs.values()}
    if len(flags) != 1:
        stop("the judged runs mix --sandhi-exempt / --floor-mc settings")
    seen3 = set()
    for p in a.anchor3:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        run = runs.get((d.get("setting"), "cvtune-clean"))
        if not d.get("pass") or d.get("name") != "cvtune-clean" or run is None or run.get("scores_sha256") != d.get("n64_sha256"):
            stop(f"{p}: no passing anchor 3 for the N 64 scores file of the judged {d.get('setting')} cvtune-clean run")
        seen3.add(d["setting"])
    if seen3 != {"chat-std", "formal-std"}:
        stop("anchor 3 is required for chat-std and formal-std")
    miss = [k for k in JUDGED if k not in runs]
    if miss or len(runs) != len(a.judged):
        stop(f"judged runs missing {miss} or duplicated ({len(a.judged)} files, {len(runs)} runs)")
    for k, d in runs.items():
        if d["n"] != ROW_COUNTS[k[1]]:
            stop(f"{k}: {d['n']} rows")
    sandhi, floor = flags.pop()
    res = {"cell": sel["chosen"], "sandhi_exempt": sandhi, "floor_mc": floor, "clauses": {}}
    by_role = lambda role: [k for k in JUDGED if ROLES[k[0]][k[1]] == role]
    for role in ("1", "2a", "2b"):
        res["clauses"][role] = {f"{s}/{n}": runs[s, n]["verdict"] for s, n in by_role(role)}
    base = "".join(runs[k]["base_ok"] for k in by_role("3"))
    cell = "".join(runs[k]["cell_ok"] for k in by_role("3"))
    s = p4c.stats([c == "1" for c in base], [c == "1" for c in cell])
    res["clauses"]["3"] = {"user-reported chat-std + formal-std": bool(Q.verdict("3", s))}
    res["clause3_stats"] = p4c.jsonable({k: s[k] for k in ("n", "fixes", "breaks", "net", "p", "error_ratio")})
    res["all_pass"] = all(v for c in res["clauses"].values() for v in c.values())
    for role, c in res["clauses"].items():
        for k, v in c.items():
            print(f"clause {role} {k} {'PASS' if v else 'FAIL'}")
    print(f"clause 3 pooled fixes {s['fixes']} breaks {s['breaks']} net {s['net']} error_ratio {s['error_ratio']:.3f}")
    print("ALL FOUR CLAUSES PASS" if res["all_pass"] else "NOT ALL CLAUSES PASS: 照登記不過")
    res["report_only"] = {f"{s}/{n}": {k: runs[s, n][k] for k in ("fixes", "breaks", "net", "error_ratio", "ngram_fixes", "ngram_breaks",
                                                                      "top8_changed_rows", "top8_needs_beyond_8_rows", "rows_without_ll", "rows_top8_candidate_without_ll", "gold_in_64")}
                          for s, n in JUDGED}
    res["gold_deducted"] = {k[0]: runs[k]["gold_deducted"] for k in by_role("3")}
    if a.out:
        Q.write_atomic(a.out, json.dumps(res, indent=1))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--setting", required=True, choices=SETTINGS)
    r.add_argument("--name", required=True, choices=sorted(ROW_COUNTS))
    for k in ("rows", "dump", "dump-words", "rowstats", "tinylm-scores", "summary", "sjtl", "out"):
        r.add_argument("--" + k, required=True)
    r.add_argument("--t2")
    g = r.add_mutually_exclusive_group(required=True)
    g.add_argument("--cells", action="store_true")
    g.add_argument("--cell-from")
    g.add_argument("--cell", help="a fixed cell L,C of the grid (R1b): no selection file")
    for k in ("norc-scores", "norc-rowstats", "norc-summary"):
        r.add_argument("--" + k)
    r.add_argument("--sandhi-exempt", action="store_true", help="R1b arm B: the R1 term counts the sandhi readings of 一 and 不 as 0")
    r.add_argument("--sandhi-of", help="R1c report only: --rows is sandhi.py of this registered rows file")
    r.add_argument("--floor-mc", action="store_true", help="R1c: the R1 term takes mc for the lexicon's floor-score single characters")
    s = sub.add_parser("select")
    s.add_argument("chatclause")
    s.add_argument("wikitune")
    s.add_argument("--out", required=True)
    f = sub.add_parser("final")
    f.add_argument("sel")
    f.add_argument("--anchor3", nargs=2, required=True, metavar=("CHAT_STD.json", "FORMAL_STD.json"), help="the passing anchor3 results of the two cvtune-clean runs")
    f.add_argument("judged", nargs="+")
    f.add_argument("--out")
    n = sub.add_parser("anchor3")
    n.add_argument("--setting", required=True, choices=("chat-std", "formal-std"))
    n.add_argument("--out", required=True)
    n.add_argument("--n64", required=True)
    n.add_argument("--n8", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "anchor3":
        write_anchor3(a, anchor3(a))
        return 0
    return {"run": cmd_run, "select": cmd_select, "final": cmd_final}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
