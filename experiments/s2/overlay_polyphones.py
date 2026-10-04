"""S2r-2 §3：疊加層裡「讀音是單字破音字取最高分猜出來」的列，依猜錯時的差異分類（只量測，不改資料）。

猜出來的列＝用基底把詞切成基底詞（和 ime.Lexicon.to_syllables 同一個 DP），切出來的單字段是破音字
（基底單字列有 2 個以上不同讀音）、而且那個字不是一／不（一、不另有規則）。每個猜出來的字比較「選到的讀音」和基底單字列的
其他讀音：全部只差輕聲／本調 → 輕聲；全部聲母韻母相同、聲調不同 → 聲調；有任何一個聲母或韻母不同 → 聲韻。
一列取它所有猜出來的字裡最嚴重的類別。只讀公開資料。

用法：python3 experiments/s2/overlay_polyphones.py [--sample PATH]   --sample 把「聲韻」類抽 50 列（種子 20261004）寫成 TSV
"""
import argparse
import collections
import math
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "reference", "proto"))
import ime  # noqa: E402

BASE = os.path.join(ROOT, "data", "lexicon", "mcbpmf-data.txt")
OVERLAY = os.path.join(ROOT, "data", "lexicon", "overlay-add.tsv")
SEED = 20261004
TONES = "ˊˇˋ˙"
CATS = ("neutral", "tone", "segmental")


def segments(base, text):
    """和 ime.Lexicon.to_syllables 相同的 DP，回傳切出的詞（讀音不用）。"""
    n = len(text)
    best = [(-math.inf, None)] * (n + 1)
    best[0] = (0.0, None)
    for i in range(1, n + 1):
        for L in range(1, min(base.max_len, i) + 1):
            w = text[i - L:i]
            if w in base.by_word and best[i - L][0] > -math.inf:
                s = best[i - L][0] + base.by_word[w][1]
                if s > best[i][0]:
                    best[i] = (s, i - L)
    out, i = [], n
    while i > 0:
        j = best[i][1]
        out.append((j, i))
        i = j
    return list(reversed(out))


def char_cat(chosen, alts):
    """選到的單字讀音 vs 其他讀音的最嚴重差異。"""
    cat = 0
    strip = lambda s: "".join(c for c in s if c not in TONES)
    for a in alts:
        if strip(a) != strip(chosen):
            return 2
        if "˙" in a or "˙" in chosen:
            continue
        cat = max(cat, 1)
    return cat


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample")
    a = ap.parse_args()
    base = ime.Lexicon(BASE)
    readings = collections.defaultdict(set)         # 單字 -> 基底單字列的所有讀音
    for syls, ents in base.by_reading.items():
        if len(syls) == 1:
            for w, _ in ents:
                if len(w) == 1:
                    readings[w].add(syls[0])
    rows = [l.rstrip("\n").split("\t") for l in open(OVERLAY, encoding="utf-8")]
    primary = {}
    for r, w, _, _ in rows:
        primary.setdefault(w, r)                    # 主要列在前
    total, per_cat, chars = len(primary), [0, 0, 0], [collections.Counter() for _ in CATS]
    guessed, samples = 0, []
    for w, r in primary.items():
        syls = r.split("-")
        cat, gch = -1, []
        for i, j in segments(base, w):
            c = w[i:j]
            if j - i == 1 and len(readings[c]) > 1 and c not in "一不":
                k = char_cat(syls[i], readings[c] - {syls[i]})
                cat = max(cat, k)
                gch.append((c, k, i))
        if cat < 0:
            continue
        guessed += 1
        per_cat[cat] += 1
        for c, k, _ in gch:
            if k == cat:
                chars[cat][c] += 1
        if cat == 2:
            samples.append((w, r, " ".join(f"{c}:{syls[i]}/" + "/".join(sorted(readings[c] - {syls[i]})) for c, k, i in gch if k == 2)))
    print(f"overlay words {total}; guessed (polyphone single-char segment, excluding 一/不) {guessed} ({guessed / total:.1%})")
    for k, name in enumerate(CATS):
        print(f"  {name}: {per_cat[k]}  top20 chars: " + " ".join(f"{c}{n}" for c, n in chars[k].most_common(20)))
    if a.sample:
        pick = random.Random(SEED).sample(samples, 50)
        with open(a.sample, "w", encoding="utf-8") as f:
            f.write("word\treading\tguessed char:chosen/alternatives\n")
            for w, r, d in pick:
                f.write(f"{w}\t{r}\t{d}\n")


if __name__ == "__main__":
    main()
