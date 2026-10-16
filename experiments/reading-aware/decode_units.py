"""Decoder for a model whose single-character entries may be units `字 U+0001 讀音` (contract docs/contracts/reading-aware-0.md 3.4, "解碼").

reference/proto/lm.py is not edited; this is lm.decode with the language-model term looked up by unit. The word entry (lexicon score lp,
capped overlay words, demotion) is exactly lm.decode's. Per candidate (word, reading) with the previous path's last unit `pu`:
  - the character has NO unit in the model (a bare-key model, B0 included): the bare key, reading-agnostic: lm.word(lam, pu, word, lp);
  - the unit `字 U+0001 讀音` is in the model: lm.word(lam, pu, unit, lp);
  - the character has units, but not this reading (never labelled so in the corpus): P(字 | pu) * share, where P(字 | pu) = the sum of
    lm.prob(pu, u, 10**lp_u) over the character's units u (lp_u = the lexicon score of u's reading) and share = 10**lp / the sum of 10**lp
    over all the character's readings in the lexicon (lp of the lexicon passed to decode). No UNSEEN_OVERLAY_PENALTY. The path's unit is
    still the unit string, which is not in the model, so the NEXT word backs off without a bigram context (decision, not in the contract);
  - the character is not in the model at all: lm.word's own out-of-vocabulary path (the bare key is unknown).
A multi-character word is its own unit. The recombination key is (surface, last unit) (lm.decode: (surface, last word)); the path state is
(score, words, last unit) and the returned n-best is lm.decode's: [(score, words)]. With no unit in the model every unit is its word and
the result equals lm.decode's bit for bit (test_decode). A model with units must have no classes.sjc / KN side file (units are not in them).

install() is for lm_eval: it replaces lm.decode, lm.history and cap_overlay's lm (count of a character = the sum over its units).
"""
import math

import rcommon
import ime
import lm as L

U = rcommon.U


class UnitIndex:
    """What the decoder needs to know about one loaded BigramLM, computed once."""

    def __init__(self, lm):
        self.lm = lm
        self.units = {}                     # character -> [(unit string, id)], ids ascending
        for i, w in enumerate(lm.vocab):
            if U in w:
                self.units.setdefault(w.split(U, 1)[0], []).append((w, i))
        if self.units and (lm.kn is not None or lm.cls is not None):
            raise ValueError("a model with reading units cannot use classes.sjc or a KN side file (run with --no-classes --no-kn)")
        self._tabs = {}

    def char_table(self, lex):
        """{character: {reading: lp}} of the lexicon (cached per lexicon object)."""
        hit = self._tabs.get(id(lex))
        if hit is None or hit[0] is not lex:
            hit = (lex, {c: dict(rs) for c, rs in rcommon.char_readings(lex).items()})
            self._tabs[id(lex)] = hit
        return hit[1]

    def top_unit(self, c):
        """The most frequent unit of the character (ties: the lowest id); the character itself when it has none."""
        us = self.units.get(c)
        return max(us, key=lambda x: (self.lm.uni[x[1]], -x[1]))[0] if us else c

    def word(self, lam, pu, word, key, lp, tab):
        """(term, unit) of the word whose syllables are `key`: lm.word's term for the unit and the unit string."""
        lm = self.lm
        us = self.units.get(word) if len(word) == 1 else None
        if not us:
            return lm.word(lam, pu, word, lp), word
        unit = word + U + key[0]
        if unit in lm.ids:
            return lm.word(lam, pu, unit, lp), unit
        lps = tab[word]
        p_char = 0.0
        for u, _ in us:
            lp_u = lps.get(u.split(U, 1)[1])
            if lp_u is not None:
                p_char += lm.prob(pu, u, 10 ** lp_u)
        share = 10 ** lp / sum(10 ** x for x in lps.values())
        return lam * math.log10(p_char * share) + (1 - lam) * lp, unit


_INDEX = {}


def index_of(lm):
    hit = _INDEX.get(id(lm))
    if hit is None or hit[0] is not lm:
        hit = _INDEX[id(lm)] = (lm, UnitIndex(lm))
    return hit[1]


def decode(lex, syls, lm, profile, beam=64, start="<s>", demote=True):
    """lm.decode with unit lookup (see the module docstring). `start` is a unit or a word string (history() below)."""
    idx = index_of(lm)
    tab = idx.char_table(lex)
    table = getattr(lex, "demote", None) if demote else None
    lam = L.PROFILES[profile]
    syls = tuple(syls); n = len(syls)
    hyps = [[] for _ in range(n + 1)]; hyps[0] = [(0.0, (), start)]
    for i in range(1, n + 1):
        cand = {}
        for Ln in range(1, min(lex.max_len, i) + 1):
            key = syls[i - Ln:i]
            entries = lex.by_reading.get(key)
            if not entries or not hyps[i - Ln]:
                continue
            reading = "-".join(key) if table else None
            for word, lp in entries[:ime.PER_KEY]:
                d = L.demote_delta(table, reading, word)
                for s, ws, lu in hyps[i - Ln]:
                    term, unit = idx.word(lam, lu, word, key, lp, tab)
                    sc = s + (term - d)
                    ck = ("".join(ws) + word, unit)  # one path per (surface, last unit): the next score depends on the last unit
                    if ck not in cand or sc > cand[ck][0]:
                        cand[ck] = (sc, ws + (word,), unit)
        hyps[i] = sorted(cand.values(), key=lambda x: -x[0])[:beam]
    out = [(s + lm.eos(lam, lu), ws) for s, ws, lu in hyps[n]]
    best, seen = [], set()
    for x in sorted(out, key=lambda x: -x[0]):
        surface = "".join(x[1])
        if surface not in seen:
            seen.add(surface)
            best.append(x)
    return best


def history(left, lm):
    """lm.history with units: the left context has no readings, so a single character stands for its most frequent unit."""
    idx = index_of(lm)
    for x in (left, left[-1:]):
        if x:
            u = idx.top_unit(x) if len(x) == 1 else x
            if lm.ctx.get(lm.ids.get(u, -1)) is not None:
                return u
    return "<s>"


class CountView:
    """What lm.cap_overlay reads of the model (count, N), with a character's count = the sum over its units (the contract: "次數用這個字所有單位的總和")."""

    def __init__(self, lm):
        self.lm, self.N = lm, lm.N
        self.idx = index_of(lm)

    def count(self, w):
        c = self.lm.count(w)
        for _, i in self.idx.units.get(w, ()):
            c += self.lm.uni[i]
        return c
