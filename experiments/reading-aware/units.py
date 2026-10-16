"""Position-based reading labels and units (contract docs/contracts/reading-aware-0.md 3.4).

A unit is the thing the counts are about: a multi-character word is one unit (its string); a single character the lexicon knows with one
reading is the character itself; a single character with two or more lexicon readings is `字 U+0001 讀音`. The label of a character
depends only on its position in the (variant-merged) text, never on how the lattice cuts the text, so every cut that uses the position
gets the same unit. Symbols are single characters: c this character, p the one before it, p2 the second before, n the one after, n2 the
second after ("" when there is none; sets below are sets of characters, so "" never matches).

Primary reading = tools/readings.py rules 0, 1, 3, 4 for a single character (reading_overrides.tsv, 一/不 citation tone, heterophony1.list,
lexicon first); rule 2 (Moedict) is for evaluation inputs only and is not used. Rule readings that the lexicon does not list for the
character are replaced by the primary reading and counted in Labeller.fallbacks.
"""
import collections

import rcommon

DIGITS = set("〇一二三四五六七八九十百兩幾")
HAN_NUM_BEFORE_XING = set("這那每各第")
DE_BEFORE = set("不非總就也都還")       # 得 -> ㄉㄟˇ after these ...
DE_NOT_BEFORE = set("很太")             # ... unless followed by these
HAI_HUAN = set("錢給你我他書債款清")     # 還 -> ㄏㄨㄢˊ before these
ZHONG_CHONG = set("新做來打建組複")      # 重 -> ㄔㄨㄥˊ
KONG_P = set("有沒抽撥得")               # 空 -> ㄎㄨㄥˋ after these
KONG_N = set("出位缺檔")
ZHANG_N = set("大得出在成高歪相")        # 長 -> ㄓㄤˇ
CITATION = {"一": "ㄧ", "不": "ㄅㄨˋ"}


def load_overrides(path):
    """tools/reading_overrides.tsv: single-character rows only, {char: reading} (multi-character rows are words, not positions)."""
    out = {}
    for line in open(path, encoding="utf-8"):
        if line.startswith("#") or "\t" not in line:
            continue
        w, r = line.rstrip("\n").split("\t")
        if len(w) == 1 and len(r.split()) == 1:
            out[w] = r
    return out


def load_hetero(path):
    """heterophony1.list: `字 讀音` per line (tools/readings.py reads it the same way; a repeated character keeps the last)."""
    return dict(l.split() for l in open(path, encoding="utf-8") if len(l.split()) == 2)


class Labeller:
    def __init__(self, table, overrides=None, hetero=None):
        """table = rcommon.char_readings(lexicon) of the lexicon the decoder will use."""
        self.table = table
        self.overrides, self.hetero = overrides or {}, hetero or {}
        self.fallbacks = collections.Counter()   # rule readings the lexicon did not have, by character
        self._primary = {}

    def multi(self, c):
        """True when the character has two or more readings in the lexicon (its units carry a reading)."""
        return len(self.table.get(c, ())) >= 2

    def primary(self, c):
        r = self._primary.get(c)
        if r is None:
            cands = [x for x, _ in self.table[c]]
            citation = [x for x in cands if x == CITATION.get(c, x)]
            if self.overrides.get(c) in cands:                                   # rule 0
                r = self.overrides[c]
            elif c in CITATION and len(citation) == 1 and len(citation) < len(cands):   # rule 1
                r = citation[0]
            elif self.hetero.get(c) in cands:                                    # rule 3
                r = self.hetero[c]
            else:                                                                 # rule 4
                r = cands[0]
            self._primary[c] = r
        return r

    def _rule(self, text, i, c):
        """The reading the position rules of contract 3.4 give, or None for "primary reading"."""
        at = lambda k: text[k] if 0 <= k < len(text) else ""
        p, p2, n, n2 = at(i - 1), at(i - 2), at(i + 1), at(i + 2)
        if c in rcommon.TAIL_WHITELIST:
            return next((r for r, _ in self.table[c] if rcommon.is_dot(r)), None)   # ˙ when the lexicon has one, else primary
        if c == "了":
            return "ㄌㄜ˙"
        if c == "的":
            return "ㄉㄜ˙"
        if c == "得":
            return "ㄉㄟˇ" if p in DE_BEFORE and n not in DE_NOT_BEFORE else "ㄉㄜ˙"
        if c == "地":
            return "ㄉㄜ˙" if p and p2 == p else "ㄉㄧˋ"
        if c == "還":
            return "ㄏㄨㄢˊ" if n in HAI_HUAN else "ㄏㄞˊ"
        if c == "重":
            return "ㄔㄨㄥˊ" if n in ZHONG_CHONG else None
        if c == "空":
            return "ㄎㄨㄥˋ" if p in KONG_P or n in KONG_N or (n == "下" and n2 == "來") else None
        if c == "長":
            return "ㄓㄤˇ" if n in ZHANG_N else None
        if c == "行":
            return "ㄏㄤˊ" if p in DIGITS or (p.isascii() and p.isdigit()) or p in HAN_NUM_BEFORE_XING else None
        return None

    def label(self, text, i, c=None):
        """Reading of the character at position i of `text` (variant-merged); always one of the lexicon readings of the character."""
        c = c or text[i]
        r = self._rule(text, i, c)
        if r is None:
            return self.primary(c)
        if r in {x for x, _ in self.table[c]}:
            return r
        self.fallbacks[c] += 1
        return self.primary(c)

    def unit(self, word, pos, text):
        """The unit of `word` starting at position `pos` of `text`."""
        if len(word) == 1 and self.multi(word):
            return word + rcommon.U + self.label(text, pos, word)
        return word


def base_word(unit):
    """The word string of a unit."""
    return unit.split(rcommon.U, 1)[0]
