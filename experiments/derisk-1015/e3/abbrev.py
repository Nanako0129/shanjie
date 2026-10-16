"""E3 (docs/contracts/derisk-1015.md section 3): abbreviated-syllable input and the decoder that expands it.

A syllable is abbreviated by keeping only its initial (the first bopomofo letter, when it is one of the 21 initials). A syllable
whose first letter is not an initial (ㄧ ㄨ ㄩ, a bare final, ㄦ) has no initial and is never abbreviated.

Input convention: a token that is exactly one initial letter is an abbreviation and stands for EVERY lexicon syllable that begins
with that letter. This is what the user typing an initial means, and it includes the cases where the bare letter is itself a full
syllable (ㄓ ㄔ ㄕ ㄖ ㄗ ㄘ ㄙ, first tone): the decoder cannot and need not tell them apart (they are just one more reading of the
expansion). It is why the E3 decoder is NOT the null configuration on full input: use the unpatched decode for that (e3_eval --abbrev off).
"""
import collections
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
for sub in ("tools", "reference/proto"):
    sys.path.insert(0, os.path.join(ROOT, sub))
import ime  # noqa: E402
import lm as L  # noqa: E402

INITIALS = frozenset("ㄅㄆㄇㄈㄉㄊㄋㄌㄍㄎㄏㄐㄑㄒㄓㄔㄕㄖㄗㄘㄙ")


def initial(syl):
    """The initial letter of a full syllable, or None when it has none."""
    return syl[0] if syl and syl[0] in INITIALS else None


def is_abbrev(tok):
    return len(tok) == 1 and tok in INITIALS


def abbreviate(syls, draws, p):
    """Each syllable with an initial becomes its initial when its draw is below p; the rest stay. `draws` has one uniform [0, 1)
    number per syllable (drawn for every syllable, initial or not, so the draws of different p stay aligned: the set abbreviated at
    p = 0.2 is a subset of the set at 0.5, and p = 0 changes nothing, p = 1 abbreviates every syllable that has an initial)."""
    return [initial(s) if u < p and initial(s) else s for s, u in zip(syls, draws)]


def draws_for(seed, name, n_syls_per_row):
    """The fixed random draws of one set: one list per row, from a Random seeded by (seed, set name)."""
    rng = random.Random(f"{seed}|{name}")
    return [[rng.random() for _ in range(n)] for n in n_syls_per_row]


class Expander:
    """Lexicon keys (tuples of full syllables) that match a span of input tokens. Built once per lexicon."""

    def __init__(self, lex):
        self.keys = lex.by_reading
        self.root = {}
        self.syls = set()
        for key in self.keys:
            node = self.root
            for s in key:
                node = node.setdefault(s, {})
                self.syls.add(s)
        self.n_by_initial = collections.Counter(initial(s) for s in self.syls if initial(s))
        self._by_init = {}   # id(node) -> {initial: [(syllable, child)]}, built on first use
        self._memo = {}      # span -> sorted matching keys (ponytail: unbounded; per-run cache of a few thousand rows)

    def _children(self, node, tok):
        grp = self._by_init.get(id(node))
        if grp is None:
            grp = self._by_init[id(node)] = collections.defaultdict(list)
            for s, child in node.items():
                if initial(s):
                    grp[initial(s)].append((s, child))
        return grp.get(tok, ())

    def match(self, span):
        """Sorted keys of the lexicon that fit `span`: an abbreviation token takes any syllable with that initial, a full token only itself.
        A span without abbreviations returns [span] or [] exactly like by_reading.get."""
        if not any(is_abbrev(t) for t in span):
            return [span] if span in self.keys else []
        got = self._memo.get(span)
        if got is None:
            front = [((), self.root)]
            for tok in span:
                if is_abbrev(tok):
                    front = [(pre + (s,), child) for pre, node in front for s, child in self._children(node, tok)]
                else:
                    front = [(pre + (tok,), node[tok]) for pre, node in front if tok in node]
            got = self._memo[span] = sorted(pre for pre, _ in front if pre in self.keys)
        return got


def make_decode(stats):
    """lm.decode with abbreviation tokens expanded. Same beam, scoring, tie-breaking and n-best as lm.decode (the loop is lm.decode's,
    with `lex.by_reading.get(key)` replaced by the matching keys, in sorted order); `stats` collects counters across calls:
    abbr_tokens, readings_sum (distinct lexicon syllables behind each abbreviated token), entries_tried (lexicon word entries scored).
    `stats` is a collections.Counter. For input without abbreviation tokens the result is lm.decode's, computed by the same loop."""
    cache = {}

    def decode(lex, syls, lm, profile, beam=64, start="<s>", demote=True):
        ex = cache.get(id(lex)) or cache.setdefault(id(lex), Expander(lex))
        table = getattr(lex, "demote", None) if demote else None
        lam = L.PROFILES[profile]
        syls = tuple(syls); n = len(syls)
        stats["abbr_tokens"] += sum(is_abbrev(t) for t in syls)
        stats["readings_sum"] += sum(ex.n_by_initial[t] for t in syls if is_abbrev(t))
        hyps = [[] for _ in range(n + 1)]; hyps[0] = [(0.0, ())]
        for i in range(1, n + 1):
            cand = {}
            for ln in range(1, min(lex.max_len, i) + 1):
                if not hyps[i - ln]:
                    continue
                for key in ex.match(syls[i - ln:i]):
                    entries = lex.by_reading[key]
                    reading = "-".join(key) if table else None
                    for word, lp in entries[:ime.PER_KEY]:
                        stats["entries_tried"] += 1
                        d = L.demote_delta(table, reading, word)
                        for s, ws in hyps[i - ln]:
                            sc = s + (lm.word(lam, ws[-1] if ws else start, word, lp) - d)
                            ck = ("".join(ws) + word, word)
                            if ck not in cand or sc > cand[ck][0]:
                                cand[ck] = (sc, ws + (word,))
            hyps[i] = sorted(cand.values(), key=lambda x: -x[0])[:beam]
        out = [(s + lm.eos(lam, ws[-1] if ws else start), ws) for s, ws in hyps[n]]
        best, seen = [], set()
        for x in sorted(out, key=lambda x: -x[0]):
            surface = "".join(x[1])
            if surface not in seen:
                seen.add(surface)
                best.append(x)
        return best

    return decode
