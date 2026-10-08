"""S2k：從第一段的分群結果建詞類別檔 classes.sjc（docs/contracts/s2k-word-classes.md §4.1）。

輸入：experiments/s2-classes 的 edges.npz（詞彙、unigram、bigram 邊）與 cls-<N>-<K>.npz（分群），加上模型 bigram.sjlm。
類別表依模型詞 id 存，Python（reference/proto/lm.py）與 Rust（core/src/lm.rs）讀同一份數字，不在載入時重算。

二進位格式（little-endian）：
  magic     8 bytes   b"SJCL0001"
  model_sha 32 bytes  bigram.sjlm 的 SHA-256（不符就載入失敗）
  K         u32       類別數（<s> 是類別 K+1、</s> 是類別 K+2，所以表是 (K+3) x (K+3)）
  mu        f64       類別項的權重 μ
  V         u32       模型詞彙數
  cls       u16[V]    每個模型詞 id 的類別；0xFFFF 沒有類別。id 0 (<s>) = K+1、id 1 (</s>) = K+2
  emit      f64[V]    發射機率 P(w|c(w)) = uni(w) / 類別 c 的 uni 總和；<s>、</s> 是 1.0；沒有類別的詞是 0.0
  Pc        f64[(K+3)^2] 列主序的 P(d|c) = (M[c,d] + ε) / (M[c].sum + (K+3)ε)，M 是全部（剪枝前）加權 bigram 的類別計數
總長 = 8 + 32 + 4 + 8 + 4 + 10V + 8(K+3)^2。算法與 experiments/s2-classes/classlm.py 的 ClassLM._load 相同。

對應：classlm.py 依字串查類別，這裡依模型詞 id；有類別但沒有模型 id 的詞（含其中詞庫產得出的）只記錄數目，不套類別項（契約 §4.1）。
用法：python3 tools/build_classes.py [--edges edges.npz] [--cls cls-40000-512.npz] [--lm data/lm/bigram.sjlm] [--out data/lm/classes.sjc]
"""
import argparse
import hashlib
import os
import struct
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "reference", "proto"))
import ime  # noqa: E402
import lm as L  # noqa: E402

WORK = os.path.expanduser("~/.cache/shanjie/work/s2k")
EPS, MU = 0.1, 0.8
NONE = 0xFFFF


def pack(model_sha, K, mu, cls, emit, Pc):
    """檔案的位元組（格式見開頭）；測試也用它。"""
    return (L.CLASS_MAGIC + model_sha + struct.pack("<IdI", K, mu, len(cls)) + struct.pack(f"<{len(cls)}H", *cls)
            + struct.pack(f"<{len(emit)}d", *emit) + np.ascontiguousarray(Pc, dtype="<f8").tobytes())


def build(edges, cls_file, lm_path, producible):
    z = np.load(edges, allow_pickle=True)
    vocab, uni, src, dst, cnt = list(z["vocab"]), z["uni"], z["src"], z["dst"], z["cnt"]
    V = len(vocab)
    c = np.load(cls_file)
    K = int(c["K"])
    cls = np.concatenate([c["cls"], [K + 1, K + 2]])
    K3 = K + 3
    ok = (cls[src] >= 0) & (cls[dst] >= 0)
    M = np.bincount(cls[src[ok]].astype(np.int64) * K3 + cls[dst[ok]], weights=cnt[ok], minlength=K3 * K3).reshape(K3, K3)
    Pc = (M + EPS) / (M.sum(1, keepdims=True) + K3 * EPS)
    cu = np.bincount(cls[:V][cls[:V] >= 0], weights=uni[cls[:V] >= 0], minlength=K3)
    by_word = {"<s>": (K + 1, 1.0), "</s>": (K + 2, 1.0)}
    for i, w in enumerate(vocab):
        if cls[i] >= 0:
            by_word[w] = (int(cls[i]), uni[i] / cu[cls[i]])

    model = L.BigramLM(lm_path, classes=False)
    out_cls, out_emit = [], []
    for w in model.vocab:
        c_, e = by_word.get(w, (NONE, 0.0))
        out_cls.append(c_); out_emit.append(float(e))
    assert out_cls[0] == K + 1 and out_cls[1] == K + 2 and model.vocab[:2] == ["<s>", "</s>"]
    missing = [w for w in by_word if w not in model.ids]
    bad = [w for w in missing if w in producible]   # 模型裡計數為 0 的罕見詞：產品不套類別項（契約 §4.1 更正），只記錄數目
    blob = pack(hashlib.sha256(open(lm_path, "rb").read()).digest(), K, MU, out_cls, out_emit, Pc)
    return blob, dict(K=K, V=len(model.vocab), classed=sum(1 for x in out_cls if x != NONE),
                      with_class_no_model_id=len(missing), producible_among_them=len(bad))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--edges", default=os.path.join(WORK, "edges.npz"))
    ap.add_argument("--cls", default=os.path.join(WORK, "cls-40000-512.npz"))
    ap.add_argument("--lm", default=os.path.join(ROOT, "data", "lm", "bigram.sjlm"))
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "lm", "classes.sjc"))
    a = ap.parse_args()
    lex = ime.Lexicon(os.path.join(ROOT, "data", "lexicon", "mcbpmf-data.txt"), overlay=ime.OVERLAYS)
    blob, info = build(a.edges, a.cls, a.lm, set(lex.by_word))
    print(info, f"bytes={len(blob)} sha256={hashlib.sha256(blob).hexdigest()}")
    with open(a.out + ".tmp", "wb") as f:
        f.write(blob)
    os.replace(a.out + ".tmp", a.out)


if __name__ == "__main__":
    main()
