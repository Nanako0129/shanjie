#!/usr/bin/env python3
"""R1d offline recomputation (README of this directory, section "R1d: 底層重做"): the reading term is already inside the n-gram decoder
(the CLI's `--reading-decode`, core/src/reading.rs); this tool reorders the decoded 64-best with the colloquial term (chat only), and in the
six rerank-on settings fuses the first 8 of that order with the small model at the .sjtl header's own triple of the level. Counts and numbers only on
stdout and in the JSON: never a sentence, context, word or character of a row (row numbers are allowed).

  r1d.py run    --setting S --name SET --rows R --dump D --dump-words W --rowstats RS --tinylm-scores SC --summary STDOUT_OF_THE_CLI
                --base-rowstats BRS --base-summary BSTDOUT --sjtl data/lm/tinylm.sjtl [--t2 data/lm/colloquial.sjlm]
                (--mu-cells | --mu-from SEL.json | --mu M)  --out OUT.json                          (mu is chat only)
  r1d.py select CHATCLAUSE_CHAT_OFF_CELLS.json --out SEL.json
  r1d.py anchor3 --setting S --n64 SCORES_N64 --n8 SCORES_N8 --out A3.json                           (chat-std and formal-std cvtune-clean)
  r1d.py final  SEL.json --anchor3 CHAT_STD.json FORMAL_STD.json JUDGED.json ...                  (the four clauses of the 8 settings)

S is one of chat-std chat-cons chat-aggr formal-std formal-cons formal-aggr chat-off formal-off. The rd run (`--rows` ... `--summary`) is
ONE CLI run with `--reading-decode --context --packs acg --tinylm ... --rerank-level L --tinylm-scores-topk 64 --dump --dump-words --rowstats
--tinylm-scores`, chat WITHOUT --colloquial-core (so the CLI's own fusion is the plain header triple); the -off settings take the files of the
matching -std run. The baseline (`--base-rowstats`, `--base-summary`) is the shipping run of the setting: chat-on with --colloquial-core (tag
+colloq:...+rc), formal-on, and for -off the same profile without --tinylm.

Base score of candidate j of the 64: b_j = ng_j + mu * (col_j - col_0) in chat (col from P4's T2 table exactly as r1.py load_run computes it),
b_j = ng_j in formal; ordered by b, ties by dump rank. Rerank off: the pick is the first of b. Rerank on: T.pick over the first 8 of b with the
header triple of the setting's profile and level; a row without `ll`, or with a top-8 candidate without one, takes the first of b.
Before any cell it STOPs (exit 1) on: a row count or rows hash other than the registered one, a summary tag that is not the setting's (rd run
or baseline), a scores record whose ng / ok differ from the dump or from the lenient comparison, a non-finite col, anchor 1 (mu 0, the offline
rerank-on pick equals the run's `c` on every row, and the dump is in b order at mu 0), anchor 2 (rerank off at mu 0 is dump rank 0).
Exit codes: 0 ok, 1 STOP or bad arguments.
"""
import argparse, json, math, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import r1 as R  # noqa: E402  (loaders, header_params, anchor3, tag constants; puts p5, p4, d23, tools, p4c on sys.path)

Q, T, P, p4c, evalstats = R.Q, R.T, R.P, R.p4c, R.evalstats
stop = R.stop
TOP_K = R.TOP_K
MUS = (0.0, 0.25, 0.5, 1.0)

# setting -> (profile, level, rerank on)
SETTINGS = {**{s: (p, l, True) for s, (p, l) in R.SETTINGS.items()}, "chat-off": ("chat", "standard", False), "formal-off": ("formal", "standard", False)}
RUN_OF = {"chat-off": "chat-std", "formal-off": "formal-std"}      # the rd run whose files a rerank-off setting reads
ROLES = {**R.ROLES, "chat-off": R.ROLES["chat-std"], "formal-off": R.ROLES["formal-std"]}
# the selection set of mu is chatclause under chat-off; the other "select" roles of the six-setting table are reported at the chosen mu
SELECT = ("chat-off", "chatclause")
ROW_COUNTS = R.ROW_COUNTS
# final: every (setting, set) of the table that is not a select-role set (those may be passed too, as report-only runs)
JUDGED = sorted((s, n) for s, d in ROLES.items() for n, role in d.items() if role != "select")
CLAUSE3_PAIRS = (("chat-std", "formal-std"), ("chat-off", "formal-off"))


def mu_label(mu):
    return None if mu is None else f"{mu:g}"


# ---------------------------------------------------------------- summary tags

def check_tag(tag, setting, sjtl_sha8, rd):
    """STOP unless the summary tag is exactly the setting's. rd: the --reading-decode run (tag ends in +rd, chat has no colloquial term, `setting`
    is the run's own: the -std one for a -off setting); otherwise the shipping baseline of `setting` (chat-on: +colloq:..+rc). Both are
    built by r1.ship_tag from tools/bench.py's VERSIONS2[v0.6.1] row and data/*.sha256 (the small model's hash is `sjtl_sha8`, the run's own file)."""
    profile, level, rerank = SETTINGS[setting]
    want = R.ship_tag(profile, level, rerank, tl8=sjtl_sha8, colloq=False if rd else None) + ("+rd" if rd else "")
    if tag != want:
        stop(f"{setting}: summary tag {tag} is not the {'rd run' if rd else 'baseline'} tag {want} of the setting")


# ---------------------------------------------------------------- loading

def load_run(a, setting, name):
    """Everything one rd CLI run and the baseline give, checked: per row the 64 candidates (surface, ng, words), ok / ed of all 64, the scores
    record and, for chat, col of all 64. -> (rows, base_ok, base rowstats, rd rowstats ok)."""
    import d23 as D
    profile, level, rerank = SETTINGS[setting]
    run_setting = RUN_OF.get(setting, setting)
    n_want = ROW_COUNTS[name]
    if name not in Q.ROW_SHA256:
        stop(f"{name}: no registered rows hash")
    Q.check_rows_sha(name, a.rows)
    rows = T.parse_rows(T.read_text(a.rows))
    base = evalstats.read_rowstats(a.base_rowstats)
    rd_ok = [bool(b[0]) for b in evalstats.read_rowstats(a.rowstats)]
    sha8 = R.sha_file(a.sjtl)[:8]
    rtag, rn, rtop = R.read_summary(a.summary)
    check_tag(rtag, run_setting, sha8, rd=True)
    btag, bn, btop = R.read_summary(a.base_summary)
    check_tag(btag, setting, sha8, rd=False)
    cands, _, recs = T.read_cli_files(a.dump, a.dump_words, a.tinylm_scores, len(rows))
    for what, n in (("rows", len(rows)), ("baseline rowstats", len(base)), ("rd rowstats", len(rd_ok)), ("rd summary", rn), ("baseline summary", bn)):
        if n != n_want:
            stop(f"{name}: {what} has {n} rows, expected {n_want}")
    if sum(b[0] for b in base) != btop or sum(rd_ok) != rtop:
        stop(f"{name}: a rowstats top1 differs from its summary's")
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
            stop(f"row {i}: gold length differs between scores and the baseline rowstats")
        out.append({"syls": syls, "cands": cs, "sc": sc, "ok64": ok64, "ed64": ed64, "ng64": [c[1] for c in cs]})
    if profile == "chat":
        import p4
        if R.sha_file(a.t2) != P.T2_SHA256:
            stop("--t2 is not P4's T2 table (sha256)")
        lex, ovl = D.load_lex_scores()
        try:
            pre = p4.precompute(out, {"T2": p4.Table(a.t2)}, ovl, D.T.Lex.load().base)
        except (p4.Stop, RuntimeError) as e:
            stop(str(e))
        for i, (r, p) in enumerate(zip(out, pre), 1):
            r["col"] = [lc - P.GAMMA_PER_MU * k for lc, k in zip(p["lc"]["T2"], p["k"])]
            if not all(math.isfinite(x) for x in r["col"]):
                stop(f"row {i}: a non-finite colloquial score (the core would fall back to the plain pick; R1d registers no such rule)")
    return out, [bool(b[0]) for b in base], base, rd_ok


# ---------------------------------------------------------------- the pick

def base_scores(r, mu):
    """b_j of the 64 candidates: ng_j, plus mu * (col_j - col_0) in chat (mu None / 0: exactly ng)."""
    if not mu:
        return list(r["ng64"])
    col = r["col"]
    return [g + mu * (c - col[0]) for g, c in zip(r["ng64"], col)]


def r1d_pick(params, rerank, r, mu):
    """-> (chosen candidate index among the 64, order of the 64 by b, 'off' | 'ok' | 'none' | 'nollcand'). params: the header triple."""
    b = base_scores(r, mu)
    order = sorted(range(len(b)), key=lambda j: (-b[j], j))
    if not rerank:
        return order[0], order, "off"
    ll = r["sc"].get("ll")
    if ll is None:
        return order[0], order, "none"
    top = order[:TOP_K]
    if any(ll[j] is None for j in top):
        return order[0], order, "nollcand"
    return top[T.pick([b[j] for j in top], [ll[j] for j in top], *params)], order, "ok"


def stats_of(base_ok, rows, picks):
    s = p4c.stats(base_ok, [r["ok64"][j] for r, j in zip(rows, picks)])
    s["u2"] = P.u2(s)
    return s


def run_mu(params, rerank, rows, base_ok, mu):
    picks, firsts, moved, beyond, kinds = [], [], 0, 0, {"off": 0, "ok": 0, "none": 0, "nollcand": 0}
    top8 = list(range(TOP_K))
    for r in rows:
        j, order, how = r1d_pick(params, rerank, r, mu)
        picks.append(j)
        firsts.append(order[0])
        kinds[how] += 1
        moved += set(order[:TOP_K]) != set(top8[:len(order)])
        beyond += how != "none" and any(x >= TOP_K for x in order[:TOP_K])
    s = stats_of(base_ok, rows, picks)
    if not rerank:      # the pick IS the first of b, so b-first against the n-gram shipping baseline is the setting's own fixes / breaks
        bf = p4c.stats(base_ok, [r["ok64"][j] for r, j in zip(rows, firsts)])
        s.update({"b_first_fixes": bf["fixes"], "b_first_breaks": bf["breaks"]})
    s.update({"top8_changed_rows": moved, "top8_needs_beyond_8_rows": beyond,
              "rows_without_ll": kinds["none"], "rows_top8_candidate_without_ll": kinds["nollcand"], "top1_before": sum(base_ok),
              "top1_after": sum(r["ok64"][j] for r, j in zip(rows, picks))})
    return s, picks


# ---------------------------------------------------------------- anchors

def anchor1(params, rows, rd_ok):
    """mu 0, rerank on: the offline pick equals the run's `c` and the final ok equals the run's rowstats, every row; the dump is in b order."""
    bad_c, bad_ok, bad_order = [], [], []
    for i, (r, o) in enumerate(zip(rows, rd_ok), 1):
        j, order, _ = r1d_pick(params, True, r, 0.0)
        bad_c += [i] * (r["sc"]["c"] != j)
        bad_ok += [i] * (r["ok64"][j] != o)
        bad_order += [i] * (order != list(range(len(order))))
    if bad_c or bad_ok or bad_order:
        stop(f"anchor 1 mismatch: pick differs on {len(bad_c)} rows, ok on {len(bad_ok)} rows, dump not in n-gram order on {len(bad_order)} rows, "
             f"first rows {(bad_c + bad_ok + bad_order)[:10]}")


def anchor2(rows):
    """Rerank off at mu 0 is the first of the dump (trivial; asserted so a change of the sort cannot slip in)."""
    bad = [i for i, r in enumerate(rows, 1) if r1d_pick((), False, r, 0.0)[0] != 0]
    if bad:
        stop(f"anchor 2 mismatch: rerank off at mu 0 is not dump rank 0 on {len(bad)} rows, first rows {bad[:10]}")


# ---------------------------------------------------------------- commands

def check_pair(setting, name):
    if setting not in SETTINGS or name not in ROLES[setting]:
        stop(f"{name} is not a set of {setting} in the README table")


def parse_mu(text):
    try:
        mu = float(text)
    except ValueError:
        stop(f"bad mu {text!r}")
    if mu not in MUS:
        stop("off-grid mu refused")
    return mu


def cmd_run(a):
    check_pair(a.setting, a.name)
    Q.check_out(a.out, a.mu_from)
    profile, level, rerank = SETTINGS[a.setting]
    role = ROLES[a.setting][a.name]
    if a.mu_cells and (a.setting, a.name) != SELECT:
        stop("--mu-cells is for the selection set (chat-off chatclause)")
    if profile == "chat" and not a.t2:
        stop("chat needs --t2")
    if profile == "formal" and (a.mu_cells or a.mu_from or a.mu is not None):
        stop("mu is chat only")
    if profile == "chat" and not (a.mu_cells or a.mu_from or a.mu is not None):
        stop("chat needs --mu-cells, --mu-from or --mu")
    mu = None
    if a.mu_from:
        with open(a.mu_from, encoding="utf-8") as f:
            chosen = json.load(f).get("chosen")
        if chosen is None:
            stop("--mu-from: the selection chose no mu")
        mu = parse_mu(chosen)
    elif a.mu is not None:
        mu = parse_mu(a.mu)
    rows, base_ok, base, rd_ok = load_run(a, a.setting, a.name)
    params = R.header_params(a.sjtl, profile, level)      # the -off settings read the standard triple: the rd run they share is the -std one
    anchor1(params, rows, rd_ok)
    anchor2(rows)
    res = {"setting": a.setting, "name": a.name, "role": role, "n": len(rows), "anchor1": "ok", "anchor2": "ok",
           "scores_sha256": R.sha_file(a.tinylm_scores), "gold_in_64": sum(any(r["ok64"]) for r in rows), "params": list(params)}
    print(f"{a.setting} {a.name} n {len(rows)} anchors ok; gold_in_64 {res['gold_in_64']}")
    if a.mu_cells:
        res["cells"] = {}
        for m in MUS:
            s, _ = run_mu(params, rerank, rows, base_ok, m)
            res["cells"][mu_label(m)] = p4c.jsonable(s)
            print(f"mu {mu_label(m)} u2 {s['u2']} {p4c.kv(s)}")
        Q.write_atomic(a.out, json.dumps(res, indent=1))
        return 0
    s, picks = run_mu(params, rerank, rows, base_ok, mu)
    res.update({"mu": mu_label(mu), "selection": os.path.basename(a.mu_from) if a.mu_from else "fixed", **p4c.jsonable(s)})
    print(f"mu {mu_label(mu)} u2 {s['u2']} {p4c.kv(s)} top1 {s['top1_before']} -> {s['top1_after']}")
    if role in ("1", "2a"):
        res["verdict"] = bool(Q.verdict(role, s))
    elif role == "2b":
        brow = lambda r, j: (int(r["ok64"][j]), r["ed64"][j], r["sc"]["glen"])
        (lo, _), _ = evalstats.paired_bootstrap(base, [brow(r, j) for r, j in zip(rows, picks)], level=0.90)
        res.update({"bootstrap90_lower_rows": lo, "bootstrap90_lower_pp": 100 * lo / len(rows), "verdict": 100 * lo / len(rows) >= -1.0})
    if a.name == "dev302":
        res["fix_rows"] = [i for i, (r, j, o) in enumerate(zip(rows, picks, base_ok), 1) if r["ok64"][j] and not o]
        res["break_rows"] = [i for i, (r, j, o) in enumerate(zip(rows, picks, base_ok), 1) if o and not r["ok64"][j]]
    res["base_ok"] = "".join(str(int(o)) for o in base_ok)
    res["cell_ok"] = "".join(str(int(r["ok64"][j])) for r, j in zip(rows, picks))
    res["pick_idx"] = picks
    if "verdict" in res:
        print(f"clause {role} {'PASS' if res['verdict'] else 'FAIL'}")
    Q.write_atomic(a.out, json.dumps(res, indent=1))
    return 0


def choose(cells):
    """cells: {mu label: stats} of chatclause under chat-off. The largest U2, ties the smaller mu (README R1d)."""
    return min(MUS, key=lambda m: (-cells[mu_label(m)]["u2"], m))


def cmd_select(a):
    Q.check_out(a.out, None)
    with open(a.cells, encoding="utf-8") as f:
        d = json.load(f)
    if (d.get("setting"), d.get("name")) != SELECT or "cells" not in d or d.get("anchor2") != "ok" or d.get("n") != ROW_COUNTS["chatclause"]:
        stop(f"{a.cells} is not the --mu-cells output of {SELECT}")
    best = choose(d["cells"])
    for m in MUS:
        print(f"mu {mu_label(m)} u2 {d['cells'][mu_label(m)]['u2']}")
    print(f"chosen mu {mu_label(best)}")
    Q.write_atomic(a.out, json.dumps({"chosen": mu_label(best), "cells": {mu_label(m): d["cells"][mu_label(m)]["u2"] for m in MUS}}, indent=1))
    return 0


def cmd_final(a):
    try:
        with open(a.sel, encoding="utf-8") as f:
            sel = json.load(f)
    except (OSError, ValueError):
        stop(f"{a.sel} is not the JSON written by `select` (a bare MU is refused)")
    if not isinstance(sel, dict) or "cells" not in sel or sel.get("chosen") is None:
        stop(f"{a.sel} is not the JSON written by `select` (cells and chosen)")
    chosen = mu_label(parse_mu(sel["chosen"]))
    runs = {}
    for p in a.judged:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        k = (d.get("setting"), d.get("name"))
        if "cells" in d or d.get("anchor2") != "ok" or k not in [(s, n) for s, x in ROLES.items() for n in x]:
            stop(f"{p}: not a run of a (setting, set) of the table")
        if k in runs:
            stop(f"{p}: duplicate run of {k}")
        if SETTINGS[k[0]][0] == "chat" and (d.get("mu") != chosen or d.get("selection") != os.path.basename(a.sel)):
            stop(f"{p}: not a run at the chosen mu {chosen} made with --mu-from {os.path.basename(a.sel)}")
        if d.get("anchor1") != "ok":
            stop(f"{p}: anchor 1 was not checked")
        if "verdict" not in d and d["role"] in ("1", "2a", "2b"):
            stop(f"{p}: a judged role without a verdict")
        runs[k] = d
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
    if miss:
        stop(f"judged runs missing {miss}")
    for k, d in runs.items():
        if d["n"] != ROW_COUNTS[k[1]]:
            stop(f"{k}: {d['n']} rows")
        if k[0] in RUN_OF and (RUN_OF[k[0]], k[1]) in runs and runs[RUN_OF[k[0]], k[1]]["scores_sha256"] != d["scores_sha256"]:
            stop(f"{k}: not the rd scores file of {(RUN_OF[k[0]], k[1])} (the -off setting reads the -std run's files)")
    res = {"mu": chosen, "clauses": {}}
    for role in ("1", "2a", "2b"):
        res["clauses"][role] = {f"{s}/{n}": runs[s, n]["verdict"] for s, n in JUDGED if ROLES[s][n] == role}
    res["clauses"]["3"], res["clause3_stats"] = {}, {}
    for pair in CLAUSE3_PAIRS:
        base = "".join(runs[s, "user-reported"]["base_ok"] for s in pair)
        cell = "".join(runs[s, "user-reported"]["cell_ok"] for s in pair)
        s3 = p4c.stats([c == "1" for c in base], [c == "1" for c in cell])
        key = "user-reported " + " + ".join(pair)
        res["clauses"]["3"][key] = bool(Q.verdict("3", s3))
        res["clause3_stats"][key] = p4c.jsonable({k: s3[k] for k in ("n", "fixes", "breaks", "net", "p", "error_ratio")})
        print(f"clause 3 {key}: fixes {s3['fixes']} breaks {s3['breaks']} net {s3['net']} error_ratio {s3['error_ratio']:.3f}")
    res["all_pass"] = all(v for c in res["clauses"].values() for v in c.values())
    for role, c in res["clauses"].items():
        for k, v in c.items():
            print(f"clause {role} {k} {'PASS' if v else 'FAIL'}")
    print("ALL FOUR CLAUSES PASS" if res["all_pass"] else "NOT ALL CLAUSES PASS: 照登記不過")
    keys = ("fixes", "breaks", "net", "error_ratio", "top8_changed_rows", "top8_needs_beyond_8_rows",
            "rows_without_ll", "rows_top8_candidate_without_ll", "top1_before", "top1_after", "gold_in_64")
    res["report_only"] = {f"{s}/{n}": {k: d[k] for k in keys + (("b_first_fixes", "b_first_breaks") if s.endswith("-off") else ())}
                          for (s, n), d in sorted(runs.items())}
    # the three levels' order after R1d, per set they share (README: the order of the levels must still hold)
    res["level_order"] = {}
    for n in sorted({n for (s, n) in runs}):
        for prof in ("chat", "formal"):
            lv = [(s, runs[s, n]) for s in (f"{prof}-cons", f"{prof}-std", f"{prof}-aggr") if (s, n) in runs]
            if len(lv) == 3:
                res["level_order"][f"{prof}/{n}"] = {"before": [d["top1_before"] for _, d in lv], "after": [d["top1_after"] for _, d in lv]}
    for k, v in res["level_order"].items():
        print(f"top1 cons/std/aggr {k}: before {v['before']} after {v['after']}")
    if a.out:
        Q.write_atomic(a.out, json.dumps(res, indent=1))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--setting", required=True, choices=SETTINGS)
    r.add_argument("--name", required=True, choices=sorted(ROW_COUNTS))
    for k in ("rows", "dump", "dump-words", "rowstats", "tinylm-scores", "summary", "base-rowstats", "base-summary", "sjtl", "out"):
        r.add_argument("--" + k, required=True)
    r.add_argument("--t2")
    g = r.add_mutually_exclusive_group()
    g.add_argument("--mu-cells", action="store_true")
    g.add_argument("--mu-from")
    g.add_argument("--mu")
    s = sub.add_parser("select")
    s.add_argument("cells")
    s.add_argument("--out", required=True)
    f = sub.add_parser("final")
    f.add_argument("sel")
    f.add_argument("--anchor3", nargs=2, required=True, metavar=("CHAT_STD.json", "FORMAL_STD.json"))
    f.add_argument("judged", nargs="+")
    f.add_argument("--out")
    n = sub.add_parser("anchor3")
    n.add_argument("--setting", required=True, choices=("chat-std", "formal-std"))
    n.add_argument("--out", required=True)
    n.add_argument("--n64", required=True)
    n.add_argument("--n8", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "anchor3":
        R.write_anchor3(a, R.anchor3(a))
        return 0
    return {"run": cmd_run, "select": cmd_select, "final": cmd_final}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
