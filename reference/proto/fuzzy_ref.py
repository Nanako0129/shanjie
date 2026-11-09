"""Reference for core/src/fuzzy.rs: the typo-tolerance tables of docs/spec/decoder.md §6.2 (`Tolerance`, `compose`, `NEIGHBOURS`).

Standalone and small: test_fuzzy_ref.py compares every constant and table here with what core/src/fuzzy.rs and core/src/engine.rs hard-code.
  fuzzy sound: four pairs, one bit each (FUZZY_PAIRS order); the alternatives of a syllable under a set of enabled pairs are the other syllables
               that read the same once every enabled pair is merged to the first symbol of the pair (tone marks are kept).
  adjacent key: replace one symbol key of a syllable by its left/right neighbour in the same keyboard row, re-compose the keys through the
               composer's columns (a key fills its own column, a second key of the same column replaces the first); the shown reading maps to
               the syllables it may have been typed from.
"""
# Copied from core/src/engine.rs (SYMBOLS, KEYS_STANDARD) and core/src/fuzzy.rs (FUZZY_PAIRS, ROWS); the test reads those sources.
SYMBOLS = "ㄅㄆㄇㄈㄉㄊㄋㄌㄍㄎㄏㄐㄑㄒㄓㄔㄕㄖㄗㄘㄙㄧㄨㄩㄚㄛㄜㄝㄞㄟㄠㄡㄢㄣㄤㄥㄦ"
KEYS_STANDARD = "1qaz2wsxedcrfv5tgbyhnujm8ik,9ol.0p;/-"
ROWS = ["1234567890-=", "qwertyuiop[]", "asdfghjkl;'", "zxcvbnm,./"]
FUZZY_PAIRS = [("ㄣ", "ㄥ"), ("ㄓ", "ㄗ"), ("ㄔ", "ㄘ"), ("ㄕ", "ㄙ")]
FUZZY_SOUND = 0b1111


def _neighbours():
    """symbol -> the symbols on the keys left and right of its key in the same row (tone keys and non-symbol keys dropped)."""
    key_sym = dict(zip(KEYS_STANDARD, SYMBOLS))
    out = {}
    for row in ROWS:
        for i, k in enumerate(row):
            if k in key_sym:
                out[key_sym[k]] = [key_sym[row[j]] for j in (i - 1, i + 1) if 0 <= j < len(row) and row[j] in key_sym]
    return out


NEIGHBOURS = _neighbours()


def _column(sym):
    """Composer column of a symbol: 0 initial, 1 medial, 2 final."""
    i = SYMBOLS.index(sym)
    return 0 if i < 21 else 1 if i < 24 else 2


def compose(keys):
    """The reading the composer shows after these symbol keys, typed in order."""
    cols = ["", "", ""]
    for c in keys:
        cols[_column(c)] = c
    return "".join(cols)


def _split(syl):
    return "".join(c for c in syl if c in SYMBOLS), "".join(c for c in syl if c not in SYMBOLS)


def _normal(syl, pairs):
    """syl with every enabled pair (bit b of `pairs`) merged to the first symbol of the pair."""
    second = {p[1]: p[0] for b, p in enumerate(FUZZY_PAIRS) if pairs >> b & 1}
    return "".join(second.get(c, c) for c in syl)


class Tolerance:
    """`syllables`: the lexicon's syllables; `single`: those that have a one-syllable entry (only they are adjacent-key alternatives)."""

    def __init__(self, syllables, single):
        self.syllables = sorted(set(syllables))
        self.sound_table = [{}]
        for pairs in range(1, FUZZY_SOUND + 1):
            groups = {}
            for s in self.syllables:
                groups.setdefault(_normal(s, pairs), set()).add(s)
            self.sound_table.append({s: sorted(g - {s}) for g in groups.values() if len(g) > 1 for s in g})
        shown = {}
        for s in sorted(set(single) & set(self.syllables)):
            keys, tone = _split(s)
            for i, c in enumerate(keys):
                for nb in NEIGHBOURS.get(c, []):
                    r = compose(keys[:i] + nb + keys[i + 1:]) + tone
                    if r != s:
                        shown.setdefault(r, set()).add(s)
        self.adjacent_table = {r: sorted(v) for r, v in shown.items()}

    def sound(self, syl, flags):
        """Alternatives of `syl` with the pairs in bits 0-3 of `flags` enabled."""
        return self.sound_table[flags & FUZZY_SOUND].get(syl, [])

    def adjacent(self, shown):
        """Syllables a shown reading may have been typed from."""
        return self.adjacent_table.get(shown, [])
