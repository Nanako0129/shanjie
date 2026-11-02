#!/usr/bin/env python3
"""Tone-sandhi variant of a rows file (前文|句子|讀音): 一 and 不 read the way they are spoken, for a report-only second run
(the maintainer types the sandhi tones; tools/readings.py writes the base tones ㄧ / ㄅㄨˋ that dictionaries and exams use).

  sandhi.py IN OUT      -> OUT has the same rows, only the 一 / 不 syllables changed; counts only on stdout (no row text).

Rules, by the next syllable's tone (the standard spoken sandhi), only when the next character of the sentence is that syllable's
(punctuation, a digit or a letter right after 一 / 不 ends the phrase; 個 written neutral counts as its underlying 4th tone):
  不 ㄅㄨˋ: before a 4th tone -> ㄅㄨˊ; otherwise unchanged.
  一 ㄧ:    before a 4th tone -> ㄧˊ; before a 1st, 2nd or 3rd tone -> ㄧˋ; unchanged at the end, before a neutral tone,
            after 第 十 初 統 唯 週 期 萬 (ordinal / counting / end of a word) and before 月 號 樓 (dates and floors).
A row whose sentence has another number of Han characters than syllables is copied unchanged and counted.
ponytail: a short exception list, not a word-level rule (e.g. 一 inside 一一 or a list of numbers is still changed).
"""
import os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "tools"))
from triage import is_han  # noqa: E402  (core/src/learn.rs is_han, the engine's own set)

BASE = {"一": "ㄧ", "不": "ㄅㄨˋ"}
KEEP_AFTER = set("第十初統唯週期萬")
KEEP_BEFORE = set("月號樓")


def tone(syl):
    return {"ˊ": 2, "ˇ": 3, "ˋ": 4, "˙": 0}.get(syl[-1], 1)


def sandhi(sentence, syls):
    """-> (new syllables, number changed), or None when characters and syllables do not align."""
    pos = [k for k, c in enumerate(sentence) if is_han(c)]
    han = [sentence[k] for k in pos]
    if len(han) != len(syls):
        return None
    out, changed = list(syls), 0
    for i, (c, s) in enumerate(zip(han, syls)):
        # the next syllable must be the next character: a 一 / 不 before punctuation, a digit or a letter ends its phrase
        if c not in BASE or s != BASE[c] or i + 1 == len(syls) or pos[i + 1] != pos[i] + 1:
            continue
        t = tone(syls[i + 1])
        if t == 0 and han[i + 1] == "個":   # 個 written neutral keeps its 4th tone underneath: 一個 is spoken ㄧˊ
            t = 4
        if c == "不":
            new = "ㄅㄨˊ" if t == 4 else s
        elif t == 0 or (i and han[i - 1] in KEEP_AFTER) or han[i + 1] in KEEP_BEFORE:
            new = s
        else:
            new = "ㄧˊ" if t == 4 else "ㄧˋ"
        changed += new != s
        out[i] = new
    return out, changed


def sandhi_rows(text):
    """-> (the sandhi text of a whole rows file, counts): every line that is a row gets sandhi(); other lines are kept."""
    n = {"rows": 0, "rows_changed": 0, "syllables_changed": 0, "misaligned": 0}
    lines = []
    for line in text.split("\n"):
        cr = "\r" if line.endswith("\r") else ""     # a CRLF file keeps its endings; the syllables are read as the CLI does
        parts = line[:len(line) - len(cr)].split("|")
        if len(parts) == 3:
            n["rows"] += 1
            r = sandhi(parts[1], parts[2].split())
            if r is None:
                n["misaligned"] += 1
            else:
                n["syllables_changed"] += r[1]
                n["rows_changed"] += r[1] > 0
                line = "|".join(parts[:2] + [" ".join(r[0])]) + cr
        lines.append(line)
    return "\n".join(lines), n


def main(src, dst):
    with open(src, encoding="utf-8", newline="") as f:
        out, n = sandhi_rows(f.read())
    with open(dst, "x", encoding="utf-8", newline="") as f:
        f.write(out)
    print(" ".join(f"{k} {v}" for k, v in n.items()))


if __name__ == "__main__":
    main(*sys.argv[1:3])
