"""Counting wrapper for the reading-aware experiment (contract docs/contracts/reading-aware-0.md 3.4, "計數與建模").

It is derisk-1015/e1/count.py's recipe with one change: every edge of the forward-backward lattice is counted as its UNIT instead of its
word string (units.py). Lexicon, OpenCC conversion, sentence split, markup stripping, the additive extra-lexicon scheme and the 0.01
posterior floor are the original functions (experiments/s2/build_counts.py and e1/count.py are imported, not edited). Only the unmapped
uni/bi counts are written (the files tools/build_lm.py reads); no trigram, no mapped counts (this slice rebuilds no trigram table).

  --identity  every unit is its word string. The output must then equal e1/count.py's unmapped counts, and build_model.py must rebuild B0
              byte for byte (contract 4, anchor "計數"); without --identity the units of multi-reading characters carry the reading.

  python3 count_units.py wiki --out DIR --articles 200000 --procs 44 --overlay-add FILE --extra-lexicon FILE... [--identity]
                              [--hetero heterophony1.list] [--overrides tools/reading_overrides.tsv]
  python3 count_units.py text --out DIR --tag colloquial3 --overlay-add FILE --extra-lexicon FILE... [--identity] FILE...
Outputs under --out: unmapped/counts-<tag>.pkl ({"uni","bi","articles","runs"}), meta-<tag>.json (inputs' SHA-256, label statistics).
"""
import argparse
import collections
import math
import os
import pickle

import rcommon
import units
import build_counts as bc  # noqa: E402
import build_counts_text as bct  # noqa: E402
import count  # noqa: E402  (derisk-1015/e1/count.py)
import common  # noqa: E402  (derisk-1015/e1/common.py)
import mapping  # noqa: E402
import ime  # noqa: E402

_U = {}


def han_runs_with_offset(text):
    """[(run, offset)] of every maximal Han run of `text` (mapping.tokenize is the one tokenizer count.py uses too)."""
    out, pos = [], 0
    for kind, raw, _ in mapping.tokenize(text):
        if kind == "han":
            out.append((raw, pos))
        pos += len(raw)
    return out


def expected_units(lex, run, unit_at, min_count=bc.EXPECTED_MIN):
    """bc.expected_counts with the edge (word a at position i) counted as unit_at(a, i). unit_at = lambda w, i: w gives its result exactly
    (same operations in the same order)."""
    fb = bc.forward_backward(lex, run)
    if fb is None:
        return None
    n, ends, starts, alpha, beta = fb
    Z, NEG = beta[0], -float("inf")
    lmin = math.log10(min_count) if min_count > 0 else NEG
    uni, bi = collections.defaultdict(float), collections.defaultdict(float)
    for j in range(1, n + 1):
        for i, a, lpa in ends[j]:
            la = alpha[i] + lpa + beta[j] - Z
            if la < lmin or la == NEG:
                continue
            ua = unit_at(a, i)
            uni[ua] += 10 ** la
            if i == 0:
                bi[("<s>", ua)] += 10 ** la
            if j == n:
                bi[(ua, "</s>")] += 10 ** la
            for k, b, lpb in starts[j]:
                lb = alpha[i] + lpa + lpb + beta[k] - Z
                if lb >= lmin and lb > NEG:
                    bi[(ua, unit_at(b, j))] += 10 ** lb
    return uni, bi


def run_units(run, unit_at):
    """bc.run_expected with units: without extra lexicons expected_units on the lexicon; with them the additive scheme (model-v5 section 8):
    lex0's counts plus, from lex, the units of the pack words and the bigrams with a pack word at one end."""
    W = bc._W
    pack = W["pack"]
    if not pack:
        return expected_units(W["lex"], run, unit_at)
    r0 = expected_units(W["lex0"], run, unit_at)
    if r0 is None:
        return None
    n = len(run)
    if not any(run[i:i + L] in pack for i in range(n) for L in W["pack_lens"] if i + L <= n):
        return r0
    r1 = expected_units(W["lex"], run, unit_at)
    if r1 is None:
        return r0
    uni, bi = r0
    for u, e in r1[0].items():
        if units.base_word(u) in pack:
            uni[u] += e
    for k, e in r1[1].items():
        if units.base_word(k[0]) in pack or units.base_word(k[1]) in pack:
            bi[k] += e
    return uni, bi


class UnitAcc:
    """What one batch (or one text file) accumulates: Acc of count.py without the trigram and the mapped counts."""

    def __init__(self, labeller=None):
        self.labeller = labeller            # None = identity labelling
        self.uni, self.bi = collections.Counter(), collections.Counter()
        self.runs = 0

    def add_text(self, converted):
        """One converted sentence: every Han run of >= 2 characters. Labels come from the variant-merged sentence (bc.fold), so a position's
        context is the same whatever the lattice does; positions outside the run (punctuation, digits) are context too."""
        folded = bc.fold(converted)
        for run, off in han_runs_with_offset(converted):
            if len(run) < 2:
                continue
            if self.labeller is None:
                unit_at = lambda w, i: w
            else:
                memo = {}
                unit_at = lambda w, i, off=off, memo=memo: memo.get((w, i)) or memo.setdefault((w, i), self.labeller.unit(w, off + i, folded))
            r = run_units(run, unit_at)
            if r is None:
                continue
            self.runs += 1
            for u, e in r[0].items():
                self.uni[u] += e
            for k, e in r[1].items():
                self.bi[k] += e


def make_labeller(identity, hetero=None, overrides=None):
    if identity:
        return None
    lex = ime.Lexicon(rcommon.LEXICON, overlay=ime.overlays())
    return units.Labeller(rcommon.char_readings(lex), units.load_overrides(overrides) if overrides else None,
                          units.load_hetero(hetero) if hetero else None)


def init_units(extra_lexicons, overlay_add, identity, hetero, overrides):
    count.init3(extra_lexicons, overlay_add)
    _U["labeller"] = make_labeller(identity, hetero, overrides)


def count_batch_units(texts):
    acc = UnitAcc(_U["labeller"])
    for raw in texts:
        for s in count.wiki_sentences(raw):
            acc.add_text(s)
    fb = _U["labeller"].fallbacks if _U["labeller"] else collections.Counter()
    return acc.uni, acc.bi, acc.runs, fb


def write_outputs(out, tag, uni, bi, runs, articles, fallbacks, meta):
    os.makedirs(os.path.join(out, "unmapped"), exist_ok=True)
    with open(os.path.join(out, "unmapped", f"counts-{tag}.pkl"), "wb") as f:
        pickle.dump({"uni": uni, "bi": bi, "articles": articles, "runs": runs}, f, protocol=pickle.HIGHEST_PROTOCOL)
    nunit = sum(1 for k in uni if rcommon.U in k)
    meta.update({"tag": tag, "runs": runs, "articles": articles, "uni_types": len(uni), "bi_types": len(bi), "unit_types": nunit,
                 "label_fallbacks": dict(fallbacks)})
    common.write_json(os.path.join(out, f"meta-{tag}.json"), meta)
    print(f"{tag}: {runs} runs, {len(uni)} uni ({nunit} with a reading), {len(bi)} bi types; label fallbacks {sum(fallbacks.values())}", flush=True)


def _inputs(a, files):
    extra = [p for p in (a.hetero, a.overrides) if p]
    return count._inputs(a, files) + extra


def cmd_wiki(a):
    import multiprocessing as mp
    uni, bi, fb = collections.Counter(), collections.Counter(), collections.Counter()
    runs = arts = 0
    args = (tuple(a.extra_lexicon), a.overlay_add, a.identity, a.hetero, a.overrides)
    init_units(*args)   # in the parent first, like count.cmd_wiki: a failure stops here, not in every Pool worker's initializer
    with mp.Pool(a.procs, initializer=init_units, initargs=args) as pool:
        for u, b, r, f in pool.imap_unordered(count_batch_units, bc.batches(bc.articles(a.articles))):
            uni.update(u); bi.update(b); fb.update(f); runs += r; arts += 200
            if arts % 10000 == 0:
                print(f"~{arts} articles, {runs} runs, {len(uni)} units, {len(bi)} bigrams", flush=True)
    arts = min(arts, a.articles)
    meta = common.provenance(_inputs(a, [bc.DUMP]), {"identity": a.identity})
    write_outputs(a.out, str(arts), uni, bi, runs, arts, fb, meta)


def cmd_text(a):
    init_units(tuple(a.extra_lexicon), a.overlay_add, a.identity, a.hetero, a.overrides)
    acc = UnitAcc(_U["labeller"])
    for p in a.paths:
        for s in bct.lines(p):
            acc.add_text(bc.convert(s, *bc._W["conv"]))
    meta = common.provenance(_inputs(a, a.paths), {"identity": a.identity})
    write_outputs(a.out, a.tag, acc.uni, acc.bi, acc.runs, 0, acc.labeller.fallbacks if acc.labeller else collections.Counter(), meta)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("wiki", "text"):
        p = sub.add_parser(name)
        p.add_argument("--out", required=True)
        p.add_argument("--extra-lexicon", action="append", default=[], metavar="FILE")
        p.add_argument("--overlay-add", help="overlay-add.tsv to count with (model-v6 used commit 4d47a50's file), as in e1/count.py")
        p.add_argument("--identity", action="store_true", help="every unit is its word string (the anchor run)")
        p.add_argument("--hetero", help="heterophony1.list (primary readings, tools/readings.py rule 3); without it the primary reading is the lexicon's first")
        p.add_argument("--overrides", default=os.path.join(rcommon.ROOT, "tools", "reading_overrides.tsv"))
    sub.choices["wiki"].add_argument("--articles", type=int, default=200000)
    sub.choices["wiki"].add_argument("--procs", type=int, default=max(1, os.cpu_count() - 2))
    sub.choices["text"].add_argument("--tag", required=True)
    sub.choices["text"].add_argument("paths", nargs="+")
    a = ap.parse_args(argv)
    if a.identity:
        a.hetero = a.overrides = None
    (cmd_wiki if a.cmd == "wiki" else cmd_text)(a)


if __name__ == "__main__":
    main()
