"""E1a counting: one pass over the model-v6 inputs gives unigram / bigram / trigram expected counts (unmapped) and, from the
same pass, the "mapped" unigram / bigram counts of E4 (b), where a Han run that starts right after a digit or Latin run
starts from <X> instead of <s>.

It wraps experiments/s2/build_counts.py (imported, not edited): the lexicon, the OpenCC conversion, the sentence split, the markup
stripping and `run_expected` (the additive extra-lexicon counting of model-v5 §8) are the original functions, so the unmapped uni / bi
are the model-v6 recipe's own numbers (test_e1 checks the batch result equals `bc.count_batch`). New here:
  - `expected_tri`: expected trigram counts on the same forward-backward lattice as `bc.expected_counts`
    (trigram sequence ["<s>", "<s>"] + words + ["</s>"], like build_counts_text's one-best trigram; a path posterior below `--tri-min`
    is not recorded, default the same 0.01 as the bigram);
  - the mapping of E4 (b), through mapping.han_runs_with_left (the one tokenizer shared with the decoder).
A Han run (>= 2 chars, like the original) whose left neighbour is a digit / Latin run: mapped bigram (<s>, a) becomes (<X>, a),
unigram <X> gets +1 per such run. The right boundary stays </s> (the decoder never needs P(<X> | w), and the end-of-text count
P(</s> | w) must not change). The mapped files carry no trigram.

Outputs, under --out:
  unmapped/counts-<tag>.pkl   {"uni", "bi", "articles", "runs"}   the same shape and keys as experiments/s2 (feeds tools/build_lm.py as is)
  unmapped/tri-<tag>.pkl      {"tri"}                             a separate file so build_lm.merged_counts never loads the trigram table
  mapped/counts-<tag>.pkl     {"uni", "bi", "articles", "runs"}   the same inputs, only the mapping step differs
  meta-<tag>.json             commit, argv, SHA-256 of every input, counts

  python3 count.py wiki --out DIR --articles 200000 --procs 40 --extra-lexicon FILE... [--overlay-add FILE] [--tri-min 0.01]
  python3 count.py text --out DIR --tag colloquial3 --extra-lexicon FILE... [--overlay-add FILE] FILE...
"""
import argparse
import collections
import math
import os
import pickle

import common  # noqa: F401  (sets sys.path)
import build_counts as bc  # noqa: E402
import build_counts_text as bct  # noqa: E402
import ime  # noqa: E402
import mapping  # noqa: E402

X = mapping.X


def expected_tri(lex, text, min_count=bc.EXPECTED_MIN):
    """Expected trigram counts of one run of Han characters; None when the lexicon cannot cut it.
    The words of a path are w1..wn and its trigrams are (<s>,<s>,w1) (<s>,w1,w2) ... (w[n-1],wn,</s>): n + 1 of them, so the counts
    sum to (expected words + 1) per run, and for every context the successors sum to the bigram count of that context."""
    fb = bc.forward_backward(lex, text)
    if fb is None:
        return None
    n, ends, starts, alpha, beta = fb
    Z, NEG = beta[0], -math.inf
    lmin = math.log10(min_count) if min_count > 0 else NEG
    tri = collections.defaultdict(float)
    for j in range(1, n + 1):
        for i, a, lpa in ends[j]:
            la = alpha[i] + lpa + beta[j] - Z
            if la < lmin or la == NEG:
                continue
            if i == 0:
                tri[("<s>", "<s>", a)] += 10 ** la
                if j == n:
                    tri[("<s>", a, "</s>")] += 10 ** la
            for k, b, lpb in starts[j]:
                lb = alpha[i] + lpa + lpb + beta[k] - Z      # a trigram is never larger than the bigram it extends, so lb already gates it
                if lb < lmin or lb == NEG:
                    continue
                if i == 0:
                    tri[("<s>", a, b)] += 10 ** lb
                if k == n:
                    tri[(a, b, "</s>")] += 10 ** lb
                for l, c, lpc in starts[k]:
                    lc = alpha[i] + lpa + lpb + lpc + beta[l] - Z
                    if lc >= lmin and lc > NEG:
                        tri[(a, b, c)] += 10 ** lc
    return tri


def run_expected3(run, tri_min=bc.EXPECTED_MIN):
    """(uni, bi, tri) for one run: uni / bi are `bc.run_expected(run)` itself; tri follows the same additive scheme (model-v5 §8):
    without extra lexicons it is expected_tri on the lexicon; with them, the trigrams of the lexicon without the pack words
    (lex0) plus, from the lexicon with them (lex1), the trigrams that contain a pack word."""
    r = bc.run_expected(run)
    if r is None:
        return None
    pack = bc._W["pack"]
    if not pack:
        return r[0], r[1], expected_tri(bc._W["lex"], run, tri_min)
    tri = expected_tri(bc._W["lex0"], run, tri_min)
    n = len(run)
    if any(run[i:i + L] in pack for i in range(n) for L in bc._W["pack_lens"] if i + L <= n):
        t1 = expected_tri(bc._W["lex"], run, tri_min)
        if t1 is not None:
            for k, e in t1.items():
                if k[0] in pack or k[1] in pack or k[2] in pack:
                    tri[k] += e
    return r[0], r[1], tri


class Acc:
    """What one batch (or one text file) accumulates."""

    def __init__(self):
        self.uni, self.bi, self.tri = collections.Counter(), collections.Counter(), collections.Counter()
        self.dbi = collections.Counter()   # mapped minus unmapped, bigram part (only keys that move)
        self.nx = 0                        # runs that start after an <X>
        self.runs = 0

    def add_run(self, run, left_x, tri_min):
        r = run_expected3(run, tri_min)
        if r is None:
            return
        uni, bi, tri = r
        self.runs += 1
        for w, e in uni.items():
            self.uni[w] += e
        for k, e in bi.items():
            self.bi[k] += e
            if left_x and k[0] == "<s>":
                self.dbi[k] -= e
                self.dbi[(X, k[1])] += e
        for k, e in tri.items():
            self.tri[k] += e
        self.nx += left_x

    def add_text(self, converted, tri_min):
        """A converted text (one sentence or one line): every Han run of >= 2 characters, like the original loops."""
        for run, left_x in mapping.han_runs_with_left(converted):
            if len(run) >= 2:
                self.add_run(run, left_x, tri_min)

    def result(self):
        return self.uni, self.bi, self.tri, self.dbi, self.nx, self.runs


def wiki_sentences(raw):
    """build_counts.count_batch's per-article preprocessing (entities, templates, markup, sentence split, OpenCC), without --mw."""
    phrase, char, maxp = bc._W["conv"]
    t = raw.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").replace("&quot;", '"')
    for _ in range(3):
        t = bc.TEMPLATE.sub("", t)
    for pat, rep in bc.MARKUP:
        t = pat.sub(rep, t)
    for s in bc.SENT.findall(t):
        yield bc.convert(s, phrase, char, maxp)


def count_batch3(texts):
    acc = Acc()
    for raw in texts:
        for s in wiki_sentences(raw):
            acc.add_text(s, bc._W["tri_min"])
    return acc.result()


def _reload_lexicons(overlay_add, extra):
    """bc._init with another overlay-add.tsv (the model-v6 counting used the commit 4d47a50 file, README "overlay-add.tsv 的兩個版本");
    same construction as bc._init: lex = base + [overlay, *extra]; with extras also lex0 = base + [overlay] and the pack words."""
    base = os.path.join(bc.ROOT, "data", "lexicon", "mcbpmf-data.txt")
    bc._W["lex"] = ime.Lexicon(base, overlay=[overlay_add, *extra])
    if extra:
        bc._W["lex0"] = ime.Lexicon(base, overlay=[overlay_add])
        bc._W["pack"] = set(bc._W["lex"].by_word) - set(bc._W["lex0"].by_word)
        bc._W["pack_lens"] = sorted({len(w) for w in bc._W["pack"]})


def init3(extra_lexicons=(), overlay_add=None, tri_min=bc.EXPECTED_MIN):
    extra = tuple(dict.fromkeys(os.path.abspath(f) for f in extra_lexicons))
    bc._init(False, True, False, extra)
    if overlay_add:
        _reload_lexicons(os.path.abspath(overlay_add), extra)
    bc._W["tri_min"] = tri_min


def write_outputs(out, tag, uni, bi, tri, dbi, nx, runs, articles, meta):
    """unmapped/ and mapped/ pickles (see the module docstring). The mapped counts are the unmapped ones plus the moves in dbi."""
    for d in ("unmapped", "mapped"):
        os.makedirs(os.path.join(out, d), exist_ok=True)
    head = {"articles": articles, "runs": runs}
    dump = lambda obj, *p: pickle.dump(obj, open(os.path.join(out, *p), "wb"), protocol=pickle.HIGHEST_PROTOCOL)
    dump({"uni": uni, "bi": bi, **head}, "unmapped", f"counts-{tag}.pkl")
    dump({"tri": tri}, "unmapped", f"tri-{tag}.pkl")
    uni_m, bi_m = collections.Counter(uni), collections.Counter(bi)
    uni_m[X] += nx
    for k, v in dbi.items():
        bi_m[k] += v
    dump({"uni": uni_m, "bi": bi_m, **head}, "mapped", f"counts-{tag}.pkl")
    meta.update({"tag": tag, "runs": runs, "articles": articles, "uni_types": len(uni), "bi_types": len(bi), "tri_types": len(tri),
                 "x_runs": nx, "mapped_bi_types": len(bi_m)})
    common.write_json(os.path.join(out, f"meta-{tag}.json"), meta)
    print(f"{tag}: {runs} runs, {len(uni)} uni, {len(bi)} bi, {len(tri)} tri types; {nx} runs start after <X>", flush=True)


def _inputs(a, files):
    return [p for p in [*files, *a.extra_lexicon, a.overlay_add, os.path.join(bc.ROOT, "data", "lexicon", "mcbpmf-data.txt"),
                        os.path.join(bc.ROOT, "data", "lexicon", "overlay-add.tsv")] if p]


def cmd_wiki(a):
    import multiprocessing as mp
    uni, bi, tri, dbi = (collections.Counter() for _ in range(4))
    nx = runs = arts = 0
    with mp.Pool(a.procs, initializer=init3, initargs=(tuple(a.extra_lexicon), a.overlay_add, a.tri_min)) as pool:
        for u, b, t, d, x, s_ in pool.imap_unordered(count_batch3, bc.batches(bc.articles(a.articles))):
            uni.update(u); bi.update(b); tri.update(t); dbi.update(d); nx += x; runs += s_; arts += 200
            if arts % 10000 == 0:
                print(f"~{arts} articles, {runs} runs, {len(uni)} words, {len(bi)} bigrams, {len(tri)} trigrams", flush=True)
    arts = min(arts, a.articles)
    meta = common.provenance(_inputs(a, [bc.DUMP]), {"tri_min": a.tri_min})
    write_outputs(a.out, str(arts), uni, bi, tri, dbi, nx, runs, arts, meta)


def cmd_text(a):
    init3(a.extra_lexicon, a.overlay_add, a.tri_min)
    acc = Acc()
    for p in a.paths:
        for s in bct.lines(p):
            acc.add_text(bc.convert(s, *bc._W["conv"]), a.tri_min)
    meta = common.provenance(_inputs(a, a.paths), {"tri_min": a.tri_min})
    write_outputs(a.out, a.tag, acc.uni, acc.bi, acc.tri, acc.dbi, acc.nx, acc.runs, 0, meta)


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("wiki", "text"):
        p = sub.add_parser(name)
        p.add_argument("--out", required=True)
        p.add_argument("--extra-lexicon", action="append", default=[], metavar="FILE")
        p.add_argument("--overlay-add", help="overlay-add.tsv to count with instead of the checkout's (model-v6 used commit 4d47a50's file)")
        p.add_argument("--tri-min", type=float, default=bc.EXPECTED_MIN, help="a trigram path below this posterior inside one run is not recorded (default = the bigram's 0.01)")
    sub.choices["wiki"].add_argument("--articles", type=int, default=200000)
    sub.choices["wiki"].add_argument("--procs", type=int, default=max(1, os.cpu_count() - 2))
    sub.choices["text"].add_argument("--tag", required=True)
    sub.choices["text"].add_argument("paths", nargs="+")
    a = ap.parse_args(argv)
    (cmd_wiki if a.cmd == "wiki" else cmd_text)(a)


if __name__ == "__main__":
    main()
