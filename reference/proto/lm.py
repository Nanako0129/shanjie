"""S2 參考實作：讀 tools/build_lm.py 產生的 bigram LM 檔，提供計分與帶 LM 的解碼。Rust 核心必須和它逐分數一致。

計分（profile 只差在 λ：chat 0.5、formal 0.7）：
  詞分數 = λ·log10 P(w | v) + (1 − λ)·lp，lp 是詞庫分數（疊加層詞先套上限，見 cap_overlay）
  P(w | v) = (c(v,w) − D) / t(v) + back(v) · 10^lp   若 v 有保留條目（c 為保留條目的次數，沒保留就是 0 項）
           = 10^lp                                  若 v 沒有保留條目
  back(v)  = 1 − Σ_保留條目 (c − D) / t(v)，Σ 依檔案中 next_id 遞增的順序逐項累加（浮點順序固定）
  句尾     = λ·log10 P(</s> | 最後一詞)，回退分布用 p_eos = eos_total / N
解碼和 ime.decode 相同（每個位置保留 beam 個、依分數穩定排序、同一 surface 只在分數嚴格較高時取代），
v 是前一個詞（句首為 "<s>"）；走完後每條路徑加句尾項再穩定排序一次。
"""
import collections
import math
import struct

import ime

PROFILES = {"chat": 0.5, "formal": 0.7}
UNSEEN_OVERLAY_PENALTY = 1.0


class BigramLM:
    def __init__(self, path):
        b = open(path, "rb").read()
        assert b[:8] == b"SJLM0001", "bad magic"
        V, self.N, self.eos_total, self.D = struct.unpack_from("<IQQd", b, 8)
        p = 8 + struct.calcsize("<IQQd")
        self.vocab = []
        for _ in range(V):
            (n,) = struct.unpack_from("<H", b, p); p += 2
            self.vocab.append(b[p:p + n].decode("utf-8")); p += n
        self.ids = {w: i for i, w in enumerate(self.vocab)}
        self.uni = struct.unpack_from(f"<{V}Q", b, p); p += 8 * V
        (C,) = struct.unpack_from("<I", b, p); p += 4
        ctx = struct.unpack_from(f"<{C}I", b, p); p += 4 * C
        tot = struct.unpack_from(f"<{C}Q", b, p); p += 8 * C
        off = struct.unpack_from(f"<{C + 1}I", b, p); p += 4 * (C + 1)
        (E,) = struct.unpack_from("<I", b, p); p += 4
        nxt = struct.unpack_from(f"<{E}I", b, p); p += 4 * E
        cnt = struct.unpack_from(f"<{E}I", b, p); p += 4 * E
        assert p == len(b), "trailing bytes"
        self.p_eos = self.eos_total / self.N
        self.ctx = {}   # v_id -> (t, back, {w_id: c})
        for i, v in enumerate(ctx):
            t, kept_sum, entries = tot[i], 0.0, {}
            for j in range(off[i], off[i + 1]):
                entries[nxt[j]] = cnt[j]
                kept_sum += cnt[j] - self.D
            self.ctx[v] = (t, 1.0 - kept_sum / t, entries)

    def prob(self, v, w, pb):
        c = self.ctx.get(self.ids.get(v, -1))
        if c is None:
            return pb
        t, back, entries = c
        k = entries.get(self.ids.get(w, -1), 0)
        return (k - self.D) / t + back * pb if k else back * pb

    def word(self, lam, v, w, lp):
        return lam * math.log10(self.prob(v, w, 10 ** lp)) + (1 - lam) * lp

    def eos(self, lam, v):
        return lam * math.log10(self.prob(v, "</s>", self.p_eos))

    def count(self, w):
        i = self.ids.get(w)
        return self.uni[i] if i is not None else 0


def cap_overlay(lex, overlay_words, lm):
    """疊加層詞的分數取 min(lp, log10(c/N))；語料沒見過的扣 UNSEEN_OVERLAY_PENALTY。回傳新的 Lexicon。"""
    out = ime.Lexicon.__new__(ime.Lexicon)
    out.by_reading, out.by_word, out.max_len = collections.defaultdict(list), {}, lex.max_len
    for key, entries in lex.by_reading.items():
        for w, lp in entries:
            if w in overlay_words:
                c = lm.count(w)
                lp = min(lp, math.log10(c / lm.N)) if c else lp - UNSEEN_OVERLAY_PENALTY
            out.by_reading[key].append((w, lp))
            if w not in out.by_word or lp > out.by_word[w][1]:
                out.by_word[w] = (key, lp)
    for k in out.by_reading:
        out.by_reading[k].sort(key=lambda x: -x[1])
    return out


def decode(lex, syls, lm, profile, beam=64):
    lam = PROFILES[profile]
    syls = tuple(syls); n = len(syls)
    hyps = [[] for _ in range(n + 1)]; hyps[0] = [(0.0, ())]
    for i in range(1, n + 1):
        cand = {}
        for L in range(1, min(lex.max_len, i) + 1):
            key = syls[i - L:i]
            entries = lex.by_reading.get(key)
            if not entries or not hyps[i - L]:
                continue
            for word, lp in entries[:ime.PER_KEY]:
                for s, ws in hyps[i - L]:
                    sc = s + lm.word(lam, ws[-1] if ws else "<s>", word, lp)
                    surface = "".join(ws) + word
                    if surface not in cand or sc > cand[surface][0]:
                        cand[surface] = (sc, ws + (word,))
        hyps[i] = sorted(cand.values(), key=lambda x: -x[0])[:beam]
    out = [(s + lm.eos(lam, ws[-1] if ws else "<s>"), ws) for s, ws in hyps[n]]
    return sorted(out, key=lambda x: -x[0])
