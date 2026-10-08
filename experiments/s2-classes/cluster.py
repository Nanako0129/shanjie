"""Brown 式詞類別（exchange 演算法），資料來自 build_lm.py 同一份加權計數（維基 20 萬篇 ×1、口語 ×5）。

目標：類別 bigram 的互資訊 I = Σ_cd M(c,d)·log(M(c,d)/(O(c)·I(d)))（等價於最大化類別 bigram 似然，差一個只和 unigram 類別有關的常數）。
  M 是類別 bigram 計數矩陣，O／I 是列／行邊際。<s>、</s> 各自是固定類別；詞頻排名在 N 之外的詞先歸入一個固定的 RARE 類別。
流程：
  1. 前 N 詞依詞頻由高到低：前 K 個各自成類，其餘每個詞貼進使目標增量最大的類別（貪婪初始化）。
  2. exchange：依詞頻順序逐詞試移到每個類別，增量為正才搬；做 P 輪（或一輪搬動數降到很低就停）。
  3. N 之外的詞：用它和已分類鄰居的 bigram，貼進最佳類別（不移動其他詞）。沒有任何已分類鄰居的詞不給類別（解碼時走原本的回退）。
用法：python3 cluster.py --N 40000 --K 256 --passes 6   （輸出 ~/.cache/shanjie/work/s2-classes/cls-N-K.npz）
"""
import argparse
import json
import os
import pickle
import time

import numpy as np

WORK = os.environ.get("S2K_COUNTS") or os.path.expanduser("~/.cache/shanjie/work/s2")   # S2k：計數目錄可指定（model-v3 的在 188 的 work/s2f4）
OUT = os.environ.get("S2K_OUT") or os.path.expanduser("~/.cache/shanjie/work/s2-classes")
CORPORA = [("counts-200000.pkl", 1), ("counts-colloquial3.pkl", 5)]   # 與 tools/build_lm.py 相同


def prepare():
    """詞彙依 (−uni, 詞) 排序後 id 0..V−1；<s>=V、</s>=V+1。邊 (src,dst,count) 存成 npz。"""
    p = os.path.join(OUT, "edges.npz")
    if os.path.exists(p):
        z = np.load(p, allow_pickle=True)
        return list(z["vocab"]), z["uni"], z["src"], z["dst"], z["cnt"]
    uni, bi = {}, {}
    for name, w in CORPORA:
        c = pickle.load(open(os.path.join(WORK, name), "rb"))
        for k, v in c["uni"].items():
            uni[k] = uni.get(k, 0) + v * w
        for k, v in c["bi"].items():
            bi[k] = bi.get(k, 0) + v * w
        del c
    vocab = sorted(uni, key=lambda s: (-uni[s], s))
    ids = {w: i for i, w in enumerate(vocab)}
    V = len(vocab); ids["<s>"], ids["</s>"] = V, V + 1
    n = len(bi)
    src = np.fromiter((ids[a] for a, b in bi), np.int32, n)
    dst = np.fromiter((ids[b] for a, b in bi), np.int32, n)
    cnt = np.fromiter(bi.values(), np.int64, n)
    u = np.array([uni[w] for w in vocab], np.int64)
    np.savez(p, vocab=np.array(vocab, dtype=object), uni=u, src=src, dst=dst, cnt=cnt)
    return vocab, u, src, dst, cnt


def g(x):
    return x * np.log(np.maximum(x, 1.0))


def csr(key, other, cnt, n):
    o = np.argsort(key, kind="stable")
    ptr = np.zeros(n + 1, np.int64); np.cumsum(np.bincount(key, minlength=n), out=ptr[1:])
    return ptr, other[o], cnt[o].astype(np.float64)


class Clusterer:
    def __init__(self, N, K, src, dst, cnt, V):
        self.N, self.K, self.V = N, K, V
        R = N                      # 節點：0..N−1 前 N 詞，N=RARE，N+1=<s>，N+2=</s>
        node = lambda x: np.where(x < N, x, np.where(x == V, N + 1, np.where(x == V + 1, N + 2, R)))
        s, d = node(src).astype(np.int64), node(dst).astype(np.int64)
        M_ = N + 3
        key = s * M_ + d
        uk, inv = np.unique(key, return_inverse=True)
        c = np.bincount(inv, weights=cnt).astype(np.float64)
        self.es, self.ed, self.ec = (uk // M_).astype(np.int32), (uk % M_).astype(np.int32), c
        self.out_ptr, self.out_nb, self.out_c = csr(self.es, self.ed, self.ec, M_)
        o = np.argsort(self.ed, kind="stable")
        self.in_ptr = np.zeros(M_ + 1, np.int64); np.cumsum(np.bincount(self.ed, minlength=M_), out=self.in_ptr[1:])
        self.in_nb, self.in_c = self.es[o], self.ec[o]
        self.T = self.ec.sum()
        K3 = K + 3
        self.K3 = K3
        self.cls = np.full(M_, -1, np.int32)
        self.cls[N], self.cls[N + 1], self.cls[N + 2] = K, K + 1, K + 2
        self.M = np.zeros((K3, K3)); self.O = np.zeros(K3); self.I = np.zeros(K3)
        sp = np.array([N, N + 1, N + 2])
        m = np.isin(self.es, sp) & np.isin(self.ed, sp)
        for a, b, c_ in zip(self.es[m], self.ed[m], self.ec[m]):
            self.M[self.cls[a], self.cls[b]] += c_
        self.O, self.I = self.M.sum(1), self.M.sum(0)

    def neighbors(self, w):
        lo, hi = self.out_ptr[w], self.out_ptr[w + 1]
        nb, c = self.out_nb[lo:hi], self.out_c[lo:hi]
        s = c[nb == w].sum()
        k = (nb != w)
        cl = self.cls[nb[k]]; ok = cl >= 0
        out = np.bincount(cl[ok], weights=c[k][ok], minlength=self.K3)
        lo, hi = self.in_ptr[w], self.in_ptr[w + 1]
        nb, c = self.in_nb[lo:hi], self.in_c[lo:hi]
        k = (nb != w)
        cl = self.cls[nb[k]]; ok = cl >= 0
        inn = np.bincount(cl[ok], weights=c[k][ok], minlength=self.K3)
        return out, inn, s

    def apply(self, a, out, inn, s, sign):
        self.M[a, :] += sign * out; self.M[:, a] += sign * inn; self.M[a, a] += sign * s
        self.O += sign * inn; self.I += sign * out   # x→w 的邊讓 x 所在類別的列邊際增加，w→x 同理
        self.O[a] += sign * (out.sum() + s); self.I[a] += sign * (inn.sum() + s)

    def scores(self, out, inn, s):
        K, M = self.K, self.M
        Si, Sj = np.nonzero(out)[0], np.nonzero(inn)[0]
        sc = np.zeros(K)
        if len(Si):
            B = M[:K][:, Si]
            T = g(B + out[Si]) - g(B)
            sc += T.sum(1)
            for j, d in enumerate(Si):
                if d < K: sc[d] -= T[d, j]
        if len(Sj):
            B = M[Sj][:, :K]
            T = g(B + inn[Sj][:, None]) - g(B)
            sc += T.sum(0)
            for j, c in enumerate(Sj):
                if c < K: sc[c] -= T[j, c]
        dg = np.diag(M)[:K]
        sc += g(dg + out[:K] + inn[:K] + s) - g(dg)
        o0, i0 = self.O[:K] + inn[:K], self.I[:K] + out[:K]
        sc -= g(o0 + out.sum() + s) - g(o0)
        sc -= g(i0 + inn.sum() + s) - g(i0)
        return sc

    def objective(self):
        """每詞次的類別 bigram 互資訊（nats）= (Σg(M) − Σg(O) − Σg(I))/T + log T。"""
        return (g(self.M).sum() - g(self.O).sum() - g(self.I).sum()) / self.T + np.log(self.T)

    def init(self, log):
        N, K = self.N, self.K
        t = time.time()
        for w in range(N):
            out, inn, s = self.neighbors(w)
            if w < K:
                b = w
            else:
                b = int(np.argmax(self.scores(out, inn, s)))
            self.cls[w] = b
            self.apply(b, out, inn, s, +1)
            if w % 5000 == 0:
                log(f"init {w}/{N} {time.time() - t:.0f}s")
        log(f"init done MI={self.objective():.5f}")

    def exchange(self, log):
        moved = 0
        for w in range(self.N):
            a = self.cls[w]
            out, inn, s = self.neighbors(w)
            self.apply(a, out, inn, s, -1)
            sc = self.scores(out, inn, s)
            b = int(np.argmax(sc))
            if sc[b] <= sc[a] + 1e-9:
                b = a
            else:
                moved += 1
            self.cls[w] = b
            self.apply(b, out, inn, s, +1)
        return moved


def assign_rare(cl, vocab_n, src, dst, cnt, V, N):
    """N 之外的詞：用和已分類詞（前 N 詞）的 bigram 貼進最佳類別。回傳長度 V 的類別陣列（−1＝無）。"""
    full = np.full(V + 2, -1, np.int32)
    full[:N] = cl.cls[:N]; full[V], full[V + 1] = cl.K + 1, cl.K + 2
    ok = np.zeros(V + 2, bool); ok[:N] = True; ok[V:] = True
    so = np.argsort(src, kind="stable"); ptr_o = np.zeros(V + 3, np.int64); np.cumsum(np.bincount(src, minlength=V + 2), out=ptr_o[1:])
    do = np.argsort(dst, kind="stable"); ptr_i = np.zeros(V + 3, np.int64); np.cumsum(np.bincount(dst, minlength=V + 2), out=ptr_i[1:])
    K3 = cl.K3
    res = full.copy()
    for w in range(N, V):
        i = so[ptr_o[w]:ptr_o[w + 1]]; j = do[ptr_i[w]:ptr_i[w + 1]]
        i = i[(dst[i] != w) & ok[dst[i]]]; j = j[(src[j] != w) & ok[src[j]]]
        if len(i) + len(j) == 0:
            continue
        out = np.bincount(full[dst[i]], weights=cnt[i], minlength=K3).astype(np.float64)
        inn = np.bincount(full[src[j]], weights=cnt[j], minlength=K3).astype(np.float64)
        res[w] = int(np.argmax(cl.scores(out, inn, 0.0)))
    return res[:V]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--N", type=int, default=40000); ap.add_argument("--K", type=int, default=256)
    ap.add_argument("--passes", type=int, default=6)
    a = ap.parse_args()
    t0 = time.time()
    trace = []
    def log(m):
        print(m, flush=True); trace.append(m)
    vocab, uni, src, dst, cnt = prepare()
    V = len(vocab)
    log(f"loaded V={V} edges={len(src)} {time.time() - t0:.0f}s")
    cl = Clusterer(a.N, a.K, src, dst, cnt, V)
    cl.init(log)
    mis = [cl.objective()]
    for p in range(a.passes):
        t = time.time(); mv = cl.exchange(log); mis.append(cl.objective())
        log(f"pass {p + 1}: moved {mv} MI={mis[-1]:.5f} {time.time() - t:.0f}s")
        if mv < a.N * 0.002:
            break
    res = assign_rare(cl, V, src, dst, cnt, V, a.N)
    log(f"rare assigned: {int((res[a.N:] >= 0).sum())}/{V - a.N} total {time.time() - t0:.0f}s")
    np.savez(os.path.join(OUT, f"cls-{a.N}-{a.K}.npz"), cls=res, K=a.K, N=a.N, mi=np.array(mis))
    json.dump(trace, open(os.path.join(OUT, f"cls-{a.N}-{a.K}.log.json"), "w"))


if __name__ == "__main__":
    main()
