"""Shared pieces of experiments/reading-aware (contract docs/contracts/reading-aware-0.md). Nothing here changes the repo's own code.

Importing this module sets sys.path the way the derisk-1015 tools do (reference/proto, tools, experiments/s2, and the two derisk-1015
folders whose modules are reused: common/patched_eval.py and e1/count.py).
"""
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for _sub in ("reference/proto", "tools", "experiments/s2", "experiments/derisk-1015/common", "experiments/derisk-1015/e1"):
    _p = os.path.join(ROOT, _sub)
    if _p not in sys.path:
        sys.path.append(_p)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import ime  # noqa: E402

U = "\x01"                                  # joins a character and its reading in a unit: 字 U+0001 讀音 (contract 3.4)
DOT = "˙"
TONES = "ˊˇˋ˙"
LEXICON = os.path.join(ROOT, "data", "lexicon", "mcbpmf-data.txt")
# Contract 3.3: the sentence-final particles whose one-tone typing the A2 arm is about (哪 is not in it).
TAIL_WHITELIST = "吧嗎呢啦嘛啊呀喔哦欸嘞咧囉哩唄"


def load_lexicon(extra=None):
    """lm_eval.main's own base lexicon (base data plus ime.OVERLAYS, plus --extra-overlay)."""
    return ime.Lexicon(LEXICON, overlay=ime.overlays(extra))


def char_readings(lex):
    """{character: [(reading, lp)]}, the single-syllable entries of single-character words, best score first (stable, like tools/readings.py)."""
    out = {}
    for key, entries in lex.by_reading.items():
        if len(key) == 1:
            for w, lp in entries:
                if len(w) == 1:
                    out.setdefault(w, []).append((key[0], lp))
    for v in out.values():
        v.sort(key=lambda x: -x[1])
    return out


def strip_tone(reading):
    return reading.rstrip(TONES)


def is_dot(reading):
    return reading.endswith(DOT)


def segment(sent, syls, lex):
    """Best-scoring cut of `sent` into lexicon words whose reading is exactly the given syllables: [(start, end)] or None.
    Scores are lexicon scores only (strictly greater wins, first best kept, like build_counts.segment); the language model is not consulted."""
    n = len(sent)
    if len(syls) != n:
        return None
    best = [(-math.inf, 0)] * (n + 1)
    best[0] = (0.0, 0)
    for i in range(1, n + 1):
        for L in range(1, min(lex.max_len, i) + 1):
            if best[i - L][0] == -math.inf:
                continue
            word = sent[i - L:i]
            for w, lp in lex.by_reading.get(tuple(syls[i - L:i]), ()):
                if w == word:
                    if best[i - L][0] + lp > best[i][0]:
                        best[i] = (best[i - L][0] + lp, i - L)
                    break
    if best[n][0] == -math.inf:
        return None
    spans, i = [], n
    while i > 0:
        spans.append((best[i][1], i))
        i = best[i][1]
    return spans[::-1]


def standalone(sent, syls, lex):
    """Positions of `sent` that are single-character words in segment()'s cut (empty list when there is no cut)."""
    spans = segment(sent, syls, lex)
    return [a for a, b in spans if b - a == 1] if spans else []


def load_rows(spec):
    """Rows [(sentence, [syllables], context)] exactly as lm_eval evaluates them: a file path, or `dev:N` = the first N rows of the
    name-sorted eval/dev/*.txt (lm_eval's --dev N)."""
    import glob
    import lm_eval
    if spec.startswith("dev:"):
        return lm_eval.rows_of(sorted(glob.glob(os.path.join(ROOT, "eval", "dev", "*.txt"))))[:int(spec[4:])]
    return lm_eval.rows_of([spec])
