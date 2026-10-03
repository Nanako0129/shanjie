"""把 Wikidata 標籤做成分類詞包（疊加層格式 `讀音\\t詞\\t分數\\t來源`），寫到 ~/.cache/shanjie/work/names/<類別>.tsv。

標籤優先序 zh-tw → zh-hant → zh，再用 OpenCC 對照表（commit 3ac34aa）轉台灣繁體；只收 2–12 個純漢字、
不在詞庫（基底＋疊加層）裡的名稱。讀音用基底＋疊加層詞庫的 to_syllables；分數先給同字數基底詞的第 25 百分位
（和 S1 疊加層相同），實際使用時由 iter2.py 的 packs 設定加位移。
用法：python3 experiments/names/build_packs.py
"""
import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "reference", "proto"))
sys.path.insert(0, os.path.join(ROOT, "experiments", "s2"))
import ime  # noqa: E402
import build_counts as bc  # noqa: E402

SRC = os.path.expanduser("~/.cache/shanjie/sources/wikidata")
OUT = os.path.expanduser("~/.cache/shanjie/work/names")
SCORE = {2: -7.17149945, 3: -7.04116568, 4: -6.60980192}
HAN = re.compile(r"^[一-鿿]{2,12}$")


def main():
    os.makedirs(OUT, exist_ok=True)
    lex = ime.Lexicon(os.path.join(ROOT, "data", "lexicon", "mcbpmf-data.txt"),
                      overlay=os.path.join(ROOT, "data", "lexicon", "overlay-add.tsv"))
    phrase, char, maxp = bc.load_conv()
    seen = set(lex.by_word)
    for f in sorted(glob.glob(f"{SRC}/*.tsv")):
        name = os.path.basename(f)[:-4]
        rows, skipped = [], 0
        for line in open(f, encoding="utf-8"):
            q, sl, tw, hant, zh = (line.rstrip("\n").split("\t") + [""] * 5)[:5]
            label = tw or hant or zh
            label = bc.convert(re.sub(r"[\s·・‧]", "", label), phrase, char, maxp)
            if not HAN.match(label) or label in seen:
                skipped += 1; continue
            syls = lex.to_syllables(label)
            if syls is None:
                skipped += 1; continue
            seen.add(label)
            rows.append(f"{'-'.join(syls)}\t{label}\t{SCORE.get(len(label), SCORE[4])!r}\twikidata-{name}:{q}\n")
        open(os.path.join(OUT, f"{name}.tsv"), "w", encoding="utf-8").writelines(sorted(rows, key=lambda r: r.split("\t")[1]))
        print(f"{name}: {len(rows)} 個新詞（略過 {skipped}）")


if __name__ == "__main__":
    main()
