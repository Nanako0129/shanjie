"""The conversion guard of docs/contracts/tw-conv.md section 3, for any sentence file (the whole PTT snapshot, a test file).

  python3 experiments/s2/tw_conv_guard.py FILE...        one sentence per line, a `.tsv` path contributes its last tab field
                                                           (`.bz2` is decompressed); prints numbers and character pairs only, never a sentence

For every sentence s:
  1 (unchanged)  convert(s) must equal fold_tw(s): a Taiwan sentence is changed by the fold rows of tw-conv.tsv and by nothing else.
  2 (round trip) s is turned into the Simplified s' through STCharacters (a Traditional character is replaced only when exactly one key other
                 than itself lists it), then convert(s') must equal fold_tw(s), with the mainland terms of mainland-terms.txt replaced
                 (longest first) when s' is judged Simplified (the Simplified path does that on purpose). A position that still differs is a
                 (expected, got) character pair; the pair must be listed in tw-conv-ambiguous.tsv (a real one-to-many character, e.g. 乾／幹).
Output: `sentences N`, `unchanged-fail N`, `roundtrip-fail N` (sentences), then `pair<TAB>expected<TAB>got<TAB>count<TAB>listed|NOT-LISTED` per pair.
Exit status 1 when assertion 1 fails for more than 0.1% of the sentences or a pair is not listed (the stop condition of the rebuild's first step).
"""
import bz2
import collections
import difflib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_counts as bc  # noqa: E402

AMBIGUOUS = os.path.join(HERE, "tw-conv-ambiguous.tsv")
TERMS = os.path.join(HERE, "mainland-terms.txt")
MAX_UNCHANGED_FAIL = 0.001


def load_ambiguous(path=AMBIGUOUS):
    """{(expected, got): reason} from the tab-separated file: expected, got, reason; `#` lines are comments."""
    out = {}
    if os.path.isfile(path):
        for line in open(path, encoding="utf-8"):
            if line.strip() and not line.startswith("#"):
                p = line.rstrip("\n").split("\t")
                out[(p[0], p[1])] = p[2] if len(p) > 2 else ""
    return out


class Checker:
    """The tables are loaded once (build_counts.load_conv); `conv` is its tuple."""

    def __init__(self, conv=None):
        self.conv = conv or bc.load_conv()
        st = bc.read_opencc()["st"]
        owners = collections.defaultdict(set)
        for k, vs in st.items():
            for v in vs:
                if v != k:
                    owners[v].add(k)
        self.reverse = {v: next(iter(ks)) for v, ks in owners.items() if len(ks) == 1}   # a Traditional character listed by exactly one other key
        terms = [l.rstrip("\n") for l in open(TERMS, encoding="utf-8") if l.strip() and not l.startswith("#")]
        self.terms = {k: bc.TW_PHRASE[k] for k in terms}
        self.term_max = max(map(len, self.terms))

    def convert(self, s):
        return bc.convert(s, *self.conv)

    def simplify(self, s):
        return "".join(self.reverse.get(c, c) for c in s)

    def replace_terms(self, text):
        """The mainland terms, longest first, left to right (written here on its own, not bc._longest)."""
        out, i = [], 0
        while i < len(text):
            for n in range(min(self.term_max, len(text) - i), 1, -1):
                v = self.terms.get(text[i:i + n])
                if v is not None:
                    out.append(v); i += n
                    break
            else:
                out.append(text[i]); i += 1
        return "".join(out)

    def unchanged(self, s):
        """Assertion 1: (got, expected)."""
        return self.convert(s), bc.fold_tw(s)

    def roundtrip(self, s):
        """Assertion 2: (got, expected) for the Simplified s'."""
        s2 = self.simplify(s)
        want = bc.fold_tw(s)
        return self.convert(s2), (self.replace_terms(want) if bc.is_simplified(s2) else want)


def pairs(want, got):
    """The (expected, got) pairs where two strings differ: character by character when they are as long as each other, else the differing blocks."""
    if len(want) == len(got):
        return [(a, b) for a, b in zip(want, got) if a != b]
    return [(want[i1:i2], got[j1:j2]) for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, want, got, autojunk=False).get_opcodes() if tag != "equal"]


def lines_of(path):
    f = bz2.open(path, "rt", encoding="utf-8", errors="replace") if path.endswith(".bz2") else open(path, encoding="utf-8", errors="replace")
    for line in f:
        line = line.rstrip("\n")
        yield line.split("\t")[-1] if ".tsv" in path else line


def run(sentences, checker=None, ambiguous=None):
    """-> (n sentences, assertion-1 failures, assertion-2 failures, Counter of (expected, got) pairs)."""
    ck, amb = checker or Checker(), ambiguous
    n = f1 = f2 = 0
    bad = collections.Counter()
    for s in sentences:
        n += 1
        got, want = ck.unchanged(s)
        f1 += got != want
        got, want = ck.roundtrip(s)
        if got != want:
            f2 += 1
            bad.update(pairs(want, got))
    return n, f1, f2, bad


def main(argv=None):
    paths = (argv if argv is not None else sys.argv[1:])
    if not paths:
        sys.exit(__doc__)
    ambiguous = load_ambiguous()
    n, f1, f2, bad = run((s for p in paths for s in lines_of(p)), ambiguous=ambiguous)
    print(f"sentences\t{n}\nunchanged-fail\t{f1}\nroundtrip-fail\t{f2}")
    unlisted = 0
    for (a, b), c in sorted(bad.items(), key=lambda kv: -kv[1]):
        listed = (a, b) in ambiguous
        unlisted += not listed
        print(f"pair\t{a}\t{b}\t{c}\t{'listed' if listed else 'NOT-LISTED'}")
    return 1 if (n and f1 / n > MAX_UNCHANGED_FAIL) or unlisted else 0


if __name__ == "__main__":
    sys.exit(main())
