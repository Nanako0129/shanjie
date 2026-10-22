#!/usr/bin/env python3
"""Tone-sandhi variant of a rows file (前文|句子|讀音): 一 and 不 read the way they are spoken, for a report-only second run
(the maintainer types the sandhi tones; tools/readings.py writes the base tones ㄧ / ㄅㄨˋ that dictionaries and exams use).

  sandhi.py IN OUT      -> OUT has the same rows, only the 一 / 不 syllables changed; counts only on stdout (no row text).

Rules, by the next syllable's tone (the standard spoken sandhi):
  不 ㄅㄨˋ: before a 4th tone -> ㄅㄨˊ; otherwise unchanged.
  一 ㄧ:    before a 4th tone -> ㄧˊ; before a 1st, 2nd or 3rd tone -> ㄧˋ; unchanged at the end, before a neutral tone,
            after 第 十 初 統 唯 週 期 萬 (ordinal / counting / end of a word) and before 月 號 樓 (dates and floors).
A row whose sentence has another number of Han characters than syllables is copied unchanged and counted.
ponytail: a short exception list, not a word-level rule (e.g. 一 inside 一一 or a list of numbers is still changed).
"""
import sys

BASE = {"一": "ㄧ", "不": "ㄅㄨˋ"}
KEEP_AFTER = set("第十初統唯週期萬")
KEEP_BEFORE = set("月號樓")


def tone(syl):
    return {"ˊ": 2, "ˇ": 3, "ˋ": 4, "˙": 0}.get(syl[-1], 1)


def is_han(c):
    return "一" <= c <= "鿿"


def sandhi(sentence, syls):
    """-> (new syllables, number changed), or None when characters and syllables do not align."""
    han = [c for c in sentence if is_han(c)]
    if len(han) != len(syls):
        return None
    out, changed = list(syls), 0
    for i, (c, s) in enumerate(zip(han, syls)):
        if c not in BASE or s != BASE[c] or i + 1 == len(syls):
            continue
        t = tone(syls[i + 1])
        if c == "不":
            new = "ㄅㄨˊ" if t == 4 else s
        elif t == 0 or (i and han[i - 1] in KEEP_AFTER) or han[i + 1] in KEEP_BEFORE:
            new = s
        else:
            new = "ㄧˊ" if t == 4 else "ㄧˋ"
        changed += new != s
        out[i] = new
    return out, changed


def main(src, dst):
    rows = changed = rows_changed = misaligned = 0
    lines = []
    with open(src, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            parts = line.split("|")
            if len(parts) == 3:
                rows += 1
                r = sandhi(parts[1], parts[2].split(" "))
                if r is None:
                    misaligned += 1
                else:
                    changed += r[1]
                    rows_changed += r[1] > 0
                    line = "|".join(parts[:2] + [" ".join(r[0])])
            lines.append(line)
    with open(dst, "x", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"rows {rows} rows_changed {rows_changed} syllables_changed {changed} misaligned {misaligned}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
