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
import os
import re
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


DEMOTE_TSV = os.path.join(ime._LEXDIR, "demote.tsv")


def cap_overlay(lex, overlay_words, lm, demote_path=DEMOTE_TSV):
    """疊加層詞的分數取 min(lp, log10(c/N))；語料沒見過的扣 UNSEEN_OVERLAY_PENALTY。回傳新的 Lexicon。
    回傳的詞庫帶著降權表 `demote`（預設從 data/lexicon/demote.tsv 載入並對照詞庫，decode 預設套用；
    demote_path=None 是沒有表，只給用自訂小詞庫的測試）。"""
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
    out.demote = load_demote(demote_path, out) if demote_path else {}
    return out


_HAN = ((0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF), (0x20000, 0x2A6DF), (0x2A700, 0x2EBEF),
        (0x30000, 0x3134F), (0x2F800, 0x2FA1F), (0x3007, 0x3007))


def context_key(prefix):
    """core/src/learn.rs context_key：尾端連續漢字、最多 2 字；沒有就是 ""（Rust 回傳哨兵，這裡用空字串）。"""
    tail = []
    for c in reversed(prefix):
        if not any(a <= ord(c) <= b for a, b in _HAN) or len(tail) == 2:
            break
        tail.append(c)
    return "".join(reversed(tail))


def history(left, lm):
    """S2h §1：前文 left（context_key 的結果）決定第一個詞的歷史詞。依序試整段、最後 1 字，
    第一個在模型裡有 bigram 歷史紀錄的就是；都沒有（或 left 為空）是 "<s>"。"""
    for x in (left, left[-1:]):
        if x and lm.ctx.get(lm.ids.get(x, -1)) is not None:
            return x
    return "<s>"


_EDGE = (" ", "\t", "\r", "\x0b", "\x0c", "\u00a0")  # 核心 DEMOTE_EDGE：欄位頭尾不可有這些
_DECIMAL = re.compile(r"[0-9]+(\.[0-9]+)?\Z", re.ASCII)  # 核心 plain_decimal：float() 會收的 "2_0"、"١" 之類都不收


def parse_demote(text):
    """降權表（docs/contracts/sw-sensitive-demote.md §2）的格式：回傳 [(讀音或 "*", 詞, δ)]。規則和核心的
    Demote::parse 一樣嚴格：5 個非空欄位、欄位頭尾沒有空白、δ 是 [0-9]+ 或 [0-9]+.[0-9]+ 且有限、為正，
    同一個（讀音, 詞）不可重複。行以 "\n" 結束（前面可有一個 "\r"，最後一行沒有 "\n" 時不剝）；
    空行與 # 行略過。違反就丟 ValueError。"""
    lines = text.split("\n")
    rows, seen = [], set()
    for i, line in enumerate(lines):
        if i < len(lines) - 1 and line.endswith("\r"):
            line = line[:-1]
        if not line or line[0] == "#":
            continue
        f = line.split("\t")
        if len(f) != 5 or any(not x or x.startswith(_EDGE) or x.endswith(_EDGE) for x in f):
            raise ValueError("bad demote row")
        reading, word, delta = f[0], f[1], f[2]
        if not _DECIMAL.match(delta):
            raise ValueError("bad demote row")
        d = float(delta)
        if not (math.isfinite(d) and d > 0) or (reading, word) in seen:
            raise ValueError("bad demote row")
        seen.add((reading, word))
        rows.append((reading, word, d))
    return rows


def load_demote(path, lex):
    """讀降權表並對照詞庫 lex（ime.Lexicon 或 cap_overlay 的結果）：{(讀音, 詞): δ}，讀音以 "-" 相接，
    "*" 是所有讀音。除了格式錯，某一列在詞庫裡找不到這個讀音下的這個詞（"*" 是找不到這個詞）也丟 ValueError
    （核心同樣讓引擎建立失敗）。"""
    with open(path, encoding="utf-8", newline="") as f:
        rows = parse_demote(f.read())
    for reading, word, _ in rows:
        if reading == "*":
            ok = word in lex.by_word
        else:
            ok = any(w == word for w, _ in lex.by_reading.get(tuple(reading.split("-")), ()))
        if not ok:
            raise ValueError("demote row matches no lexicon entry")
    return {(r, w): d for r, w, d in rows}


def demote_delta(demote, reading, word):
    """特定讀音優先於 "*"；沒有就 0.0。demote 為 None 或空表時永遠是 0.0。"""
    if not demote:
        return 0.0
    d = demote.get((reading, word))
    return d if d is not None else demote.get(("*", word), 0.0)


def decode(lex, syls, lm, profile, beam=64, start="<s>", demote=True):
    """demote：是否套用 lex.demote（cap_overlay 從 data/lexicon/demote.tsv 載入，預設開，和核心的 decode 一樣）；
    False 就逐位元等於沒有這張表。詞項分數 = lm.word(...) − δ；要試哪些詞條仍依原本的分數。"""
    table = getattr(lex, "demote", None) if demote else None
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
            reading = "-".join(key) if table else None
            for word, lp in entries[:ime.PER_KEY]:
                d = demote_delta(table, reading, word)
                for s, ws in hyps[i - L]:
                    sc = s + (lm.word(lam, ws[-1] if ws else start, word, lp) - d)
                    surface = "".join(ws) + word
                    if surface not in cand or sc > cand[surface][0]:
                        cand[surface] = (sc, ws + (word,))
        hyps[i] = sorted(cand.values(), key=lambda x: -x[0])[:beam]
    out = [(s + lm.eos(lam, ws[-1] if ws else start), ws) for s, ws in hyps[n]]
    return sorted(out, key=lambda x: -x[0])
