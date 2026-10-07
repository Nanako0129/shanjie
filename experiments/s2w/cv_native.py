"""S2w 契約 §4.3：cvtune-native，只留參考句不依賴任何轉換的 cvtune 列。
照 build_tune.py 的讀檔與 is_tune 重算：每個原句經 convert() 前後完全相同才算「沒被轉換改過」；
一個子句（4–30 字連續漢字）只要出現在任何「轉換後有改變」的原句（轉換前或轉換後的子句）裡，就不算 native。
cvtune.txt 的每一列（`前文|句子|讀音`）句子在 native 集合裡就留下，寫成 cvtune-native.txt（同一個資料夾）。
用法：python3 experiments/s2w/cv_native.py   （$S2_WORK/tune/cvtune.txt，$S2_WORK 的意義同 build_tune.py）
"""
import bz2
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "experiments", "s2"))
import build_counts as bc  # noqa: E402
import build_tune as bt  # noqa: E402


def main():
    phrase, char, maxp = bc.load_conv()
    clean, changed = set(), set()
    files = [os.path.join(bt.SRC, f) for f in sorted(os.listdir(bt.SRC)) if f.startswith("cv-")] + [os.path.join(bt.SRC, "cmn_sentences.tsv.bz2")]
    for p in files:
        f = bz2.open(p, "rt", encoding="utf-8") if p.endswith(".bz2") else open(p, encoding="utf-8")
        for line in f:
            raw = line.rstrip("\n").split("\t")[-1]
            if not bt.is_tune(raw):
                continue
            s = bc.convert(raw, phrase, char, maxp)
            if s == raw:
                clean.update(bt.HAN.findall(s))
            else:
                changed.update(bt.HAN.findall(s)); changed.update(bt.HAN.findall(raw))
    native = clean - changed
    src = os.path.join(bt.OUT, "cvtune.txt")
    rows = [l for l in open(src, encoding="utf-8") if l.split("|")[1] in native]
    dst = os.path.join(bt.OUT, "cvtune-native.txt")
    open(dst, "w", encoding="utf-8", newline="\n").write("".join(rows))
    total = sum(1 for _ in open(src, encoding="utf-8"))
    print(f"tune clauses: native-candidate {len(clean)}, touched by a changed sentence {len(changed & clean)}, native {len(native)}")
    print(f"cvtune-native: {len(rows)} of {total} cvtune rows → {dst}")
    if len(rows) < 1000:
        sys.exit("STOP: fewer than 1,000 native rows (contract §4.3)")


if __name__ == "__main__":
    main()
