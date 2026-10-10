"""Kneser-Ney 接續次數側檔（docs/contracts/kn-smoothing.md §2.1），離線量測用，不動模型檔。

N(w) = |{ v : once(v), v != "<s>", v 在模型詞彙裡, bi[(v, w)] >= theta }|，bi 是 build_lm.merged_counts 的整數二元組
（與 build_lm.build 同一套加權、四捨五入、去 0、異體合併；build 丟掉 v 不在詞彙裡的二元組，這裡也不數）。
</s> 與詞彙外的 w 不必擋：side_bytes 只取詞彙 id >= 2 的 N。類的非代表成員取代表成員的 N；模型詞彙裡有、計數裡沒有的詞 N = 0。
側檔 SJKN0002（docs/contracts/kn-core.md §1，little-endian）：magic b"SJKN0002"、V u32、模型檔 SHA-256 32 bytes、theta u32、beta f64、
ΣN′ u64、N u32[V]（id 0、1 為 0）。ΣN′ 是預先彙總的值：用 lm.kn_total（和 lm.py 重算時同一個函式）加 build_lm.variant_classes 的類算好寫進檔頭，
Rust 只讀檔頭；lm_eval.py 照舊傳 kn_classes 重算比對，那是它在上游的檢查。不再產生 SJKN0001（lm.py 仍讀得到舊檔）。
用法：S2_WORK=計數目錄 python3 tools/kn_cont.py --lm MODEL --theta 1|2|3 --beta β --out FILE
"""
import argparse
import hashlib
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "reference", "proto"))
import build_lm  # noqa: E402
import lm as L  # noqa: E402

MAGIC = b"SJKN0002"


def continuation(bi, cls, theta, vocab):
    """詞 -> N（只含代表成員與不在類裡的詞；非代表成員由 side_bytes 取代表的）。"""
    once = lambda w: w not in cls or cls[w][0] == w
    known = set(vocab)
    n = {}
    for (v, w), c in bi.items():   # 每個 (v, w) 在 bi 裡只出現一次，所以直接計數
        if c >= theta and v != "<s>" and v in known and once(v):
            n[w] = n.get(w, 0) + 1
    return n


def side_bytes(model_path, n, cls, theta, beta):
    """詞彙與雜湊都取自 model_path 這一個檔，N 陣列一定對齊它。"""
    raw = open(model_path, "rb").read()
    vocab = L.BigramLM(model_path, classes=False).vocab
    rep = lambda w: cls[w][0] if w in cls else w
    vals = [0, 0] + [n.get(rep(w), 0) for w in vocab[2:]]
    total = L.kn_total(vocab, [x + 1 for x in vals], cls)
    return MAGIC + struct.pack("<I", len(vocab)) + hashlib.sha256(raw).digest() + struct.pack("<IdQ", theta, beta, total) \
        + struct.pack(f"<{len(vocab)}I", *vals)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lm", required=True)
    ap.add_argument("--theta", type=int, required=True, choices=(1, 2, 3))
    ap.add_argument("--beta", type=float, required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    if not 0.0 <= a.beta <= 1.0:
        ap.error("--beta must be in [0, 1]")
    vocab = L.BigramLM(a.lm, classes=False).vocab
    _, bi, cls = build_lm.merged_counts()
    data = side_bytes(a.lm, continuation(bi, cls, a.theta, vocab), cls, a.theta, a.beta)
    V = len(vocab)
    np1 = [x + 1 for x in struct.unpack_from(f"<{V}I", data, 64)]
    zero = sum(1 for x in np1[2:] if x == 1)
    print(f"theta={a.theta} beta={a.beta:g} V={V} N0_ratio={zero / (V - 2):.4f} sumN1={struct.unpack_from('<Q', data, 56)[0]} sha256={hashlib.sha256(data).hexdigest()}")
    open(a.out, "wb").write(data)


if __name__ == "__main__":
    main()
