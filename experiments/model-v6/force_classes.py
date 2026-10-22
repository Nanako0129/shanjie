"""model-v6 契約 §2.2b：把罕見量詞搬進「個」的類別。分群（cluster.py --keep-from）之後、build_classes.py 之前跑。

讀 cluster.py 的輸出資料夾：edges.npz（vocab、uni：加權後的 unigram，build_classes.py --edges 讀的同一個欄位）與 cls-N-K.npz（cls、K、N、mi）。
量詞表（classifiers.txt，一行一個，# 開頭是註解）裡 uni 少於 --threshold（預設 1000）、而且類別和錨點詞（預設「個」）不同的詞，改成錨點的類別
（原本沒有類別的詞（-1）也算不同）。uni >= 門檻的量詞不動；不在詞彙裡的表列詞印出來、略過；錨點不在詞彙裡或沒有類別就停。
輸出另存成新的 cls-N-K 檔（--out，已存在就停，絕不覆寫 cluster.py 的輸出），印出它的 SHA-256 與搬了哪些詞（詞、原類別、uni）；
把這個檔交給 build_classes.py --cls。檔案是 np.savez 寫的（zip 內有時間戳），所以雜湊記的是這一次產生的檔，不是可重現的值。
用法：python3 experiments/model-v6/force_classes.py --dir <cluster 輸出資料夾> --N 40000 --K 512 --out <新檔.npz> [--list classifiers.txt] [--anchor 個] [--threshold 1000]
"""
import argparse
import hashlib
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))


def read_list(path):
    seen = {}
    for line in open(path, encoding="utf-8"):
        w = line.strip()
        if w and not w.startswith("#"):
            seen[w] = None
    return list(seen)


def force(vocab, uni, cls, words, anchor, threshold):
    """回傳 (新 cls, 搬了的 [(詞, 原類別, uni)], 不在詞彙的表列詞)。cls 對應 vocab 前面的位置，-1 是沒有類別。"""
    ids = {w: i for i, w in enumerate(vocab)}
    if anchor not in ids:
        raise SystemExit(f"anchor {anchor!r} is not in the vocabulary")
    target = int(cls[ids[anchor]])
    if target < 0:
        raise SystemExit(f"anchor {anchor!r} has no class")
    new, moved, skipped = np.array(cls, copy=True), [], []
    for w in words:
        i = ids.get(w)
        if i is None:
            skipped.append(w)
        elif uni[i] < threshold and cls[i] != target:
            moved.append((w, int(cls[i]), int(uni[i])))
            new[i] = target
    return new, moved, skipped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="cluster.py 的輸出資料夾（edges.npz、cls-N-K.npz）")
    ap.add_argument("--N", type=int, required=True)
    ap.add_argument("--K", type=int, required=True)
    ap.add_argument("--out", required=True, help="新的 cls 檔；已存在就停")
    ap.add_argument("--list", default=os.path.join(HERE, "classifiers.txt"))
    ap.add_argument("--anchor", default="個")
    ap.add_argument("--threshold", type=int, default=1000)
    a = ap.parse_args()
    src = os.path.join(a.dir, f"cls-{a.N}-{a.K}.npz")
    if os.path.exists(a.out):
        sys.exit(f"--out exists, refusing to overwrite: {a.out}")
    z = np.load(os.path.join(a.dir, "edges.npz"), allow_pickle=True)
    c = np.load(src)
    vocab, uni = list(z["vocab"]), z["uni"]
    assert int(c["K"]) == a.K and len(c["cls"]) == len(vocab) == len(uni)
    new, moved, skipped = force(vocab, uni, c["cls"], read_list(a.list), a.anchor, a.threshold)
    with open(a.out, "wb") as f:
        np.savez(f, **{**{k: c[k] for k in c.files}, "cls": new})
    print(f"sha256={hashlib.sha256(open(a.out, 'rb').read()).hexdigest()} moved={len(moved)} skipped={len(skipped)} out={a.out}")
    for w in skipped:
        print(f"skipped (not in vocabulary): {w}")
    for w, old, u in moved:
        print(f"moved {w}\told_class={old}\tuni={u}")


if __name__ == "__main__":
    main()
