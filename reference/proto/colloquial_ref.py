"""Reference for core/src/colloquial.rs: the colloquial term of the small-model rerank (docs/spec/semantic-rerank.md §4).

  lc(words)  sum of log10 P(w_i | w_{i-1}) of a plain bigram table from `<s>`, plus log10 P(`</s>` | w_k). P backs off to (c(w) + 1) / (N + V + 1)
             (V counting `<s>` and `</s>`); the end term backs off to the table's end-of-sentence rate.
  k(words)   words of the split that are in the overlay/pack set and not in the base lexicon, repeats counted.
  col        lc - GAMMA_PER_MU * k.
  fuse       ll_eff_i = ll_i + sigmoid((ll_i - ll_0) / GATE_T) * (MU / alpha) * (col_i - col_0).
test_colloquial_ref.py recomputes the numbers core/tests/colloquial.rs hard-codes. Reads the table with reference/proto/lm.py BigramLM
(classes off, no Kneser-Ney).
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lm as protolm  # noqa: E402

# Copied from core/src/colloquial.rs; the test compares them with that source.
MU = 0.5
GATE_T = 1.0
GAMMA_PER_MU = 0.5


class Table:
    """BigramLM without classes and KN, with the lc above. `memo` caches log10 P per (previous word, word)."""

    def __init__(self, path):
        self.lm = protolm.BigramLM(path, classes=False)
        self.n1 = self.lm.N + len(self.lm.vocab) + 1
        self.memo = {}

    def logp(self, v, w):
        key = (v, w)
        if key not in self.memo:
            pb = self.lm.p_eos if w == "</s>" else (self.lm.count(w) + 1) / self.n1
            self.memo[key] = math.log10(self.lm.prob(v, w, pb))
        return self.memo[key]

    def lc(self, words):
        prev, tot = "<s>", 0.0
        for w in words:
            tot += self.logp(prev, w)
            prev = w
        return tot + self.logp(prev, "</s>")


def k_of(words, overlay, base):
    """Words of the split in `overlay` and not in `base`, with multiplicity."""
    return sum(1 for w in words if w in overlay and w not in base)


def col(table, words, overlay, base):
    return table.lc(words) - GAMMA_PER_MU * k_of(words, overlay, base)


def word_sets(base_text, overlay_text, pack_text=""):
    """-> (base words, overlay-only candidates) read as Colloquial::load does. Base rows (mcbpmf-data.txt): three whitespace fields, no `#` or `_`
    first character, one syllable per character, a numeric score (anything else raises). Overlay and pack rows: tab lines with exactly four fields."""
    base = set()
    for line in base_text.splitlines():
        f = line.split()
        if line[:1] in ("#", "_") or len(f) != 3 or len(f[1]) != len(f[0].split("-")):
            continue
        float(f[2])
        base.add(f[1])
    overlay = set()
    for line in (overlay_text + "\n" + pack_text).splitlines():
        f = line.split("\t")
        if len(f) == 4:
            overlay.add(f[1])
    return base, overlay


def sigmoid(x):
    """1 / (1 + e^-x) without overflow for large |x|."""
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def fuse(ll, cols, alpha):
    return [ll[i] + sigmoid((ll[i] - ll[0]) / GATE_T) * (MU / alpha) * (cols[i] - cols[0]) for i in range(len(ll))]
