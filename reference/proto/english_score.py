"""English or Zhuyin at the closing key of a syllable: the Python baseline of docs/spec/english-mixed.md §16 (core/src/english_auto.rs is the port).

At a decision point (a syllable the lexicon has, typed with a replaced key or a key the reversal guard took out of column order) the keys typed, e,
are scored as an English word against the syllable s. All log10.

  lpE = log10 P(E | C) + log10 P(e | E)
    P(E | C) = (N(C->E) + beta P(E)) / (N(C) + beta), P(E) = N(E) / T; no D row for C: P(E); beta = 1.
    P(e | E): e in the table: N(e) / (N(E) + A).
              e not in the table, letters only, 5..32 of them, A > 0: A / (N(E) + A) * P_char(e), added as
                  log10(A / (N(E) + A)) + [log10 P_L(|e|) + the log10 of each trigram probability, left to right]
                  P_L(n) = (L(n) + 1) / (sum of L + 28);  P(z | xy) = (G(xyz) + 0.5) / (G(xy.) + 13.5) over ^^e$ (|e| + 1 of them).
              anything else: minus infinity (no comparison; Zhuyin).
  lpZ = S + t - kappa n_ow - kappa_dup n_dup - kappa_rev n_rev (subtracted in this order)
    S = log10 of the sum over the words w compatible with s of P(w | C): digits_score.Scorer.lp_z(C, False, units), the same plain sum;
    t = the reading term of the w with the highest P(w | C) (the first of equals; 0 with the reading term off).
  English when lpE > lpZ + theta (a tie is Zhuyin).

The table (data/lexicon/english.tsv, tools/build_english.py writes it): tab separated rows under `#` header lines,
  T N      units counted (the denominator of P(E))            N N(E)   the English tokens of the E rows
  A N      the count mass of the tokens the size cut removed    D v N(v->E) N(v)
  E e N(e)                                                      G xyz N  letter trigram types over ^, a-z, $
  L n N    types of length n, 5..32
A table that does not parse is no table (`Table.parse` raises ValueError; the engine then runs without the comparison).

  python3 reference/proto/english_score.py --golden   writes eval/golden/english-score.txt from core/tests/fixtures/english-tiny.tsv
  python3 reference/proto/english_score.py --check    the same text against the file
"""
import math
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))

# Copied from core/src/english_auto.rs; test_english_score.py checks them against that source.
THETA, KAPPA, KAPPA_DUP, KAPPA_REV = 1.0, 2.0, 3.0, 2.0
BETA = 1.0
MAX_LEN, UNSEEN_MIN = 32, 5
TRI_ADD, TRI_KINDS = 0.5, 27.0
NEG = -math.inf
LOWER = frozenset("abcdefghijklmnopqrstuvwxyz")
DIGITS = frozenset("0123456789")


def valid(e):
    """[a-z][a-z0-9]{0,31}"""
    return 0 < len(e) <= MAX_LEN and e[0] in LOWER and all(c in LOWER or c in DIGITS for c in e)


class Table:
    def __init__(self, text):
        self.t = self.n = self.a = 0
        self.d, self.e, self.g3, self.g2, self.l = {}, {}, {}, {}, {}
        for line in text.split("\n"):
            if not line or line.startswith("#"):
                continue
            f = line.split("\t")
            kind = f[0]
            try:
                if kind == "T":
                    self.t = self._num(f[1])
                elif kind == "N":
                    self.n = self._num(f[1])
                elif kind == "A":
                    self.a = self._num(f[1])
                elif kind == "D":
                    self.d[f[1]] = (self._num(f[2]), self._num(f[3]))
                elif kind == "E":
                    if not valid(f[1]) or f[1] in self.e:
                        raise ValueError("E row")
                    self.e[f[1]] = self._num(f[2])
                elif kind == "G":
                    g = f[1]
                    if len(g) != 3 or g in self.g3 or not all(c in LOWER or c == x for c, x in zip(g, "^^$")):
                        raise ValueError("G row")
                    self.g3[g] = self._num(f[2])
                    self.g2[g[:2]] = self.g2.get(g[:2], 0) + self.g3[g]
                elif kind == "L":
                    if not (f[1].isascii() and f[1].isdigit() and UNSEEN_MIN <= int(f[1]) <= MAX_LEN):
                        raise ValueError("L row")
                    self.l[int(f[1])] = self._num(f[2])
                else:
                    raise ValueError("unknown row type")
            except IndexError:
                raise ValueError("short row") from None
        self.sum_l = sum(self.l.values())
        if not (self.t > 0 and self.n > 0):
            raise ValueError("T and N must be positive")

    @staticmethod
    def _num(x):
        if not (x.isascii() and x.isdigit()):   # what Rust's u64 parse takes, without the leading `+`
            raise ValueError("not a count")
        return int(x)

    @classmethod
    def load(cls, path):
        with open(path, encoding="utf-8") as f:
            return cls(f.read())

    def p_E_given(self, ctx):
        p = self.n / self.t
        row = self.d.get(ctx)
        return p if row is None else (row[0] + BETA * p) / (row[1] + BETA)

    def lp_char(self, e):
        lp = math.log10((self.l.get(len(e), 0) + 1.0) / (self.sum_l + float(MAX_LEN - UNSEEN_MIN + 1)))
        x, y = "^", "^"
        for z in e + "$":
            lp += math.log10((self.g3.get(x + y + z, 0) + TRI_ADD) / (self.g2.get(x + y, 0) + TRI_ADD * TRI_KINDS))
            x, y = y, z
        return lp

    def lp_word(self, e):
        den = float(self.n) + float(self.a)
        n = self.e.get(e)
        if n is not None:
            return math.log10(n / den) if n > 0 else NEG
        if not (UNSEEN_MIN <= len(e) <= MAX_LEN) or not all(c in LOWER for c in e) or self.a == 0:
            return NEG
        return math.log10(self.a / den) + self.lp_char(e)

    def lp_e(self, ctx, e):
        return math.log10(self.p_E_given(ctx)) + self.lp_word(e)


def lp_z(s, term, n_ow, n_dup, n_rev, kappa=KAPPA, kappa_dup=KAPPA_DUP, kappa_rev=KAPPA_REV):
    return s + term - kappa * n_ow - kappa_dup * n_dup - kappa_rev * n_rev


def takes_english(lp_e, lpz, theta=THETA):
    return lp_e > lpz + theta


def sum_and_best(scorer, ctx, units):
    """S and the word the reading term is read of, over a digits_score.Scorer's candidates (its `lp_z(ctx, False, units)` is the same sum)."""
    tot, best = 0.0, None
    for w, _, pb in scorer.candidates(units):
        p = scorer.lm.prob(ctx, w, pb)
        tot += p
        if best is None or p > best[1]:
            best = (w, p)
    return (math.log10(tot) if tot > 0 else NEG), (best[0] if best else None)


def bits(x):
    return struct.pack(">d", x).hex()


FIXTURE = os.path.join(ROOT, "core", "tests", "fixtures", "english-tiny.tsv")
GOLDEN = os.path.join(ROOT, "eval", "golden", "english-score.txt")
# (context, e): table words, an unseen word of seen trigrams, one with trigrams the table never saw, the minus-infinity cases, a context with a D row.
E_CASES = [("<s>", "ok"), ("<s>", "speech"), ("用", "speech"), ("用", "ok"), ("沒", "test"), ("<s>", "spell"), ("用", "speeches"), ("<s>", "qxzvk"),
           ("<s>", "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz"), ("<s>", "dek"), ("<s>", "abcd"), ("<s>", "abcd9"), ("<s>", "test6"), ("<s>", "gta6")]
# (S, t, n_ow, n_dup, n_rev, theta, kappa, kappa_dup, lpE)
Z_CASES = [(-5.25, -0.75, 4, 1, 0, 3.0, 2.0, 3.0, -4.17), (-2.0, 0.0, 1, 0, 0, 1.0, 1.0, 0.0, -2.0), (-2.0, 0.0, 1, 0, 0, 1.0, 1.0, 0.0, -1.9999999999999998),
           (-1.0, 0.0, 1, 0, 0, 3.0, 2.0, 3.0, -6.0),
           (-3.1, -1.3, 0, 0, 1, 2.0, 1.5, 3.0, -2.2), (-0.1, 0.0, 2, 2, 1, -100.0, 1.5, 0.0, -9.5), (NEG, 0.0, 1, 0, 0, 3.0, 2.0, 3.0, -30.0)]


def golden_text():
    tb = Table.load(FIXTURE)
    out = ["# english-score golden (reference/proto/english_score.py --golden) from core/tests/fixtures/english-tiny.tsv. Python is the baseline;",
           "# Rust must match the f64 bits. E ctx e lpE | Z S t n_ow n_dup n_rev theta kappa kappa_dup kappa_rev lpE lpZ english"]
    for ctx, e in E_CASES:
        out.append(f"E\t{ctx}\t{e}\t{bits(tb.lp_e(ctx, e))}")
    for s, t, ow, dup, rev, theta, kappa, kdup, lpe in Z_CASES:
        z = lp_z(s, t, ow, dup, rev, kappa, kdup, KAPPA_REV)
        out.append(f"Z\t{bits(s)}\t{bits(t)}\t{ow}\t{dup}\t{rev}\t{bits(theta)}\t{bits(kappa)}\t{bits(kdup)}\t{bits(KAPPA_REV)}\t{bits(lpe)}\t{bits(z)}\t{int(takes_english(lpe, z, theta))}")
    return "".join(x + "\n" for x in out)


if __name__ == "__main__":
    if sys.argv[1:] == ["--golden"]:
        with open(GOLDEN, "w", encoding="utf-8", newline="\n") as f:
            f.write(golden_text())
    elif sys.argv[1:] == ["--check"]:
        with open(GOLDEN, encoding="utf-8") as f:
            sys.exit(0 if f.read() == golden_text() else "eval/golden/english-score.txt is not what this script writes")
    else:
        sys.exit(__doc__)
