"""自我檢查：exchange 的預測增量要等於重算整個目標的實際變化（N=1500, K=32）。"""
import numpy as np, cluster as C
vocab, uni, src, dst, cnt = C.prepare()
cl = C.Clusterer(1500, 32, src, dst, cnt, len(vocab)); cl.init(lambda m: None)
rng = np.random.default_rng(0)
for w in rng.choice(1500, 40, replace=False):
    a = cl.cls[w]; out, inn, s = cl.neighbors(w)
    cl.apply(a, out, inn, s, -1); sc = cl.scores(out, inn, s)
    base = cl.objective() * cl.T          # 已移除 w 的目標（單位 nats·T）
    for b in rng.choice(32, 3, replace=False):
        cl.apply(b, out, inn, s, +1); actual = cl.objective() * cl.T - base; cl.apply(b, out, inn, s, -1)
        pred = sc[b] - sc[a] + (cl.objective() * 0)   # 差值比較：actual(b)-actual(a)
        cl.apply(a, out, inn, s, +1); ra = cl.objective() * cl.T - base; cl.apply(a, out, inn, s, -1)
        assert abs((actual - ra) - (sc[b] - sc[a])) < 1e-6 * max(1, abs(actual)), (w, b, actual - ra, sc[b] - sc[a])
    cl.apply(a, out, inn, s, +1)
print("delta ok")
