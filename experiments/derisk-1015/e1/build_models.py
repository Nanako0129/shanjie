"""E1b: build B0 (bigram + kn side file), B0m (the same from the mapped counts) and the trigram tables T_k.

  bigram  --counts DIR --out DIR2     DIR holds counts-200000.pkl and counts-colloquial3.pkl (count.py's unmapped/ or mapped/).
                                      Writes DIR2/bigram.sjlm (tools/build_lm.py's build(), PRUNE=2, D=0.75, nothing added) and
                                      DIR2/kn.sjkn (kn-v1: tools/kn_cont.py, theta=1, beta=1, from the same counts) and DIR2/build.json.
                                      No private entries: that step belongs to main and is skipped on both sides of every comparison.
  tri     --counts DIR --lm B0 --out DIR2 [--targets-mb 100,200,400 | --thresholds 1,3,8,20]
                                      Reads DIR/tri-*.pkl (weighted x1 wiki + x5 colloquial through experiments/s2/iter2.merged, rounded like
                                      build_lm, variant-merged like build_lm.merge_variants), writes DIR2/tri-t<thr>.sjt3 for each prune threshold
                                      (a trigram is kept when its weighted count >= thr; context totals are the pre-prune ones) and DIR2/tri-sizes.json.
Size measure: total = bytes of bigram.sjlm + bytes of the .sjt3 file (sjlm_io: 64 + 20 * contexts + 8 * entries); kn.sjkn (1.4 MB)
and classes / tinylm are not counted.
"""
import argparse
import math
import os

import numpy as np

import common
import sjlm_io
import trimodel

import build_lm  # noqa: E402
import ime  # noqa: E402
import iter2  # noqa: E402
import kn_cont  # noqa: E402
import lm as L  # noqa: E402


def counts_inputs(counts, prefix="counts-"):
    return [os.path.join(counts, n.replace("counts-", prefix)) for n, _ in build_lm.CORPORA]


def cmd_bigram(a):
    build_lm.WORK = os.path.abspath(a.counts)
    os.makedirs(a.out, exist_ok=True)
    _, bi, cls = build_lm.merged_counts(extra_overlay=a.extra_overlay)
    data, V, C, E = build_lm.build(cls=cls, extra_overlay=a.extra_overlay)
    model = os.path.join(a.out, "bigram.sjlm")
    open(model, "wb").write(data)
    vocab = L.BigramLM(model, classes=False).vocab
    kn = kn_cont.side_bytes(model, kn_cont.continuation(bi, cls, 1, vocab), cls, 1, 1.0)
    open(os.path.join(a.out, "kn.sjkn"), "wb").write(kn)
    info = common.provenance(counts_inputs(a.counts), {
        "V": V, "contexts": C, "entries": E, "prune": build_lm.PRUNE, "D": build_lm.D, "kn": {"theta": 1, "beta": 1.0},
        "bigram_bytes": len(data), "bigram_sha256": common.sha256_file(model), "kn_sha256": common.sha256_file(os.path.join(a.out, "kn.sjkn")),
        "extra_overlay": a.extra_overlay})
    common.write_json(os.path.join(a.out, "build.json"), info)
    print(f"bigram V={V} contexts={C} entries={E} bytes={len(data)} sha256={info['bigram_sha256']}")


def size_table(build, max_thr=256):
    """{thr: (contexts, entries, bytes)} for thr = 1..max_thr from count histograms (no pass over the arrays per threshold)."""
    suffix = lambda x: np.cumsum(np.bincount(np.minimum(x, max_thr + 1), minlength=max_thr + 2)[::-1])[::-1]
    ent, ctx = suffix(build.c), suffix(build.cmax)
    return {t: (int(ctx[t]), int(ent[t]), sjlm_io.TRI_FIXED + common.TRI_BYTES_PER_CONTEXT * int(ctx[t]) + common.TRI_BYTES_PER_ENTRY * int(ent[t]))
            for t in range(1, max_thr + 1)}


def pick_thresholds(sizes, b0_bytes, targets_mb, want=4):
    """Thresholds whose TOTAL size (b0 + table) is closest in log scale to each target, plus 1 (unpruned). If that gives fewer than `want`
    distinct thresholds (the size curve is steep: several targets can share the nearest integer threshold) the next integer thresholds are added."""
    picks = {1}
    for mb in targets_mb:
        picks.add(min(sizes, key=lambda t: abs(math.log((b0_bytes + sizes[t][2]) / (mb * 1e6)))))
    t = 2
    while len(picks) < want and t in sizes:
        picks.add(t); t += 1
    return sorted(picks)


def cmd_tri(a):
    iter2.WORK = os.path.abspath(a.counts)
    model = os.path.abspath(a.lm)
    lm = L.BigramLM(model, classes=False)
    ids = lm.ids
    sha = bytes.fromhex(common.sha256_file(model))
    tri_corpora = [(n.replace("counts-", "tri-"), w) for n, w in build_lm.CORPORA]
    _, _, tri = iter2.merged([], tri_corpora)
    iter2._counts.clear()
    tri = {k: r for k, v in tri.items() if (r := round(v))}   # build_lm: weighted, rounded to integers, zeros dropped
    lex = ime.Lexicon(os.path.join(build_lm.LEXDIR, "mcbpmf-data.txt"), overlay=ime.overlays(a.extra_overlay))
    cls = build_lm.variant_classes(lex.by_reading)
    tri = trimodel.merge_variants_tri(tri, cls)
    u, v, w, c = trimodel.tri_arrays(tri, ids)
    del tri
    tb = trimodel.TriBuild(u, v, w, c, len(ids))
    sizes = size_table(tb)
    b0 = os.path.getsize(model)
    thrs = [int(x) for x in a.thresholds.split(",")] if a.thresholds else pick_thresholds(sizes, b0, [float(x) for x in a.targets_mb.split(",")])
    os.makedirs(a.out, exist_ok=True)
    rows = []
    for t in thrs:
        data = sjlm_io.write_tri(tb.tables(t, sha))
        path = os.path.join(a.out, f"tri-t{t}.sjt3")
        open(path, "wb").write(data)
        C, E, nbytes = sizes[t] if t in sizes else tb.stats(t)
        assert nbytes == len(data), (nbytes, len(data))
        rows.append({"threshold": t, "contexts": C, "entries": E, "tri_bytes": nbytes, "total_bytes": b0 + nbytes, "sha256": common.sha256_file(path)})
        print(f"t{t}: contexts={C} entries={E} tri_bytes={nbytes} total_MB={(b0 + nbytes) / 1e6:.1f}", flush=True)
    info = common.provenance(counts_inputs(a.counts, "tri-") + [model], {
        "trigram_types_all": int(len(c)), "bigram_bytes": b0, "tables": rows, "D_tri": tb.D,
        "curve": {str(t): list(s) for t, s in sizes.items() if t <= 32 or t % 16 == 0}})
    common.write_json(os.path.join(a.out, "tri-sizes.json"), info)


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("bigram", "tri"):
        p = sub.add_parser(name)
        p.add_argument("--counts", required=True)
        p.add_argument("--out", required=True)
        p.add_argument("--extra-overlay", help="only if the counting lexicon and ime.OVERLAYS do not already contain the names layer (they do at this commit)")
    sub.choices["tri"].add_argument("--lm", required=True)
    sub.choices["tri"].add_argument("--targets-mb", default="100,200,400")
    sub.choices["tri"].add_argument("--thresholds")
    a = ap.parse_args(argv)
    (cmd_bigram if a.cmd == "bigram" else cmd_tri)(a)


if __name__ == "__main__":
    main()
