"""S2f 契約 §3.5：台灣字形與它的異體字在計數檔裡的字次數（只報告，不當關卡）。
字次數＝Σ 詞次 × 該字在詞裡出現的次數；詞次是計數檔的 `uni`，各檔乘權重後相加（權重同 tools/build_lm.py）。
用法：python3 experiments/s2f/char_counts.py --before 檔:權重 … --after 檔:權重 …
"""
import argparse
import os
import pickle

PAIRS = [("床", "牀"), ("秘", "祕"), ("灶", "竈"), ("粽", "糉"), ("庄", "莊"), ("痴", "癡")]


def counts(specs):
    out = dict.fromkeys([ch for p in PAIRS for ch in p], 0)
    for spec in specs:
        path, w = spec.rsplit(":", 1)
        for word, n in pickle.load(open(os.path.expanduser(path), "rb"))["uni"].items():
            for ch in out:
                if ch in word:
                    out[ch] += word.count(ch) * n * float(w)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", nargs="+", required=True)
    ap.add_argument("--after", nargs="+", required=True)
    a = ap.parse_args()
    b, f = counts(a.before), counts(a.after)
    for x, y in PAIRS:
        print(f"{x}/{y}\t{b[x]:.0f}/{b[y]:.0f}\t->\t{f[x]:.0f}/{f[y]:.0f}")


if __name__ == "__main__":
    main()
