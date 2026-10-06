"""S2h §4：切尾集。把調參集每列切成「前文 + 尾段」，量「送出前半句再接著打」的情境。

每列：用詞庫最高分切分（experiments/s2/build_counts.py 的 segment()）切正解句子，至少 2 個詞才用；
尾段是最後一個詞，前文是其餘部分，讀音照字數切開（字數和音節數不同的列丟掉）。
輸入 ~/.cache/shanjie/work/s2/tune/{cvtune,wikitune}.txt，輸出 ~/.cache/shanjie/work/s2h/{cvtail,wikitail}.txt（格式 前文|尾段|讀音）。
用法：python3 experiments/s2h/split_tail.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "reference", "proto"))
sys.path.insert(0, os.path.join(ROOT, "experiments", "s2"))
import ime  # noqa: E402
from build_counts import segment  # noqa: E402

TUNE = os.path.expanduser("~/.cache/shanjie/work/s2/tune")
OUT = os.path.expanduser("~/.cache/shanjie/work/s2h")


def main():
    os.makedirs(OUT, exist_ok=True)
    lex = ime.Lexicon(os.path.join(ROOT, "data", "lexicon", "mcbpmf-data.txt"),
                      overlay=os.path.join(ROOT, "data", "lexicon", "overlay-add.tsv"))
    for src, dst in (("cvtune", "cvtail"), ("wikitune", "wikitail")):
        kept = total = 0
        with open(os.path.join(OUT, dst + ".txt"), "w", encoding="utf-8") as out:
            for line in open(os.path.join(TUNE, src + ".txt"), encoding="utf-8"):
                p = line.rstrip("\n").split("|")
                if len(p) != 3:
                    continue
                total += 1
                syls = p[2].split()
                words = segment(lex, p[1])
                if not words or len(words) < 2 or len("".join(words)) != len(syls):
                    continue
                k = len(words[-1])
                out.write(f"{''.join(words[:-1])}|{words[-1]}|{' '.join(syls[-k:])}\n")
                kept += 1
        print(f"{src} -> {dst}: {kept} of {total} rows")


if __name__ == "__main__":
    main()
