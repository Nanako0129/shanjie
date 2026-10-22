#!/usr/bin/env python3
"""colloq-levels parity tool (docs/contracts/colloquial-levels.md section 2.2): the core's new path, row by row, against the offline
arithmetic of experiments/strength-colloq (sc.py `cell_pick`, copied below; that worktree is not imported).

  expect_core.py --name SET --rows R --sjtl data/lm/tinylm.sjtl --t2 data/lm/colloquial.sjlm
      --dump D --dump-words W --rowstats RS --tinylm-scores SC --summary SUM                      (chat standard, the shipping flags)
      --cons-rowstats RS --cons-scores SC --cons-summary SUM --aggr-rowstats RS --aggr-scores SC --aggr-summary SUM
      [--out OUT.json]

Three CLI runs of one set, all with `--colloquial-core` (so every summary tag must end in `+rc`): standard with `--dump --dump-words --rowstats
--tinylm-scores`, conservative and aggressive (`--rerank-level`) with `--tinylm-scores --rowstats`. Offline side: the standard run's first 8
candidates (ng, ll, col, ch) and `cell_pick(r, cell, header[level])` with the level's cell, restated here from the README, not read from core:
  standard (0.5, 2, 0.25), conservative (1.0, 2, 3.0), aggressive (0.5, 2, 0.1); the fallback of a non-finite col is the level's .sjtl header triple.
Compared per row: the scores record's `c` and its right/wrong (`ok[c]`), and the level run's rowstats ok. Standard is compared too (anchor 1).
Prints counts and row numbers only; never a sentence, context, word or character of a row (so it can run on private sets). Exit 0: 0 differing
rows at both levels; 1: STOP (a hash, a count, a tag, a candidate/ll mismatch, or any differing row).
"""
import argparse, json, math, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "experiments", "p5-reading-channel"))
sys.path.insert(0, os.path.join(ROOT, "experiments", "s7a-tinylm"))
import p5 as Q  # noqa: E402  (puts p4d, p4, d23, tools, p4c on sys.path)
import bench  # noqa: E402
import evalstats  # noqa: E402
import triage as T  # noqa: E402

P = Q.P
TOP_K = 8
MU, GATE, NU, CAP = P.CONFIRM_CELL[0], P.CONFIRM_CELL[2], 1.0, 4        # mu 0.5, gate T 1.0 (P4d), nu 1.0, c 4 (P5 lex,1.0,4): fixed
PAIRED_SJTL_SHA256 = "e7c179d85405ec42a40bb22adb58a7485c58f77aed89dfc2ca76a0d89568ed5e"
# The three cells (strength-colloq README: the standard cell; the R2 registration; the cell R1 selected and R2 repeated).
CELL = {"standard": (0.5, 2.0, 0.25), "conservative": (1.0, 2.0, 3.0), "aggressive": (0.5, 2.0, 0.1)}
# The .sjtl header's chat triples (the fallback of a non-finite col); read from the file and checked against these.
HEADER = {"standard": (0.3, 2.0, 0.25), "conservative": (1.0, 2.0, 3.0), "aggressive": (0.3, 2.0, 0.25)}
LEVELS = ("conservative", "aggressive")
ARGS = {"conservative": "cons", "aggressive": "aggr"}
ROW_COUNTS = {"chatclause": 5761, "discordtune": 4958, "cvtune-clean": 3286, "typing76": 76, "user-reported": 157, "dev302": 302}
ROW_SHA256 = {"chatclause": "81c035b086f91a0ff15143ed0b2e05f2d6af3b1947ebb2425fc467ff4f0e4203",
              "discordtune": "77e1c90a507fa7e6085c3fca7d377abfd527a5146f43f8c4388e72342627a775",
              "cvtune-clean": "d0960ebec0dc4562f104013951026d91950a73b33f85b10792d967185675d5fb",
              "typing76": "c061f66dfade21996803137320aa986a81a3e72e3e4e1dd559abd5dc04b68d08",
              "user-reported": "5659f5f60f1d9393696886eca9749972e5026547a157f4cc10d2f98f179f296e",
              "dev302": "603850e444efee38b8c121e3d666d6c540acd3a27d5a6a5070a78a962dd98456"}
sha_file = bench.sha_file


def stop(msg):
    print(f"STOP: {msg}")
    raise SystemExit(1)


def header_triples(path):
    """{standard, conservative, aggressive} -> the chat (alpha, tau, delta) of a version 3 or 4 .sjtl (sc.py `header_triples`)."""
    import export_sjtl as X
    with open(path, "rb") as f:
        b = f.read(X.HEADER.size + X.DELTAS.size + X.LEVELS.size)
    magic, version, _, _, _, _, _, _, ca, ct, fa, ft, _ = X.HEADER.unpack_from(b, 0)
    if (magic, version) not in ((X.MAGIC_V3, 3), (X.MAGIC_V4, 4)):
        stop(f"{path} has no level triples (a version 3 or 4 .sjtl is needed)")
    delta = X.DELTAS.unpack_from(b, X.HEADER.size)[0]
    lv = X.LEVELS.unpack_from(b, X.HEADER.size + X.DELTAS.size)
    return {"standard": (ca, ct, delta), "conservative": tuple(lv[0:3]), "aggressive": tuple(lv[3:6])}


def read_summary(path):
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = re.match(r"^## .*?  (lm-\S+)  \{'n': (\d+), 'top1': (\d+),", line)
            if m:
                return m.group(1), int(m.group(2)), int(m.group(3))
    stop(f"{path}: no summary line")


def check_tag(tag, level, sjtl8):
    """STOP unless the summary tag is the chat run of `level` with the colloquial table and the reading-channel term (`+rc`)."""
    bad = []
    if not tag.startswith("lm-chat+ctx"):
        bad.append("profile / +ctx")
    bad += [x for x in ("-nodemote", "-noclasses", "-nonames", "+xo:") if x in tag]
    if f"+tl:{sjtl8}" not in tag or "+acg" not in tag:
        bad.append("+tl: / +acg")
    if ("+rl:" in tag) != (level != "standard") or (level != "standard" and f"+rl:{level}" not in tag):
        bad.append("+rl:")
    if f"+colloq:{P.T2_SHA256[:8]}" not in tag or not tag.endswith("+rc"):
        bad.append("+colloq / +rc")
    if bad:
        stop(f"{level}: summary tag {tag} does not match the setting ({', '.join(bad)})")


def check_count(what, n, want):
    if n != want:
        stop(f"{what}: {n} rows, expected {want}")


def cell_pick(r, cell, plain):
    """sc.py `cell_pick`: P4d's eff_ll with the cell's alpha (mu / alpha) plus (nu / alpha) (ch_i - ch_0), then `pick` with the cell's (alpha, tau,
    delta). A non-finite col: the plain pick with `plain`."""
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


def load_standard(a, sjtl8):
    """sc.py `load_standard`: the chat standard run -> (rows [{"sc", "col", "ch"}], rowstats). Everything is read for the first 8 candidates."""
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
    lex, ovl = D.load_lex_scores()
    logp = Q.lex_logp(lex)
    try:
        pre = p4.precompute([{"cands": cs[:TOP_K]} for cs in cands], {"T2": p4.Table(a.t2)}, ovl, D.T.Lex.load().base)
    except (p4.Stop, RuntimeError) as e:
        stop(str(e))
    cols = P.cols_from(pre)
    rows = [{"sc": sc, "col": col, "ch": [Q.ch_of(Q.word_terms(syls, c[2], logp), CAP) for c in cs[:TOP_K]]}
            for sc, col, cs, (_, _, syls) in zip(recs, cols, cands, rows_in)]
    return rows, base


def load_level(a, level, n_want, sjtl8):
    """A level's run -> (rowstats, scores records) after its row counts and summary tag."""
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


def compare(level, rows, recs, rs, hdr):
    """The offline pick of the level's cell against the level run, row by row: the scores record's `c` and ok[c], and the rowstats ok.
    -> the differing row numbers. First the run must be the standard run's candidates with the same ll."""
    diff = [i for i, (r, rec) in enumerate(zip(rows, recs), 1)
            if rec["i"] != i or any(rec[k] != r["sc"][k] for k in ("n", "ng", "ok", "ed", "glen")) or rec.get("ll", [None])[:TOP_K] != r["sc"].get("ll", [None])[:TOP_K]]
    if diff:
        stop(f"{level}: candidates, ng or ll differ from the standard run on {len(diff)} rows, first rows {diff[:10]}")
    out = []
    for i, (r, rec, b) in enumerate(zip(rows, recs, rs), 1):
        j = cell_pick(r, CELL[level], hdr[level])
        if rec["c"] != j or bool(r["sc"]["ok"][j]) != bool(b[0]) or bool(rec["ok"][rec["c"]]) != bool(b[0]):
            out.append(i)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--name", required=True, choices=sorted(ROW_COUNTS))
    ap.add_argument("--rows", required=True)
    ap.add_argument("--sjtl", required=True)
    ap.add_argument("--t2", required=True)
    for k in ("dump", "dump-words", "rowstats", "tinylm-scores", "summary", "cons-rowstats", "cons-scores", "cons-summary", "aggr-rowstats", "aggr-scores", "aggr-summary"):
        ap.add_argument("--" + k, required=True)
    ap.add_argument("--out")
    a = ap.parse_args()
    if sha_file(a.rows) != ROW_SHA256[a.name]:
        stop(f"{a.name}: the --rows file's sha256 is not the registered one")
    sjtl = sha_file(a.sjtl)
    if sjtl != PAIRED_SJTL_SHA256:
        stop("--sjtl is not the small model the terms were measured with (sha256)")
    if sha_file(a.t2) != P.T2_SHA256:
        stop("--t2 is not P4's colloquial table (sha256)")
    hdr = header_triples(a.sjtl)
    if hdr["conservative"] != HEADER["conservative"] or hdr["aggressive"] != HEADER["aggressive"] or hdr["standard"][1:] != HEADER["standard"][1:]:
        stop(f"the .sjtl header's chat triples {hdr} are not the expected {HEADER}")
    rows, base = load_standard(a, sjtl[:8])
    n = len(rows)
    res = {"name": a.name, "n": n, "rows_sha256": ROW_SHA256[a.name], "sjtl_sha256": sjtl, "t2_sha256": P.T2_SHA256, "differing": {}}
    # anchor 1: the standard cell offline equals the standard run (the core's behavior before this change, unchanged)
    std = [i for i, (r, b) in enumerate(zip(rows, base), 1)
           if r["sc"]["c"] != (j := cell_pick(r, CELL["standard"], hdr["standard"])) or bool(r["sc"]["ok"][j]) != bool(b[0])]
    res["differing"]["standard"] = std
    print(f"{a.name} n {n} standard differing rows {len(std)} {std[:10]}")
    for lv in LEVELS:
        rs, recs = load_level(a, lv, n, sjtl[:8])
        d = compare(lv, rows, recs, rs, hdr)
        res["differing"][lv] = d
        print(f"{a.name} n {n} {lv} differing rows {len(d)} {d[:10]}")
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(res, f, indent=1)
    if any(res["differing"].values()):
        stop(f"{a.name}: the core and the offline arithmetic differ")
    print(f"{a.name} OK: 0 differing rows at standard, conservative and aggressive")


if __name__ == "__main__":
    main()
