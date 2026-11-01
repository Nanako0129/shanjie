#!/usr/bin/env python3
"""LOSS-0 (research log 2026-10-31): for each homophone error row of HOMO-0, the layer where the gold loses.

  python3 experiments/loss0/loss0.py            print the tables (per profile x set group)
  python3 experiments/loss0/loss0.py --rows F   also write one TSV line per row to F (set, profile, row number, layer, D2, gold rank,
                                                label, tags, then for layers 4 and 5 the reranker margin (best gold ll minus
                                                chosen ll), the chosen candidate and that gold candidate (else empty), and last the
                                                difference pair; keep it in the private cache)

Reads HOMO-0's saved outputs under ~/.cache/shanjie/triage/homo0 and RUHI-0's human labels (triage/ruhi0/labels.tsv,
triage/ruhi0-big/labels.tsv) (main ba8d3860, triage setting S; no decoding here):
<set>-<prof>.map/.txt, .full.dump (top 64), .full.words (segmentation; k 0 = gold, k c+1 = chosen), .full.sc (top-n window:
decoder n-gram score, reranker log-likelihood, lenient ok flags, chosen index), .norc.r and .notri.r (rerun right or wrong).
Primary layer, first match in the registered order:
  1 outside top 64      no lenient-gold candidate among the 64
  2 outside rerank win  gold ranked 9..64 (beyond the reranker's window n)
  3 broken by rerank    the decoder's first candidate is gold, the pipeline chose another
  4 rerank prefers gold decoder first is wrong; some gold candidate in the window has a higher reranker ll than the chosen one
  5 rerank also wrong   the rest
Layers 4 and 5 are split as triage's D2 (tools/triage.py classify) in the formal profile only: a gated (n-gram margin >= tau, the
rerank cannot move the pick), b (core pick with delta 0 would take a gold candidate: delta keeps the decoder's pick), c (the fused
score ng + alpha * ll prefers a wrong candidate). Not in chat: there the core adds the colloquial and reading-channel terms to the
fusion, which triage.pick does not reproduce (triage.plain_pick_applies). Added after the code review, as a breakdown of the
registered layers; the formal choices are checked against triage.pick row by row.
Needs data/lm/bigram.sjlm and data/lm/tinylm.sjtl in this checkout: the files HOMO-0 ran with (main ba8d3860, pinned in HOMO0_MODELS),
not necessarily what the checkout's data/*.sha256 names.
Tags (not exclusive): HOMO-0's compound / segmentation / sentence start / seen, no-reading-channel fixes, no-trigram fixes, and
RUHI-0's human label (near, far, undecidable; else unlabelled). Observed phenomena, not causes.
"""
import argparse
import json
import os
import struct
import sys
from collections import Counter, defaultdict

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "tools"))
import bench  # noqa: E402  (lenient matching, the suite's rule)
import triage  # noqa: E402  (core pick and the .sjtl alpha/tau, as the triage buckets use them)

CACHE = os.path.expanduser("~/.cache/shanjie")
H = f"{CACHE}/triage/homo0"
SETS = ["dev302", "typing76", "probe", "fw", "user-reported", "cvtune", "wikitune"]
SMALL = {"dev302", "typing76", "probe", "fw", "user-reported"}
LAYERS = ["1 outside top 64", "2 outside rerank window", "3 broken by rerank/fusion", "4 rerank prefers gold, not adopted",
          "5 rerank also prefers wrong"]
HOMO0_MODELS = {"bigram.sjlm": "e3fb60561ea0c9a1908e8c4fc2cd8c28c36ffacd015ba45c17223a046148eb5a",   # data/*.sha256 at ba8d3860
                "tinylm.sjtl": "f7d0ac6377bfe973cf4239a949d7a763f1acb0095e50bc6c1323bd3ed09d3478"}
TAGS = ["compound", "segmentation", "sentence start", "seen", "no-rc fixes", "no-tri fixes"]


def load_bigram(path):
    """HOMO-0's reader: has(u, w) is True when the bigram (u, w) is stored in the pruned model."""
    b = open(path, "rb").read()
    if b[:8] != b"SJLM0001":
        sys.exit(f"STOP: {path} is not an SJLM0001 model")
    V, N, eos, D = struct.unpack_from("<IQQd", b, 8)
    p = 8 + struct.calcsize("<IQQd")
    ids = {}
    for i in range(V):
        (n,) = struct.unpack_from("<H", b, p); p += 2
        ids[b[p:p + n].decode("utf-8")] = i; p += n
    p += 8 * V
    (C,) = struct.unpack_from("<I", b, p); p += 4
    ctx = struct.unpack_from(f"<{C}I", b, p); p += 4 * C
    p += 8 * C
    off = struct.unpack_from(f"<{C + 1}I", b, p); p += 4 * (C + 1)
    (E,) = struct.unpack_from("<I", b, p); p += 4
    nxt = struct.unpack_from(f"<{E}I", b, p)
    pos = {v: i for i, v in enumerate(ctx)}

    def has(u, w):
        iu, iw = ids.get(u), ids.get(w)
        if iu is None or iw is None or iu not in pos:
            return False
        lo, hi = off[pos[iu]], off[pos[iu] + 1]
        end = hi
        while lo < hi:
            mid = (lo + hi) // 2
            if nxt[mid] < iw:
                lo = mid + 1
            else:
                hi = mid
        return lo < end and nxt[lo] == iw
    return has


def spans(words):
    out, i = [], 0
    for w in words:
        out.append((i, i + len(w), w)); i += len(w)
    return out


def features(chosen_words, gold_words, ctx, has):
    """HOMO-0's definitions, compared on the lenient strings (as triage's diff_segments), with the gold split the decoder scored."""
    ch, go = spans(chosen_words), spans(gold_words)
    cs, gs = bench.lenient("".join(chosen_words)), bench.lenient("".join(gold_words))
    if len(cs) != len(gs) or len(cs) != len("".join(gold_words)) or len(cs) != len("".join(chosen_words)):
        return {"len": True, "pair": "?"}
    diff = {i for i in range(len(cs)) if cs[i] != gs[i]}
    if not diff:
        return {"len": False, "same": True, "pair": "="}
    gold_spans = {(a, b) for a, b, _ in go}
    glue = any(b - a >= 2 and any(a <= i < b for i in diff) and any(a <= i < b and i not in diff for i in range(a, b))
               and (a, b) not in gold_spans for a, b, _ in ch)
    lo, hi = min(diff), max(diff) + 1
    cov = lambda sp: {(a, b) for a, b, _ in sp if a < hi and b > lo}
    gi = [k for k, (a, b, _) in enumerate(go) if a < hi and b > lo]
    left = go[gi[0] - 1][2] if gi[0] > 0 else None
    right = go[gi[-1] + 1][2] if gi[-1] + 1 < len(go) else None
    seen = (left is not None and has(left, go[gi[0]][2])) or (right is not None and has(go[gi[-1]][2], right))
    pair = "".join(cs[i] for i in sorted(diff)) + "→" + "".join(gs[i] for i in sorted(diff))
    zai = all({cs[i], gs[i]} == {"在", "再"} for i in diff)
    return {"zai": zai, "len": False, "compound": glue, "segmentation": cov(ch) != cov(go), "sentence start": lo == 0 and not ctx,
            "seen": seen, "pair": pair}


def labels():
    lab = {}
    for f in [f"{CACHE}/triage/ruhi0/labels.tsv", f"{CACHE}/triage/ruhi0-big/labels.tsv"]:
        L = [l.rstrip("\n").split("\t") for l in open(f, encoding="utf-8")]
        for t in L[1:]:
            lab[(t[0], t[1], t[2])] = t[7]
    return lab


def dump_by_row(path):
    out = defaultdict(list)
    for l in open(path, encoding="utf-8"):
        r, k, s, sc = l.rstrip("\n").split("\t")
        out[int(r)].append((int(k), s, float(sc)))
    return out


def window(d):
    """The candidates the rerank may choose (triage.pick skips the manual mask)."""
    m = set(d.get("masked", ()))
    return [k for k in range(d["n"]) if k not in m]


def layer_of(gold_rank, d, par, plain):
    """(layer index 0..4, gold ll minus chosen ll or None, D2 sub-split or "" when not formal)."""
    if gold_rank is None:
        return 0, None, ""
    if gold_rank > d["n"]:
        return 1, None, ""
    if d["ok"][0] == 1:
        return 2, None, ""
    if "ll" not in d or len(d["ng"]) < 2:
        sys.exit(f"STOP: row {d['i']}: gold is in the window but no rerank ran (no ll); the registration has no layer for it")
    alpha, tau, _ = par
    sub = ""
    if plain:
        if d["ng"][0] - d["ng"][1] >= tau:
            sub = "a gated by tau"
        elif d["ok"][triage.pick(d["ng"], d["ll"], alpha, tau, 0.0, d.get("masked", ()))]:
            sub = "b pick at delta 0 is gold"
        else:
            sub = "c fused score prefers wrong"
    golds = [k for k in window(d) if d["ok"][k] == 1]
    if not golds:
        sys.exit(f"STOP: row {d['i']}: every gold candidate in the window is masked; the registration has no layer for it")
    gold_ll = max(d["ll"][k] for k in golds)
    return (3 if gold_ll > d["ll"][d["c"]] else 4), gold_ll - d["ll"][d["c"]], sub


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows")
    a = ap.parse_args()
    for f, want in HOMO0_MODELS.items():   # the models HOMO-0's outputs came from, not whatever this checkout ships
        if not os.path.exists(f"{REPO}/data/lm/{f}") or bench.sha_file(f"{REPO}/data/lm/{f}") != want:
            sys.exit(f"STOP: data/lm/{f} is not the file HOMO-0 used (main ba8d3860, SHA-256 {want[:12]}...); copy that one in")
    lm = f"{REPO}/data/lm/bigram.sjlm"
    par = triage.tinylm_params(f"{REPO}/data/lm/tinylm.sjtl")
    has = load_bigram(lm)
    lab = labels()
    T = defaultdict(lambda: defaultdict(Counter))          # (prof, group) -> layer -> counter of tags, rows, pairs
    PAIRS = defaultdict(lambda: defaultdict(Counter))
    LLD = defaultdict(list)                                 # layer 4: gold ll minus chosen ll
    out = open(a.rows + ".part", "w", encoding="utf-8") if a.rows else None
    for s in SETS:
        for p in ["chat", "formal"]:
            base = f"{H}/{s}-{p}"
            if not os.path.exists(f"{base}.full.sc"):
                continue
            mp = [l.rstrip("\n").split("\t") for l in open(f"{base}.map")]
            rows = open(f"{base}.txt", encoding="utf-8").read().splitlines()
            sc = [json.loads(l) for l in open(f"{base}.full.sc")]
            dump = dump_by_row(f"{base}.full.dump")
            surf = {(r, k): cand for r, c in dump.items() for k, cand, _ in c}
            W = defaultdict(dict)
            for l in open(f"{base}.full.words", encoding="utf-8"):
                r, k, w = l.rstrip("\n").split("\t", 2)
                r, k = int(r), int(k)
                if k == 0:
                    W[r][0] = None if w == "-" else w.split()
                    continue
                if surf.get((r, k)) != "".join(w.split()):   # as triage.read_cli_files: the words must spell the dump's candidate
                    sys.exit(f"STOP: {s} {p} row {r} rank {k}: dump-words does not match dump")
                W[r][k] = w.split()
            full = [l.split("\t")[1] == "1" for l in open(f"{base}.full.r")]
            norc = [l.split("\t")[1] == "1" for l in open(f"{base}.norc.r")]
            notri = [l.split("\t")[1] == "1" for l in open(f"{base}.notri.r")]
            if not (len(mp) == len(rows) == len(sc) == len(full) == len(norc) == len(notri)):
                sys.exit(f"STOP: {s} {p}: file lengths differ")
            grp = "small" if s in SMALL else s
            for i, (m, row, d) in enumerate(zip(mp, rows, sc), 1):
                if d["i"] != i or 0 not in W[i] or d["c"] + 1 not in W[i] or i not in dump:
                    sys.exit(f"STOP: {s} {p} row {i}: a saved output is missing")
                if "idx" in d:
                    sys.exit(f"STOP: {s} {p} row {i}: the record has idx (pool order differs from the n-best); not handled")
                ctx, gold = row.split("|")[0], row.split("|")[1]
                g = bench.lenient(gold)
                cands = sorted(dump[i])
                ranks = [k for k, cand, _ in cands if bench.lenient(cand) == g]
                gold_rank = min(ranks) if ranks else None
                # the window's ok flags must equal the dump's lenient match, position by position, and the row must be wrong
                want = [int(bench.lenient(cand) == g) for _, cand, _ in cands[:d["n"]]]
                if d["ok"] != want:
                    sys.exit(f"STOP: {s} {p} row {i}: the window's ok flags {d['ok']} differ from the dump's lenient match {want}")
                if full[i - 1] or d["ok"][d["c"]] == 1:
                    sys.exit(f"STOP: {s} {p} row {i}: the row is right in the saved run; HOMO-0's rows must all be wrong")
                plain = triage.plain_pick_applies(p, True)
                if plain and d["c"] != triage.pick(d["ng"], d["ll"], *par[p], d.get("masked", ())):
                    sys.exit(f"STOP: {s} {p} row {i}: the saved choice is not triage.pick with this .sjtl's alpha, tau, delta")
                L, lld, sub = layer_of(gold_rank, d, par[p], plain)
                if gold_rank is not None:
                    if gold_rank not in W[i]:
                        sys.exit(f"STOP: {s} {p} row {i}: no words for the gold candidate at rank {gold_rank}")
                    gold_words = W[i][gold_rank]
                else:   # as triage.classify: the rank-0 split, or the truth's characters when there is no lexicon path
                    gold_words = W[i][0] if W[i][0] is not None else list(gold)
                f = features(W[i][d["c"] + 1], gold_words, ctx, has)
                tags = [t for t in TAGS[:4] if f.get(t)]
                if norc[i - 1]:
                    tags.append("no-rc fixes")
                if notri[i - 1]:
                    tags.append("no-tri fixes")
                hl = lab.get((s, p, m[0]), "unlabelled")
                key = (p, grp)
                T[key][L]["rows"] += 1
                T[key][L]["zai"] += bool(f.get("zai"))
                T[key][L]["len-mismatch (no tags)"] += f["len"]
                if sub:
                    T[key][L]["sub:" + sub] += 1
                T[key][L]["label:" + hl] += 1
                for t in tags:
                    T[key][L]["tag:" + t] += 1
                PAIRS[key][L][f["pair"]] += 1
                if L == 3:
                    LLD[key].append(lld)
                if out:
                    extra = ["", "", ""]
                    if L in (3, 4):   # L5-0: the margin and the two candidates behind it
                        bg = max((k for k in window(d) if d["ok"][k] == 1), key=lambda k: d["ll"][k])
                        extra = [f"{lld:.6f}", surf[(i, d["c"] + 1)], surf[(i, bg + 1)]]
                    out.write("\t".join([s, p, m[0], str(L + 1), sub, str(gold_rank or 0), hl, ",".join(tags)] + extra + [f["pair"]]) + "\n")
    for key in sorted(T):
        n = sum(T[key][L]["rows"] for L in T[key])
        zai = sum(T[key][L]["zai"] for L in T[key])
        print(f"\n== {key[0]} {key[1]}: {n} rows (在/再 {zai})")
        for L in range(5):
            c = T[key][L]
            if not c["rows"]:
                continue
            pc = PAIRS[key][L]
            lab_s = " ".join(f"{k[6:]} {v}" for k, v in sorted(c.items()) if k.startswith("label:"))
            tag_s = " ".join(f"{k[4:]} {v}" for k, v in sorted(c.items()) if k.startswith("tag:"))
            sub_s = " ".join(f"{k[4:]} {v}" for k, v in sorted(c.items()) if k.startswith("sub:"))
            print(f"  {LAYERS[L]:36s} {c['rows']:4d} ({c['rows'] / n * 100:4.1f}%)  在/再 {c['zai']:2d}  pairs {len(pc):3d}"
                  f"  len-mismatch {c['len-mismatch (no tags)']}")
            print(f"      labels: {lab_s}")
            print(f"      tags:   {tag_s}")
            if sub_s:
                print(f"      D2:     {sub_s}")
            print(f"      top pairs: " + " ".join(f"{x}×{y}" for x, y in pc.most_common(6)))
        if LLD[key]:
            v = sorted(LLD[key])
            q = lambda x: v[min(len(v) - 1, int(x * len(v)))]
            print(f"  layer 4 reranker ll margin (gold - chosen): n {len(v)}, median {q(0.5):.2f}, p25 {q(0.25):.2f}, p75 {q(0.75):.2f}")
    if out:
        out.close()
        os.replace(a.rows + ".part", a.rows)


if __name__ == "__main__":
    main()
