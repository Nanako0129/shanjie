"""詞類別回退：只在詞 bigram 未見（BigramLM.prob 的回退分支）時，
back·pb 改成 back·[(1−μ)·pb + μ·P(c(w)|c(v))·P(w|c(w))]；有保留條目的 bigram 完全不動。

P(d|c) = (M[c,d]+ε)/(O[c]+K'ε)，M 是全部（剪枝前）加權 bigram 的類別計數；P(w|c) = uni(w)/Σ_{x∈c} uni(x)。
<s>、</s> 各自是類別，</s> 的發射機率為 1。任一邊沒有類別的詞（詞庫有、語料沒見過）就不混入，維持原回退。
詞庫分數、λ、解碼（beam、PER_KEY、疊加層、cap_overlay）都沿用 reference/proto/lm.py。
"""
import glob
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "reference", "proto"))
import ime  # noqa: E402
import lm as L  # noqa: E402
from eval import lenient  # noqa: E402

OUT = os.environ.get("S2K_OUT") or os.path.expanduser("~/.cache/shanjie/work/s2-classes")
EPS = 0.1
# data/lm/ 被 git 忽略；這份由 tools/build_lm.py --out 重建，和 main 的 data/lm/bigram.sjlm 逐位元組相同（README 有 cmp 結果）
LM_PATH = os.path.join(OUT, "bigram.sjlm")


class ClassLM(L.BigramLM):
    def __init__(self, path, N=40000, K=256, mu=0.0):
        super().__init__(path)
        self.mu = mu
        if mu:
            self._load(N, K)

    def _load(self, N, K):
        z = np.load(os.path.join(OUT, "edges.npz"), allow_pickle=True)
        vocab, uni, src, dst, cnt = list(z["vocab"]), z["uni"], z["src"], z["dst"], z["cnt"]
        V = len(vocab)
        cls = np.concatenate([np.load(os.path.join(OUT, f"cls-{N}-{K}.npz"))["cls"], [K + 1, K + 2]])
        K3 = K + 3
        ok = (cls[src] >= 0) & (cls[dst] >= 0)
        M = np.bincount(cls[src[ok]].astype(np.int64) * K3 + cls[dst[ok]], weights=cnt[ok], minlength=K3 * K3).reshape(K3, K3)
        self.Pc = (M + EPS) / (M.sum(1, keepdims=True) + K3 * EPS)
        cu = np.bincount(cls[:V][cls[:V] >= 0], weights=uni[cls[:V] >= 0], minlength=K3)
        self.cls, self.emit = {"<s>": K + 1, "</s>": K + 2}, {"</s>": 1.0, "<s>": 1.0}
        for i, w in enumerate(vocab):
            if cls[i] >= 0:
                self.cls[w] = int(cls[i]); self.emit[w] = uni[i] / cu[cls[i]]

    def prob(self, v, w, pb):
        c = self.ctx.get(self.ids.get(v, -1))
        if c is None:
            back, direct = 1.0, 0.0
        else:
            t, back, entries = c
            k = entries.get(self.ids.get(w, -1), 0)
            if k:
                return (k - self.D) / t + back * pb
        cv, cw = self.cls.get(v) if self.mu else None, self.cls.get(w) if self.mu else None
        if cv is None or cw is None:
            return back * pb
        return back * ((1 - self.mu) * pb + self.mu * self.Pc[cv, cw] * self.emit[w])


def make_lex(lm):
    base = ime.Lexicon(os.path.join(ROOT, "data", "lexicon", "mcbpmf-data.txt"), overlay=ime.OVERLAYS)
    ov = {l.split("\t")[1] for l in open(os.path.join(ROOT, "data", "lexicon", "overlay-add.tsv"), encoding="utf-8")}
    return L.cap_overlay(base, ov, lm)


def rows_of(paths):
    """(正解, 讀音, 前文)。S2k §1.1：保留前文，和 lm_eval.py 的 rows_of 相同。"""
    out = []
    for f in paths:
        for line in open(f, encoding="utf-8"):
            p = line.rstrip("\n").split("|")
            if len(p) == 3:
                out.append((p[1], p[2].split(), p[0]))
    return out


def dev302():
    return rows_of(sorted(glob.glob(os.path.join(ROOT, "eval", "dev", "*.txt"))))[:302]


def run(lex, lm, rows, profile):
    """每列第一名 surface（不套寬鬆）。S2k §1.1：一律用前文解碼，和 lm_eval.py --context 相同。"""
    return ["".join(L.decode(lex, s, lm, profile, start=L.history(L.context_key(ctx), lm))[0][1]) for _, s, ctx in rows]


def ok_of(rows, tops):
    return [lenient(r[0]) == lenient(p) for r, p in zip(rows, tops)]


def mcnemar(b, c):
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)


def paired(base_ok, ok):
    fixed = sum(1 for p, q in zip(base_ok, ok) if not p and q)
    broke = sum(1 for p, q in zip(base_ok, ok) if p and not q)
    return fixed, broke, fixed - broke, mcnemar(fixed, broke)
