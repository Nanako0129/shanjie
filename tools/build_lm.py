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
LEXDIR = os.path.join(ROOT, "data", "lexicon")


def variant_classes(by_reading):
    """S2f §2.4 與修訂一：MERGE（build_counts.MERGE）套到兩邊後字串相同、而且在詞庫裡的讀音集合完全相同的詞併成一類
    （占 另有 ㄓㄢ，就不和 佔 併）。by_reading：讀音 → [(詞, 分數)]（ime.Lexicon.by_reading，基底加疊加層）。
    回傳 詞 → 類的成員 tuple，第一個是代表成員（詞庫最高分最大、再來是 MERGE 正規形、再來 code point 最小），只含成員數 >= 2 的類。"""
    import build_counts as bc
    fold = lambda w: "".join(bc.MERGE.get(c, c) for c in w)
    reads, best = {}, {}
    for r, ents in by_reading.items():
        for w, lp in ents:
            reads.setdefault(w, set()).add(r)
            best[w] = max(best.get(w, lp), lp)
    groups = {}
    for w, rs in reads.items():
        groups.setdefault((fold(w), frozenset(rs)), []).append(w)
    out = {}
    for g in groups.values():
        if len(g) > 1:
            rep = min(g, key=lambda w: (-best[w], fold(w) != w, w))
            out.update(dict.fromkeys(g, (rep,) + tuple(sorted(x for x in g if x != rep))))
    return out


def merge_variants(uni, bi, cls):
    """各語料加權、四捨五入之後做（uni、bi 是整數 dict）。unigram 以類加總後寫回每個成員（每個寫法都要在詞彙表裡、能當前文）；
    二元組以（類, 類）加總，「後一個詞」只放在代表成員上，前文則寫給類裡每個成員（條目相同）。這樣每個前文的總數 t 只算類一次
    （修訂一 6.2.3；寫回每個成員會把 k 個成員的類算 k 次）。"""
    rep = lambda w: cls[w][0] if w in cls else w
    u, b = {}, {}
    for w in [w for w in uni if w in cls]:
        u[rep(w)] = u.get(rep(w), 0) + uni.pop(w)
    for k in [k for k in bi if k[0] in cls or k[1] in cls]:
        r = (rep(k[0]), rep(k[1]))
        b[r] = b.get(r, 0) + bi.pop(k)
    for w, c in u.items():
        uni.update(dict.fromkeys(cls[w], c))
    for (v, w), c in b.items():
        bi.update(dict.fromkeys(((mv, w) for mv in cls.get(v, (v,))), c))
    return uni, bi


def merged_counts(cls=None):
    """計數階段（加權、四捨五入、去 0、異體合併）。回傳 (uni, bi, cls)；build() 與 tools/kn_cont.py 共用。"""
    uni, bi = {}, {}
    for name, w in CORPORA:
        c = pickle.load(open(os.path.join(WORK, name), "rb"))
        for k, v in c["uni"].items():
            uni[k] = uni.get(k, 0) + v * w
        for k, v in c["bi"].items():
            bi[k] = bi.get(k, 0) + v * w
    # S2n 契約 §6.2：期望次數是小數；加權相加後每一筆四捨五入成整數、去掉 0。整數輸入不受影響（round(int) 不變）。
    uni = {k: r for k, v in uni.items() if (r := round(v))}
    bi = {k: r for k, v in bi.items() if (r := round(v))}
    if cls is None:
        sys.path.insert(0, os.path.join(ROOT, "reference", "proto"))
        sys.path.insert(0, os.path.join(ROOT, "experiments", "s2"))
        import ime
        lex = ime.Lexicon(os.path.join(LEXDIR, "mcbpmf-data.txt"), overlay=ime.OVERLAYS)
        cls = variant_classes(lex.by_reading)
    uni, bi = merge_variants(uni, bi, cls)
    return uni, bi, cls


def build(cls=None):
    uni, bi, cls = merged_counts(cls)
    words = sorted(uni, key=lambda s: s.encode("utf-8"))
    vocab = ["<s>", "</s>"] + words
    ids = {w: i for i, w in enumerate(vocab)}
    # S2f 修訂二 7.2.2：類只算一次（只算代表成員的 unigram 與代表成員前文的 </s>）；影響約 0.006 log10
    once = lambda w: w not in cls or cls[w][0] == w
    N = sum(c for w, c in uni.items() if once(w))
    eos_total = sum(c for (v, w), c in bi.items() if w == "</s>" and once(v))
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
