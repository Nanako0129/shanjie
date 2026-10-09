"""Kneser-Ney 接續次數側檔（docs/contracts/kn-smoothing.md §2.1），離線量測用，不動模型檔。

N(w) = |{ v : once(v), v != "<s>", w != "</s>", bi[(v, w)] >= theta }|，bi 是 build_lm.merged_counts 的整數二元組
（與 build_lm.build 同一套加權、四捨五入、去 0、異體合併）。類的非代表成員取代表成員的 N；模型詞彙裡有、計數裡沒有的詞 N = 0。
側檔（little-endian）：magic b"SJKN0001"、V u32、模型檔 SHA-256 32 bytes、theta u32、N u32[V]（id 0、1 為 0）。
用法：S2_WORK=計數目錄 python3 tools/kn_cont.py --lm MODEL --theta 1|2|3 --out FILE
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

MAGIC = b"SJKN0001"


def continuation(bi, cls, theta):
    """詞 -> N（只含代表成員與不在類裡的詞；非代表成員由 side_bytes 取代表的）。"""
    once = lambda w: w not in cls or cls[w][0] == w
    seen = {}
    for (v, w), c in bi.items():
        if c >= theta and v != "<s>" and w != "</s>" and once(v):
            seen.setdefault(w, set()).add(v)
    return {w: len(vs) for w, vs in seen.items()}


def side_bytes(model_path, n, cls, theta):
    raw = open(model_path, "rb").read()
    vocab = L.BigramLM(model_path, classes=False).vocab
    rep = lambda w: cls[w][0] if w in cls else w
    vals = [0, 0] + [n.get(rep(w), 0) for w in vocab[2:]]
    return MAGIC + struct.pack("<I", len(vocab)) + hashlib.sha256(raw).digest() + struct.pack("<I", theta) \
        + struct.pack(f"<{len(vocab)}I", *vals)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lm", required=True)
    ap.add_argument("--theta", type=int, required=True, choices=(1, 2, 3))
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    _, bi, cls = build_lm.merged_counts()
    data = side_bytes(a.lm, continuation(bi, cls, a.theta), cls, a.theta)
    V = struct.unpack_from("<I", data, 8)[0]
    ns = struct.unpack_from(f"<{V}I", data, 48)
    zero = sum(1 for x in ns[2:] if x == 0)
    vocab = L.BigramLM(a.lm, classes=False).vocab
    sigma = sum(x + 1 for w, x in zip(vocab[2:], ns[2:]) if w not in cls or cls[w][0] == w)
    print(f"theta={a.theta} V={V} N0_ratio={zero / (V - 2):.4f} sumN1={sigma} sha256={hashlib.sha256(data).hexdigest()}")
    open(a.out, "wb").write(data)


if __name__ == "__main__":
    main()
