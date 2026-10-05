"""S2：從語料計數建二進位 bigram 語言模型（善解的 LM 檔）。這支腳本是 S2 契約的參考實作。

輸入是 experiments/s2/ 的計數檔（`~/.cache/shanjie/work/s2/`；由 build_counts.py／build_counts_text.py 從鎖定版本的
來源產生，不進 repo）。各計數檔乘上整數權重後相加；bigram 只保留加權次數 ≥ PRUNE 的條目，但每個前文的
總次數 t 用剪枝前的完整計數（被剪掉的質量歸給回退，見 reference/proto/lm.py）。只存「至少保留一個條目」的前文。

二進位格式（little-endian）：
  magic          8 bytes  b"SJLM0001"
  V              u32      詞彙數（id 0 = "<s>"，1 = "</s>"，其餘依 UTF-8 位元組排序）
  N              u64      unigram 總次數（不含 <s>、</s>）
  eos_total      u64      剪枝前所有 (v, "</s>") 的次數總和
  D              f64      absolute discounting 的折扣
  vocab          V 筆：u16 位元組長度 + UTF-8
  unigram        u64[V]   加權 unigram 次數（<s>、</s> 為 0）
  C              u32      有保留條目的前文數
  ctx_id         u32[C]   依 id 遞增
  ctx_total      u64[C]   該前文剪枝前的總次數 t
  ctx_off        u32[C+1] 條目區間
  E              u32      條目數
  next_id        u32[E]   每個前文內依 id 遞增
  count          u32[E]   加權次數
用法：python3 tools/build_lm.py [--out data/lm/bigram.sjlm] [--check]
"""
import argparse
import hashlib
import os
import pickle
import struct
import sys

WORK = os.environ.get("S2_WORK") or os.path.expanduser("~/.cache/shanjie/work/s2")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPORA = [("counts-200000.pkl", 1), ("counts-colloquial3.pkl", 5)]   # 維基 20 萬篇 ×1、口語（訓練部分＋合成句）×5
PRUNE, D = 2, 0.75


def build():
    uni, bi = {}, {}
    for name, w in CORPORA:
        c = pickle.load(open(os.path.join(WORK, name), "rb"))
        for k, v in c["uni"].items():
            uni[k] = uni.get(k, 0) + v * w
        for k, v in c["bi"].items():
            bi[k] = bi.get(k, 0) + v * w
    words = sorted(uni, key=lambda s: s.encode("utf-8"))
    vocab = ["<s>", "</s>"] + words
    ids = {w: i for i, w in enumerate(vocab)}
    N = sum(uni.values())
    eos_total = sum(c for (v, w), c in bi.items() if w == "</s>")
    total, kept = {}, {}
    for (v, w), c in bi.items():
        total[ids[v]] = total.get(ids[v], 0) + c
        if c >= PRUNE:
            kept.setdefault(ids[v], []).append((ids[w], c))
    out = [b"SJLM0001", struct.pack("<IQQd", len(vocab), N, eos_total, D)]
    for w in vocab:
        b = w.encode("utf-8"); out.append(struct.pack("<H", len(b)) + b)
    out.append(struct.pack(f"<{len(vocab)}Q", *([0, 0] + [uni[w] for w in words])))
    ctx = sorted(kept)
    entries = [e for v in ctx for e in sorted(kept[v])]
    offs, o = [], 0
    for v in ctx:
        offs.append(o); o += len(kept[v])
    offs.append(o)
    out.append(struct.pack("<I", len(ctx)))
    out.append(struct.pack(f"<{len(ctx)}I", *ctx))
    out.append(struct.pack(f"<{len(ctx)}Q", *[total[v] for v in ctx]))
    out.append(struct.pack(f"<{len(offs)}I", *offs))
    out.append(struct.pack("<I", len(entries)))
    out.append(struct.pack(f"<{len(entries)}I", *[e[0] for e in entries]))
    out.append(struct.pack(f"<{len(entries)}I", *[e[1] for e in entries]))
    return b"".join(out), len(vocab), len(ctx), len(entries)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "lm", "bigram.sjlm"))
    ap.add_argument("--check", action="store_true", help="重建並和現有檔比對 SHA-256，不寫檔")
    a = ap.parse_args()
    data, V, C, E = build()
    sha = hashlib.sha256(data).hexdigest()
    print(f"V={V} contexts={C} entries={E} bytes={len(data)} sha256={sha}")
    if a.check:
        old = hashlib.sha256(open(a.out, "rb").read()).hexdigest()
        print("matches" if old == sha else f"DIFFERS (existing {old})")
        sys.exit(0 if old == sha else 1)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    open(a.out + ".tmp", "wb").write(data)
    os.replace(a.out + ".tmp", a.out)


if __name__ == "__main__":
    main()
