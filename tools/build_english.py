#!/usr/bin/env python3
"""Builds data/lexicon/english.tsv, the count table of "English or Zhuyin at the closing key" (docs/spec/english-mixed.md §16.5).
The reader of the table is reference/proto/english_score.py (`Table`) and core/src/english_auto.rs.

Inputs: the corpus the n-gram model is counted from, with its weights (docs/spec/models.md §4.1):
  --wiki DUMP         the first --articles (200,000) main-namespace articles, through bc.article_sentences        x1   source `wiki`
  --pool FILE ...     the colloquial pool, one sentence a line (native: never converted)                              x5   source `pool`
  --g0v FILE          g0v messages, `message id<TAB>sentence`                                                         x5   source `g0v`
  --forum-push FILE   forum push units, `board/article<TAB>sentence` (one line a sentence of the article's pushes)    x5   source `forum`
  --forum-body FILE   forum article bodies, the same shape                                                            x1   source `forum`
Only the documents of buckets 0-7 are read (tools/lib/english_tokens.py: the split and the token rule; the probes take buckets 8 and 9). The held-out
sentences (docs/spec/evaluation.md §5.4) are excluded with tools/holdout_guard's matcher as tools/build_digits.py does: a Han run that holds one is not
counted and gives no context, and no English token of a sentence with such a run is counted.

A sentence becomes units: segmented words and English tokens (english_tokens.tokens); everything else is a gap. The context v of a unit is the
scorer's: `lm.history(lm.context_key(the text before the unit))`, for an English token with the blanks before it skipped (`我用 ok` counts under 用,
as it is typed). A token must occur in three documents or more; one below that is not English for this table (not a unit either).

Rows (tab separated, sorted by bytes under the header lines):
  T  N                 the units: words and the tokens of the E rows, weighted
  N  N(E)              the tokens of the E rows, weighted (their sum)
  A  N                 the weighted count of the tokens the size cut removed (document frequency >= 3, count below k)
  D  v  N(v->E)  N(v)  v = the context of a token of the E rows; N(v) = units whose context is v
  E  e  N(e)           a token
  G  xyz  N            letter trigram TYPE counts over ^^word$ of every all-letter token with document frequency >= 3 (before the size cut), each once
  L  n  N              how many of those types have n letters, n in 5..32
Size: k = 1 first; while the file is over 2,000,000 bytes k goes up K_STEPS. E rows need N(e) >= k; D rows need N(v->E) >= k, except the `<s>` row and
rows with N(v) >= 1000, which stay. Past the last step the build stops (the table must fit).
The same inputs give the same bytes (integer counts, sorted rows).

  python3 tools/build_english.py --wiki DUMP --pool A.txt B.txt --g0v G.tsv --forum-push P.tsv --forum-body B.tsv --out data/lexicon/english.tsv
      [--procs N] [--holdout-rows FILE...] [--articles N] [--limit N] [--review FILE]
--limit N reads only the first N lines of every text input (a smoke run; the header says so). --review FILE writes the tokens to read before shipping:
the 200 most frequent E rows and 200 more drawn with random.Random(20261117), one token a line.
"""
import argparse
import collections
import hashlib
import itertools
import multiprocessing as mp
import os
import random
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("tools/lib", "tools", "reference/proto"):
    sys.path.insert(0, os.path.join(ROOT, *_p.split("/")))

import build_counts as bc  # noqa: E402
import build_counts_text as bct  # noqa: E402
import english_tokens as et  # noqa: E402
import lm as L  # noqa: E402

VERSION = "1"
ARTICLES = 200_000
MAX_BYTES = 2_000_000       # "<= 2 MiB" read as 10^6-based, the stricter reading (as digits.tsv)
K_STEPS = (1, 2, 3, 5, 10, 20, 30, 50, 100, 200, 500, 1000)
COMMON = 1000               # N(v) at or above this keeps a D row whatever N(v->E) is
MIN_DOCS = 3
UNSEEN_MIN, MAX_LEN = 5, 32
WEIGHTS = {"wiki": 1, "pool": 5, "g0v": 5, "forum-push": 5, "forum-body": 1}
SOURCE_OF = {"wiki": "wiki", "pool": "pool", "g0v": "g0v", "forum-push": "forum", "forum-body": "forum"}
TABLE_BUCKETS = frozenset(range(8))
REVIEW_SEED = 20261117
SOS = "<s>"
PIECE = re.compile(r"([A-Za-z0-9]+)|(" + bc.HAN.pattern + ")")


def make_history(lm):
    cache = {}

    def hist(key):
        v = cache.get(key)
        if v is None:
            v = cache[key] = L.history(key, lm)
        return v
    return hist


def line_items(text, lex, hist, guard=None, push=False):
    """One sentence -> its units in order: ("w", word, v) and ("E", token, v). Everything else (marks, blanks, digits, a run that is no token,
    a Han run that cannot be segmented or is held out) is a gap: a context never reaches across one, because the context is read from the
    characters right before the unit."""
    held = [m.span() for m in bc.HAN.finditer(text) if guard is not None and guard.any(bc.fold(m.group()))]
    for a, b in held:   # a held-out run is a boundary for the contexts too: its characters never become a context
        text = text[:a] + "，" * (b - a) + text[b:]
    toks = {} if held else {a: (b, t) for a, b, t in et.tokens(text, push)}
    out = []

    def ctx(at):
        return hist(L.context_key(text[max(0, at - 2):at]))   # context_key looks at two characters at most

    for m in PIECE.finditer(text):
        if m.group(1):
            if m.start() in toks:
                out.append(("E", toks[m.start()][1], ctx(et.text_end_before(text, m.start()))))
            continue
        at = m.start()
        for w in bc.segment(lex, m.group(2)) or ():
            out.append(("w", w, ctx(at)))
            at += len(w)
    return out


class Counts:
    def __init__(self):
        self.words = 0                        # weighted word units
        self.ctx = collections.Counter()      # weighted word units whose context is v
        self.pair = collections.Counter()     # weighted (v, e)
        self.docs = {}                        # e -> up to MIN_DOCS document digests (8 bytes each): enough to know "three or more"

    def add_doc(self, digest, sentences, weight):
        """sentences: the item lists of one document."""
        key = digest[:8]
        for items in sentences:
            for it in items:
                if it[0] == "w":
                    self.words += weight
                    self.ctx[it[2]] += weight
                else:
                    self.pair[(it[2], it[1])] += weight
                    seen = self.docs.setdefault(it[1], set())
                    if len(seen) < MIN_DOCS:
                        seen.add(key)

    def merge(self, o):
        self.words += o.words
        self.ctx.update(o.ctx)
        self.pair.update(o.pair)
        for e, seen in o.docs.items():
            mine = self.docs.setdefault(e, set())
            for k in sorted(seen):   # a fixed order: which three are kept never matters, only how many
                if len(mine) < MIN_DOCS:
                    mine.add(k)


def totals(c):
    """-> ({e: weighted count}, the tokens with document frequency >= MIN_DOCS)."""
    n = collections.Counter()
    for (_, e), x in c.pair.items():
        n[e] += x
    return n, {e for e in n if len(c.docs.get(e, ())) >= MIN_DOCS}


def render(c, header, k=1):
    """The table text for min count k (see the module docstring)."""
    n, english = totals(c)
    kept = {e for e in english if n[e] >= k}
    d, ctx = collections.Counter(), collections.Counter(c.ctx)
    for (v, e), x in c.pair.items():
        if e in kept:
            d[v] += x
            ctx[v] += x
    n_e = sum(n[e] for e in kept)
    rows = [f"T\t{c.words + n_e}", f"N\t{n_e}", f"A\t{sum(n[e] for e in english - kept)}", f"D\t{SOS}\t{d[SOS]}\t{ctx[SOS]}"]
    rows += [f"D\t{v}\t{x}\t{ctx[v]}" for v, x in d.items() if v != SOS and x >= k]
    rows += [f"D\t{v}\t{d[v]}\t{x}" for v, x in ctx.items() if v != SOS and x >= COMMON and d[v] < k]
    rows += [f"E\t{e}\t{n[e]}" for e in kept]
    g, lens = collections.Counter(), collections.Counter()
    for e in english:
        if e.isalpha():
            x = "^^" + e + "$"
            for i in range(len(e) + 1):
                g[x[i:i + 3]] += 1
            if UNSEEN_MIN <= len(e) <= MAX_LEN:
                lens[len(e)] += 1
    rows += [f"G\t{t}\t{x}" for t, x in g.items()] + [f"L\t{m}\t{x}" for m, x in lens.items()]
    return header + "\n" + "".join(r + "\n" for r in sorted(set(rows), key=lambda r: r.encode("utf-8")))


def build_text(c, header_fn):
    """-> (text, k): the first k in K_STEPS whose table fits MAX_BYTES; (None, None) when even the last does not."""
    for k in K_STEPS:
        text = render(c, header_fn(k), k)
        if len(text.encode("utf-8")) <= MAX_BYTES:
            return text, k
    return None, None


def review_list(c, k):
    """The tokens to read before shipping: the 200 most frequent (count, then the token) and 200 of the rest with random.Random(REVIEW_SEED)."""
    n, english = totals(c)
    kept = sorted((e for e in english if n[e] >= k), key=lambda e: (-n[e], e))
    rest = kept[200:]
    return kept[:200] + sorted(random.Random(REVIEW_SEED).sample(rest, min(200, len(rest))))


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def documents(kind, items, conv):
    """One batch -> [(digest, [sentence, ...])] grouped by document. kind `wiki`: items are (title, wikitext); else (document id, sentence)."""
    source, out = SOURCE_OF[kind], []
    if kind == "wiki":
        return [(et.doc_digest(source, title), list(bc.article_sentences(raw, conv))) for title, raw in items]
    for doc, group in itertools.groupby(items, key=lambda x: x[0]):
        out.append((et.doc_digest(source, doc), [s for _, s in group]))
    return out


def count_batch(kind, items, lex, conv, hist, guard, buckets=TABLE_BUCKETS):
    c = Counts()
    for digest, sents in documents(kind, items, conv):
        if digest[0] % 10 in buckets:
            c.add_doc(digest, [line_items(s, lex, hist, guard, push=kind == "forum-push") for s in sents], WEIGHTS[kind])
    return c


def _work(job):
    kind, items = job
    return count_batch(kind, items, bc._W["lex"], bc._W["conv"], bc._W["hist"], bc._W.get("guard"))


def history_words():
    """The words with a history record in the model: all `L.history` reads of it. The main process loads the model once (about 2 GiB in Python) and
    hands the workers this list, so a worker stays small."""
    import predict as P1
    lm, _ = P1.build_lm_lex()
    return sorted(w for w, i in lm.ids.items() if lm.ctx.get(i) is not None)


class HistoryOnly:
    """What `L.history` reads of a model, for the words of `history_words`."""

    def __init__(self, words):
        self.ids = dict.fromkeys(words, 0)
        self.ctx = {0: (1,)}


def _init(guard, words):
    """A worker process: bc's tables, and the contexts' history words."""
    bc._init(False, False, False, (), guard)
    bc._W["hist"] = make_history(HistoryOnly(words))


def text_items(kind, path, limit=None):
    """(document id, sentence) of a text input: the pool's id is the sentence, the others' the first column."""
    for line in itertools.islice(bct.lines(path), limit):
        if kind == "pool":
            yield line, line
        else:
            doc, _, s = line.partition("\t")
            yield doc, s


def jobs(a, size=2000):
    """(kind, batch) for every input in the fixed order wiki, pool, g0v, forum-push, forum-body. A batch never cuts a document's lines apart
    where they are adjacent in the file (the files are grouped by document)."""
    def cut(it):
        batch, last = [], None
        for doc, s in it:
            if len(batch) >= size and doc != last:
                yield batch
                batch = []
            batch.append((doc, s))
            last = doc
        if batch:
            yield batch

    if a.wiki:
        for b in bc.batches(((t, raw) for t, _, raw in bc.articles(a.articles, dump=a.wiki, meta=True))):
            yield "wiki", b
    for kind, paths in (("pool", a.pool or []), ("g0v", [a.g0v] if a.g0v else []), ("forum-push", [a.forum_push] if a.forum_push else []),
                        ("forum-body", [a.forum_body] if a.forum_body else [])):
        for p in paths:
            for b in cut(text_items(kind, p, a.limit)):
                yield kind, b


def in_order(pool, fn, tasks, inflight):
    """fn over tasks on the pool, results in task order, at most `inflight` tasks handed out and not yet taken: the inputs are never read far ahead of
    the workers (Pool.imap would queue a whole 1 GB file)."""
    pending = collections.deque()
    for t in tasks:
        pending.append(pool.apply_async(fn, (t,)))
        if len(pending) >= inflight:
            yield pending.popleft().get()
    while pending:
        yield pending.popleft().get()


def input_files(a):
    return [("wiki", a.wiki)] * bool(a.wiki) + [("pool", p) for p in a.pool or []] + [(k, p) for k, p in (("g0v", a.g0v), ("forum-push", a.forum_push), ("forum-body", a.forum_body)) if p]


def add_inputs(ap):
    ap.add_argument("--wiki", help="zhwiki-20261001-pages-articles.xml.bz2")
    ap.add_argument("--articles", type=int, default=ARTICLES)
    ap.add_argument("--pool", nargs="*", default=[], metavar="FILE", help="the colloquial pool's sentence files")
    ap.add_argument("--g0v", metavar="FILE")
    ap.add_argument("--forum-push", metavar="FILE")
    ap.add_argument("--forum-body", metavar="FILE")
    ap.add_argument("--limit", type=int, default=None, help="read only the first N lines of every text input (smoke runs)")
    ap.add_argument("--procs", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    ap.add_argument("--holdout-rows", nargs="*", default=[], metavar="ROWS", help="held-out row files; none given = every .txt under eval/holdout/")


def header(a, shas, k):
    ins = " | ".join(f"{kind} sha256={shas[p]} x{WEIGHTS[kind]}" for kind, p in input_files(a))   # the kind and the hash, never a file name
    limit = f" | SMOKE limit={a.limit}" if a.limit else ""
    return (f"# english.tsv v{VERSION} | tools/build_english.py {VERSION} | k={k} min_docs={MIN_DOCS} buckets=0-7 articles={a.articles}{limit} | {ins} | holdout-guard=on\n"
            f"# segmentation lexicon mcbpmf-data.txt sha256={shas['lexicon']} overlay-add.tsv sha256={shas['overlay']} | context model bigram.sjlm sha256={shas['model']}")


def main(argv=None):
    ap = argparse.ArgumentParser()
    add_inputs(ap)
    ap.add_argument("--out", required=True)
    ap.add_argument("--review", metavar="FILE")
    a = ap.parse_args(argv)
    if not input_files(a):
        ap.error("no input")
    guard = bc.guard_matcher(list(a.holdout_rows))   # before anything else is loaded; stops with path/line/field count only
    bc.load_conv()
    lexdir = os.path.join(ROOT, "data", "lexicon")
    shas = {p: sha256_file(p) for _, p in input_files(a)}
    shas.update(lexicon=sha256_file(os.path.join(lexdir, "mcbpmf-data.txt")), overlay=sha256_file(os.path.join(lexdir, "overlay-add.tsv")),
                model=sha256_file(os.path.join(ROOT, "data", "lm", "bigram.sjlm")))
    c = Counts()
    with mp.Pool(a.procs, initializer=_init, initargs=(guard, history_words())) as pool:
        for done, r in enumerate(in_order(pool, _work, jobs(a), 4 * a.procs), 1):
            c.merge(r)
            if done % 200 == 0:
                print(f"~{done} batches, {c.words} word units, {len(c.docs)} token types", flush=True)
    text, k = build_text(c, lambda k: header(a, shas, k))
    if text is None:
        sys.exit(f"STOP: still over {MAX_BYTES} bytes at k = {K_STEPS[-1]}")
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    if a.review:
        with open(a.review, "w", encoding="utf-8", newline="\n") as f:
            f.write("".join(e + "\n" for e in review_list(c, k)))
    n, english = totals(c)
    kept = [e for e in english if n[e] >= k]
    print(f"tw-clean: {bc.tw_clean_state()}")
    print(f"k={k} bytes={len(text.encode('utf-8'))} word_units={c.words} token_types_seen={len(n)} docfreq_ok={len(english)} E_rows={len(kept)} "
          f"N(E)={sum(n[e] for e in kept)} A={sum(n[e] for e in english if n[e] < k)} sha256={sha256_file(a.out)}")


if __name__ == "__main__":
    main()
