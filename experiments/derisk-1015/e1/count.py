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
  python3 count.py text --out DIR --tag colloquial3 --extra-lexicon FILE... [--overlay-add FILE] [--holdout-guard [ROWS...]] FILE...
  python3 count.py wiki ... --shard K/N          only articles i with i % N == K (build_counts.py --shard's rule); the tag becomes
                                                 <articles>-shard<K>of<N>; the N processes run side by side, each with its own --procs
  python3 count.py merge --out DIR --tag T SHARD_TAG...   sums the unmapped / mapped / tri pickles of the shard tags (wiki shards, or the
                                                 `text` runs of the TSV shards of one source) into tag T, deletes nothing
`text` reads a path the way build_counts_text.lines() does: .bz2 is decompressed, a path with ".tsv" in it contributes its last tab field.
"""
import argparse
import collections
import json
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
        self.skipped = 0                   # runs left out by the holdout guard

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

    def add_text(self, converted, tri_min, guard=None):
        """A converted text (one sentence or one line): every Han run of >= 2 characters, like the original loops.
        guard: None, or the holdout_guard Matcher; a run that contains a guarded sentence is not counted (self.skipped), same test as
        build_counts_text.main (after the length filter, on the folded run)."""
        for run, left_x in mapping.han_runs_with_left(converted):
            if len(run) >= 2:
                if guard is not None and guard.any(bc.fold(run)):
                    self.skipped += 1
                    continue
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
        yield bc.convert(s, phrase, char, maxp, trad_tw_phrases=bc._W.get("trad_tw", True))


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


def init3(extra_lexicons=(), overlay_add=None, tri_min=bc.EXPECTED_MIN, trad_tw_phrases=True):
    extra = tuple(dict.fromkeys(os.path.abspath(f) for f in extra_lexicons))
    bc._init(False, True, False, extra, trad_tw_phrases=trad_tw_phrases)
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


def shard_of(spec):
    """"K/N" -> (K, N); (0, 1) when not given. Same rule as experiments/s2/build_counts.py --shard."""
    k, n = map(int, spec.split("/")) if spec else (0, 1)
    if not 0 <= k < n:
        raise ValueError(f"--shard {spec}: need 0 <= K < N")
    return k, n


def shard_filter(it, k, n):
    return (x for i, x in enumerate(it) if i % n == k)


def cmd_wiki(a):
    import multiprocessing as mp
    uni, bi, tri, dbi = (collections.Counter() for _ in range(4))
    nx = runs = arts = 0
    init3(tuple(a.extra_lexicon), a.overlay_add, a.tri_min, not a.no_trad_tw_phrases)   # in the parent first (OpenCC tables, lexicon): a failure stops here, not in every Pool worker's initializer (which respawns forever)
    k, n = shard_of(a.shard)
    mine = shard_filter(bc.articles(a.articles), k, n)
    with mp.Pool(a.procs, initializer=init3, initargs=(tuple(a.extra_lexicon), a.overlay_add, a.tri_min, not a.no_trad_tw_phrases)) as pool:
        for u, b, t, d, x, s_ in pool.imap_unordered(count_batch3, bc.batches(mine)):
            uni.update(u); bi.update(b); tri.update(t); dbi.update(d); nx += x; runs += s_; arts += 200
            if arts % 10000 == 0:
                print(f"~{arts} articles, {runs} runs, {len(uni)} words, {len(bi)} bigrams, {len(tri)} trigrams", flush=True)
    if a.shard:
        arts = len(range(k, a.articles, n))   # what the shard was asked to count (the dump may hold fewer: then every shard's slice is shorter by the same rule)
        tag = f"{a.articles}-shard{k}of{n}"
    else:
        arts = min(arts, a.articles)
        tag = str(arts)
    meta = common.provenance(_inputs(a, [bc.DUMP]), {"tri_min": a.tri_min, "shard": a.shard, "no_trad_tw_phrases": a.no_trad_tw_phrases})
    write_outputs(a.out, tag, uni, bi, tri, dbi, nx, runs, arts, meta)


def cmd_text(a):
    guard = bc.guard_matcher(a.holdout_guard, not a.no_trad_tw_phrases)   # first: a bad rows file stops here, before the lexicon loads
    init3(a.extra_lexicon, a.overlay_add, a.tri_min, not a.no_trad_tw_phrases)
    acc = Acc()
    for p in a.paths:
        for s in bct.lines(p):
            acc.add_text(bc.convert(s, *bc._W["conv"], trad_tw_phrases=not a.no_trad_tw_phrases), a.tri_min, guard)
    extra = {"tri_min": a.tri_min, "no_trad_tw_phrases": a.no_trad_tw_phrases}
    if guard is not None:
        extra.update(holdout_guard=a.holdout_guard, guard_skipped_runs=acc.skipped)   # only when on: off keeps the meta keys as before
    meta = common.provenance(_inputs(a, a.paths), extra)
    write_outputs(a.out, a.tag, acc.uni, acc.bi, acc.tri, acc.dbi, acc.nx, acc.runs, 0, meta)
    if guard is not None:
        print(f"holdout-guard: skipped {acc.skipped} runs")


def cmd_merge(a):
    """Sum the shards' pickles into one tag, one file at a time (the counts are additive: Counter sums, `articles` and `runs` add up)."""
    for d, name, keys in (("unmapped", "counts", ("uni", "bi")), ("unmapped", "tri", ("tri",)), ("mapped", "counts", ("uni", "bi"))):
        total = {k: collections.Counter() for k in keys}
        head = {"articles": 0, "runs": 0} if name == "counts" else {}
        for t in a.shards:
            part = pickle.load(open(os.path.join(a.out, d, f"{name}-{t}.pkl"), "rb"))
            for k in keys:
                total[k].update(part[k])
            for k in head:
                head[k] += part[k]
        os.makedirs(os.path.join(a.out, d), exist_ok=True)
        pickle.dump({**total, **head}, open(os.path.join(a.out, d, f"{name}-{a.tag}.pkl"), "wb"), protocol=pickle.HIGHEST_PROTOCOL)
        print(f"{d}/{name}-{a.tag}.pkl: " + ", ".join(f"{len(v)} {k}" for k, v in total.items()), flush=True)
    metas = [json.load(open(os.path.join(a.out, f"meta-{t}.json"))) for t in a.shards]
    common.write_json(os.path.join(a.out, f"meta-{a.tag}.json"), {"tag": a.tag, "merged_from": a.shards, "shard_meta": metas})


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("wiki", "text"):
        p = sub.add_parser(name)
        p.add_argument("--out", required=True)
        p.add_argument("--extra-lexicon", action="append", default=[], metavar="FILE")
        p.add_argument("--overlay-add", help="overlay-add.tsv to count with instead of the checkout's (model-v6 used commit 4d47a50's file)")
        p.add_argument("--no-trad-tw-phrases", action="store_true", help="SJ-LM-7 contract 2.2, same as build_counts.py: traditional sentences skip OpenCC TWPhrases (use it whenever the bigram counts did)")
        p.add_argument("--tri-min", type=float, default=bc.EXPECTED_MIN, help="a trigram path below this posterior inside one run is not recorded (default = the bigram's 0.01)")
    sub.choices["wiki"].add_argument("--articles", type=int, default=200000)
    sub.choices["wiki"].add_argument("--procs", type=int, default=max(1, os.cpu_count() - 2))
    sub.choices["text"].add_argument("--tag", required=True)
    sub.choices["text"].add_argument("--holdout-guard", nargs="*", default=None, metavar="ROWS", help="default off. A Han run that contains a guarded sentence is not counted; the skipped count is printed. Same matcher as build_counts_text.py --holdout-guard (no files = every .txt under eval/holdout/). Put it after the positional paths or separate with --")
    sub.choices["text"].add_argument("paths", nargs="+")
    sub.choices["wiki"].add_argument("--shard", metavar="K/N", help="only articles i with i %% N == K; output tag <articles>-shard<K>of<N>; merge the N shards with `merge`")
    p = sub.add_parser("merge")
    p.add_argument("--out", required=True)
    p.add_argument("--tag", required=True)
    p.add_argument("shards", nargs="+", metavar="SHARD_TAG")
    a = ap.parse_args(argv)
    {"wiki": cmd_wiki, "text": cmd_text, "merge": cmd_merge}[a.cmd](a)


if __name__ == "__main__":
    main()
