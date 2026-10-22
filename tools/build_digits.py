#!/usr/bin/env python3
"""Builds data/lexicon/digits.tsv, the digit-context count table (docs/contracts/digit-context.md C.3), and reads it back.

Inputs (the closed list, nothing else): (1) the wiki dump through bc.articles(200000), (2) colloquial-train.txt (build_tune.py's output).
Preprocessing is experiments/s2/build_counts.py's: entity unescape, TEMPLATE, MARKUP, SENT, convert (bc.article_sentences, bc.convert); only the
Han-run split is replaced by `line_items`, which keeps digit runs. The held-out sentences (docs/contracts/holdout-refresh.md section 8) are
excluded with tools/holdout_guard's matcher, on the same counting layer: a Han run that contains one is not counted and acts as a boundary.

A line becomes a sequence of units: segmented words (a one-character Han run counts too), digit runs `D`, and boundaries (any other
character, a Han run that cannot be segmented, a guarded Han run; a line's start and end are boundaries too). Digit run =
[0-9]+([./-][0-9]+)*, full-width digits folded first.

The context v of a unit is exactly the scorer's run context (digit-context.md C.6 item 1): `lm.history(lm.context_key(the text before the unit on
the same line), model)`, so it is `<s>` at a line start or when no Han text precedes (a word of three or more characters is v by its last two
characters or its last one, as the scorer sees it). The builder therefore loads the model like measure.py does (predict.build_lm_lex).

Rows (tab separated, sorted by bytes under one header line):
  T  N                 sum of N(u) over all units          N  N(D)  number of digit runs
  D  v  N(v->D)  N(v)  v = the context of a digit run; N(v) = number of units (words and runs) whose context is v
  W  w  N(D->w)        word immediately after a digit run
  Q  v  N(v,D->.)      digit run with context v, then any word (a run followed by a boundary does not count)
  P  v  w  N(v,D->w)   the triple (context v, digit run, w), kept when the count >= max(2, k)
  L  n  N              run length in characters, 1..9 and `10+`
  C  ch N              characters of the runs (0-9 . / -)    S  N(D->end)  runs followed by a boundary
Size fallback: first k = 1 (no cut); while the file is over 2,000,000 bytes, k goes 2 3 5 10 20 30 50. D and Q rows need N(v->D) >= k, W rows
N(D->w) >= k, P rows count >= max(2, k); D rows with N(v) >= 1000 (whatever N(v->D) is: a cut one would fall back to the larger P(D)), and the
`<s>` D row, stay at any k. Past k = 50 it stops.
The reader (`Table`) is the one definition of digits.md A.3.2's quantities; a missing row means count 0 / the fallback written there.

  python3 tools/build_digits.py --wiki DUMP --colloquial colloquial-train.txt --out data/lexicon/digits.tsv [--procs N] [--holdout-rows FILE...]
"""
import argparse
import collections
import hashlib
import itertools
import multiprocessing as mp
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("experiments/s2", "experiments/sp", "tools", "reference/proto"):
    sys.path.insert(0, os.path.join(ROOT, *_p.split("/")))

import build_counts as bc  # noqa: E402
import build_counts_text as bct  # noqa: E402
import lm as L  # noqa: E402
import predict as P1  # noqa: E402

VERSION = "2"
ARTICLES = 200_000
MAX_BYTES = 2_000_000       # "<= 2 MB" read as 10^6-based: the stricter reading also satisfies 2 MiB
K_STEPS = (1, 2, 3, 5, 10, 20, 30, 50)
COMMON = 1000               # N(v) at or above this keeps a D row whatever N(v->D) is
P_MIN = 2                   # P rows below this count are never written
LONG = 10                   # L row `10+`
FULL = str.maketrans("０１２３４５６７８９", "0123456789")
TOKEN = re.compile(r"([0-9]+(?:[./-][0-9]+)*)|(" + bc.HAN.pattern + ")")
BOUNDARY = ("B",)
SOS = "<s>"


def make_history(lm):
    """-> hist(key): L.history(key, lm), cached per distinct context_key (a few hundred thousand of them in a 200,000-article run)."""
    cache = {}

    def hist(key):
        v = cache.get(key)
        if v is None:
            v = cache[key] = L.history(key, lm)
        return v
    return hist


def line_items(text, lex, hist, guard=None):
    """One converted line -> [("w", word, v) | ("D", run, v) | ("B",)]; v is the unit's context (module docstring), hist maps a context_key to it.
    Leading and trailing boundaries are implicit, boundaries never repeat."""
    text = text.translate(FULL)
    out, pos = [], 0

    def ctx(at):
        return hist(L.context_key(text[max(0, at - 2):at]))   # context_key looks at two characters at most

    def boundary():
        if out and out[-1] is not BOUNDARY:
            out.append(BOUNDARY)

    for m in TOKEN.finditer(text):
        if m.start() != pos:
            boundary()
        pos = m.end()
        if m.group(1):
            out.append(("D", m.group(1), ctx(m.start())))
            continue
        run = m.group(2)
        held = guard is not None and guard.any(bc.fold(run))
        if held:   # a held-out run is a boundary for the contexts too: its characters never become a context
            text = text[:m.start()] + "，" * len(run) + text[m.end():]
        words = None if held else bc.segment(lex, run)
        if words:
            at = m.start()
            for w in words:
                out.append(("w", w, ctx(at)))
                at += len(w)
        else:
            boundary()
    return out


class Counts:
    def __init__(self):
        self.ctx = collections.Counter()   # N(v): units whose context is v
        self.d = collections.Counter()     # N(v->D), v may be <s>
        self.w = collections.Counter()     # N(D->w)
        self.q = collections.Counter()     # N(v,D->.)
        self.p = collections.Counter()     # N(v,D->w) keyed (v, w)
        self.l = collections.Counter()     # run length, LONG for 10+
        self.c = collections.Counter()     # run characters
        self.nd = self.s = self.t = 0

    def add_line(self, items):
        n = len(items)
        for i, it in enumerate(items):
            if it is BOUNDARY:
                continue
            nxt = items[i + 1] if i + 1 < n else BOUNDARY
            v = it[2]
            self.t += 1
            self.ctx[v] += 1
            if it[0] == "D":
                run = it[1]
                self.nd += 1
                self.d[v] += 1
                self.l[min(len(run), LONG)] += 1
                self.c.update(run)
                if nxt is BOUNDARY:
                    self.s += 1
                else:
                    self.w[nxt[1]] += 1
                    self.q[v] += 1
                    self.p[(v, nxt[1])] += 1

    def merge(self, o):
        for name in ("ctx", "d", "w", "q", "p", "l", "c"):
            getattr(self, name).update(getattr(o, name))
        for name in ("nd", "s", "t"):
            setattr(self, name, getattr(self, name) + getattr(o, name))


def render(c, header, k=1):
    """The table text for min count k (see the module docstring)."""
    rows = [f"T\t{c.t}", f"N\t{c.nd}", f"S\t{c.s}"]
    rows.append(f"D\t{SOS}\t{c.d[SOS]}\t{c.ctx[SOS]}")
    for v, n in c.d.items():
        if v != SOS and n >= k:
            rows += [f"D\t{v}\t{n}\t{c.ctx[v]}", f"Q\t{v}\t{c.q[v]}"]
    rows += [f"D\t{v}\t{c.d[v]}\t{n}" for v, n in c.ctx.items() if v != SOS and n >= COMMON and c.d[v] < k]
    rows += [f"W\t{w}\t{n}" for w, n in c.w.items() if n >= k]
    rows += [f"P\t{v}\t{w}\t{n}" for (v, w), n in c.p.items() if n >= max(P_MIN, k)]
    rows += [f"L\t{n if n < LONG else '10+'}\t{x}" for n, x in c.l.items()]
    rows += [f"C\t{ch}\t{x}" for ch, x in c.c.items()]
    if c.d[SOS]:
        rows.append(f"Q\t{SOS}\t{c.q[SOS]}")
    return header + "\n" + "".join(r + "\n" for r in sorted(set(rows), key=lambda r: r.encode("utf-8")))


def build_text(c, header_fn):
    """-> (text, k): the first k in K_STEPS whose table fits MAX_BYTES; None when even k = 50 does not."""
    for k in K_STEPS:
        text = render(c, header_fn(k), k)
        if len(text.encode("utf-8")) <= MAX_BYTES:
            return text, k
    return None, None


class Table:
    """digits.md A.3.2 over a parsed table. `pw` (the model's single-word probability) is passed in: the table does not know it."""

    def __init__(self, text):
        self.t = self.nd = self.s = 0
        self.d, self.w, self.q, self.l, self.c = {}, {}, {}, {}, {}
        self.p = collections.defaultdict(dict)
        for line in text.split("\n"):
            if not line or line.startswith("#"):
                continue
            f = line.split("\t")
            kind = f[0]
            if kind == "T":
                self.t = int(f[1])
            elif kind == "N":
                self.nd = int(f[1])
            elif kind == "S":
                self.s = int(f[1])
            elif kind == "D":
                self.d[f[1]] = (int(f[2]), int(f[3]))
            elif kind == "W":
                self.w[f[1]] = int(f[2])
            elif kind == "Q":
                self.q[f[1]] = int(f[2])
            elif kind == "P":
                self.p[f[1]][f[2]] = int(f[3])
            elif kind == "L":
                self.l[LONG if f[1] == "10+" else int(f[1])] = int(f[2])
            elif kind == "C":
                self.c[f[1]] = int(f[2])
            else:
                raise ValueError(f"unknown row type {kind!r}")
        self.sum_c = sum(self.c.values())
        self.succ_w = frozenset(self.w)
        self._succ = {}

    @classmethod
    def load(cls, path):
        with open(path, encoding="utf-8") as f:
            return cls(f.read())

    def p_D(self):
        return self.nd / self.t

    def p_D_given(self, ctx, beta=1.0):
        """(N(C->D) + beta P(D)) / (N(C) + beta); no D row: P(D)."""
        row = self.d.get(ctx)
        if row is None:
            return self.p_D()
        return (row[0] + beta * self.p_D()) / (row[1] + beta)

    def p_len(self, m, ended):
        """P_len(m | ended) = N(L = m) / N(D); not ended: N(L >= m) / N(D); m >= 10 uses N(L = 10+) both ways."""
        m = min(m, LONG)
        return (self.l.get(m, 0) if ended or m == LONG else sum(x for n, x in self.l.items() if n >= m)) / self.nd

    def p_ch(self, ch):
        return self.c.get(ch, 0) / self.sum_c

    def p_w_given_D(self, w, pw, gamma=1.0):
        """(N(D->w) + gamma P(w)) / (N(D) + gamma); no W row: N(D->w) = 0."""
        return (self.w.get(w, 0) + gamma * pw) / (self.nd + gamma)

    def p_w_given_vD(self, w, v, pw, gamma=1.0):
        """(N(v,D->w) + gamma P(w|D)) / (N(v,D->.) + gamma); no Q row: P(w|D); no P row: N(v,D->w) = 0."""
        base = self.p_w_given_D(w, pw, gamma)
        if v not in self.q:
            return base
        return (self.p.get(v, {}).get(w, 0) + gamma * base) / (self.q[v] + gamma)

    def successors(self, v):
        """digits.md A.3.5's successor layer: words with N(v,D->w) > 0 or N(D->w) > 0."""
        got = self._succ.get(v)
        if got is None:
            got = self._succ[v] = self.succ_w | frozenset(self.p.get(v, ()))
        return got


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _work(job):
    """job = (kind, items): kind "wiki" (raw articles) or "text" (lines). A worker process; bc._init filled bc._W."""
    kind, batch = job
    lex, conv, guard, hist = bc._W["lex"], bc._W["conv"], bc._W.get("guard"), bc._W["hist"]
    c = Counts()
    for raw in batch:
        sents = bc.article_sentences(raw, conv) if kind == "wiki" else [bc.convert(raw, *conv)]
        for s in sents:
            c.add_line(line_items(s, lex, hist, guard))
    return c


def _init(guard):
    """A worker process: bc's tables, and the model for the contexts (each process loads it once)."""
    bc._init(False, False, False, (), guard)
    lm, _ = P1.build_lm_lex()
    bc._W["hist"] = make_history(lm)


def count_all(wiki, articles, colloquial, procs, guard):
    c = Counts()
    jobs = itertools.chain((("wiki", b) for b in bc.batches(bc.articles(articles, dump=wiki))),
                           (("text", b) for b in bc.batches(bct.lines(colloquial), 2000)))
    with mp.Pool(procs, initializer=_init, initargs=(guard,)) as pool:
        for done, r in enumerate(pool.imap_unordered(_work, jobs), 1):   # the sum does not depend on the order
            c.merge(r)
            if done % 50 == 0:
                print(f"~{done} batches, {c.t} units, {c.nd} digit runs", flush=True)
    return c


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--wiki", required=True, help="zhwiki-20261001-pages-articles.xml.bz2")
    ap.add_argument("--articles", type=int, default=ARTICLES)
    ap.add_argument("--colloquial", required=True, help="colloquial-train.txt (build_tune.py; no is_tune lines)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--procs", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    ap.add_argument("--holdout-rows", nargs="*", default=[], metavar="ROWS", help="held-out row files; none given = every .txt under eval/holdout/")
    a = ap.parse_args(argv)
    guard = bc.guard_matcher(list(a.holdout_rows))   # before anything else is loaded; stops with path/line/field count only
    bc.load_conv()
    lexdir = os.path.join(ROOT, "data", "lexicon")
    shas = {"wiki": sha256_file(a.wiki), "colloquial": sha256_file(a.colloquial),
            "lexicon": sha256_file(os.path.join(lexdir, "mcbpmf-data.txt")), "overlay": sha256_file(os.path.join(lexdir, "overlay-add.tsv")),
            "model": sha256_file(os.path.join(ROOT, "data", "lm", "bigram.sjlm"))}
    c = count_all(a.wiki, a.articles, a.colloquial, a.procs, guard)
    text, k = build_text(c, lambda k: header(a, shas, k))
    if text is None:
        sys.exit(f"STOP: still over {MAX_BYTES} bytes at k = {K_STEPS[-1]}")
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(f"k={k} bytes={len(text.encode('utf-8'))} T={c.t} N(D)={c.nd} sha256={sha256_file(a.out)}")


def header(a, shas, k):
    return (f"# digits.tsv v{VERSION} | tools/build_digits.py {VERSION} | k={k} | wiki={os.path.basename(a.wiki)} sha256={shas['wiki']} articles={a.articles}"
            f" | colloquial={os.path.basename(a.colloquial)} sha256={shas['colloquial']} | holdout-guard=on"
            f" | segmentation lexicon mcbpmf-data.txt sha256={shas['lexicon']} overlay-add.tsv sha256={shas['overlay']}"
            f" | context model bigram.sjlm sha256={shas['model']}")


if __name__ == "__main__":
    main()
