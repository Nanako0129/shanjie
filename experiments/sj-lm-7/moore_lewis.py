#!/usr/bin/env python3
"""Moore-Lewis selection and the chat reserve bucket (docs/contracts/sj-lm-7.md section 2.4).

  moore_lewis.py score --in-domain FILE --source NAME=FILE [--source NAME=FILE ...] --out DIR --seed N [--keep 0.5]
      Two character 4-gram interpolated Kneser-Ney models (discount 0.75): the in-domain one trained on FILE (one sentence per line, the last
      tab field if the line has tabs), the out-of-domain one on a fixed-seed sample of the pooled --source sentences with the same number of
      characters (or the whole pool when it is not larger). A sentence's score is H_in - H_out, the per-character cross-entropy difference in
      bits (EOS counted); lower is more in-domain. Each source keeps its best KEEP share (ties broken by line order), written to OUT/NAME with
      the lines unchanged and in the original order. Deterministic: the sample is random.Random(seed) over the pool's line order.
      Prints counts, the score cut and SHA-256 of every output, never a sentence (the in-domain file may be private).
  moore_lewis.py split --messages FILE --reserve OUT --rest OUT
      FILE lines are `message id<TAB>text`. A message is in the reserve bucket iff the first byte of SHA-256(message id) < 32 (1/8). Prints counts.

Pure Python: memory is a few tens of bytes per distinct n-gram, so a scoring file of tens of millions of characters needs several GB.
"""
import argparse
import collections
import hashlib
import math
import os
import random
import sys

BOS, EOS = "\x02", "\x03"
RESERVE_BELOW = 32     # first byte of SHA-256(message id) < 32: 32/256 = 1/8 of the messages
KEEP = 0.5             # the share of each source kept (contract 2.4, committed before any score exists)


def in_reserve(message_id):
    return hashlib.sha256(message_id.encode("utf-8")).digest()[0] < RESERVE_BELOW


class CharKN:
    """Interpolated Kneser-Ney, absolute discount D, over characters. Orders below the highest use continuation counts (the number of
    distinct left extensions), except n-grams that start with the sentence-start padding, which use their plain counts."""

    def __init__(self, sentences, order=4, discount=0.75):
        self.order, self.d = order, discount
        raw = [None] + [collections.Counter() for _ in range(order)]
        vocab = set()
        for s in sentences:
            vocab.update(s)
            t = BOS * (order - 1) + s + EOS
            for j in range(order - 1, len(t)):       # windows that end at a real character or EOS: the padding is context, never predicted
                for k in range(1, order + 1):
                    raw[k][t[j - k + 1:j + 1]] += 1
        self.V = len(vocab) + 2                      # characters seen, EOS, and one slot for everything unseen (the uniform floor)
        self.total, self.types, self.cnt = [None] * (order + 1), [None] * (order + 1), {}
        for k in range(1, order + 1):
            if k == order:
                cnt = raw[k]
            else:
                cnt = collections.Counter()
                for g in raw[k + 1]:
                    cnt[g[1:]] += 1                  # one per distinct left extension
                for g, n in raw[k].items():
                    if g[0] == BOS:
                        cnt[g] = n
            tot, typ = collections.Counter(), collections.Counter()
            for g, n in cnt.items():
                tot[g[:-1]] += n
                typ[g[:-1]] += 1
            self.total[k], self.types[k], self.cnt[k] = tot, typ, cnt

    def prob(self, ctx, w):
        """P(w | ctx), ctx = the preceding characters (padded with BOS at the start of a sentence)."""
        p = 1.0 / self.V
        for k in range(1, self.order + 1):          # from the unigram up; each level interpolates with the lower one
            h = ctx[len(ctx) - (k - 1):] if k > 1 else ""
            tot = self.total[k].get(h)
            if not tot:
                continue                             # unseen context: the lower-order estimate stands
            p = max(self.cnt[k].get(h + w, 0) - self.d, 0) / tot + self.d * self.types[k][h] / tot * p
        return p

    def entropy(self, s):
        """Per-character cross-entropy of one sentence in bits, EOS included."""
        t = BOS * (self.order - 1) + s + EOS
        n = self.order - 1
        return -sum(math.log2(self.prob(t[max(0, i - n):i] if n else "", t[i])) for i in range(n, len(t))) / (len(t) - n)


def read_sentences(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return [(line, line.rstrip("\n").split("\t")[-1]) for line in f]


def sample_chars(sentences, n_chars, seed):
    """Fixed-seed sample of `sentences` (list of str, in file order) with at least n_chars characters in total, or all of them."""
    if sum(map(len, sentences)) <= n_chars:
        return list(sentences)
    order = list(range(len(sentences)))
    random.Random(seed).shuffle(order)
    out, total = [], 0
    for i in order:
        out.append(sentences[i])
        total += len(sentences[i])
        if total >= n_chars:
            break
    return out


def select(sources, in_domain, seed, keep=KEEP, order=4):
    """sources: {name: [(line, sentence)]}, in_domain: [sentence]. -> ({name: [kept (line, sentence) in file order]}, {name: score cut})."""
    pool = [s for rows in sources.values() for _, s in rows]
    ind = CharKN(in_domain, order)
    outd = CharKN(sample_chars(pool, sum(map(len, in_domain)), seed), order)
    kept, cut = {}, {}
    for name, rows in sources.items():
        scores = [(ind.entropy(s) - outd.entropy(s), i) for i, (_, s) in enumerate(rows)]
        n = math.ceil(len(rows) * keep)
        best = sorted(scores)[:n]
        cut[name] = best[-1][0] if best else None
        kept[name] = [rows[i] for i in sorted(i for _, i in best)]
    return kept, cut


def sha256_file(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def cmd_score(a):
    names = [x.partition("=")[0] for x in a.source]
    if len(set(names)) != len(names) or not all(names):
        sys.exit("--source wants distinct NAME=FILE values")
    sources = {n: read_sentences(f) for n, _, f in (x.partition("=") for x in a.source)}
    in_domain = [s for _, s in read_sentences(a.in_domain) if s.strip()]
    kept, cut = select(sources, in_domain, a.seed, a.keep)
    os.makedirs(a.out, exist_ok=True)
    print(f"in-domain\tsentences\t{len(in_domain)}\tchars\t{sum(map(len, in_domain))}")
    for n, rows in kept.items():
        path = os.path.join(a.out, n)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.writelines(line for line, _ in rows)
        print(f"{n}\tin\t{len(sources[n])}\tkept\t{len(rows)}\tcut\t{cut[n]!r}\tsha256\t{sha256_file(path)}")


def cmd_split(a):
    n = [0, 0]
    with open(a.messages, encoding="utf-8", errors="replace") as src, open(a.reserve, "w", encoding="utf-8", newline="") as res, \
            open(a.rest, "w", encoding="utf-8", newline="") as rest:
        for line in src:
            mid = line.split("\t", 1)[0]
            bucket = in_reserve(mid)
            (res if bucket else rest).write(line)
            n[bucket] += 1
    print(f"reserve\t{n[1]}\trest\t{n[0]}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("score")
    s.add_argument("--in-domain", required=True)
    s.add_argument("--source", action="append", required=True, metavar="NAME=FILE")
    s.add_argument("--out", required=True)
    s.add_argument("--seed", type=int, required=True)
    s.add_argument("--keep", type=float, default=KEEP)
    s.set_defaults(fn=cmd_score)
    p = sub.add_parser("split")
    p.add_argument("--messages", required=True)
    p.add_argument("--reserve", required=True)
    p.add_argument("--rest", required=True)
    p.set_defaults(fn=cmd_split)
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
