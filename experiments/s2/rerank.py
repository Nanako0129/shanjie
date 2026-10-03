"""S2 實驗：前 64 名的判別式重排（平均感知器）。review-fable.md 的改動 3。

流程：
  1. prep：把句子轉讀音（基底＋疊加層的 to_syllables）、用參考 LM（reference/proto/lm.py）解碼，存每句的前 64 名與特徵。
  2. train：平均感知器。權重初始為「只看原分數」，所以第 0 輪等於現行排序；第一名不是正解而正解在候選裡時，
     w += φ(正解) − φ(第一名)。
  3. eval：用學到的權重重排各調整集的前 64 名。
訓練句必須是 LM 沒看過的：針對混淆字合成的句子（沒進計數）與維基第 205,000 篇以後的條目。dev302 不參與。
特徵：
  稠密：路徑總分、詞庫分數和、LM log10 機率和、詞數、單字詞數、疊加層詞數。
  稀疏：每個單字詞 w 的 U|w、L|前一詞|w、R|w|後一詞（句首句尾以 <s>、</s> 表示）。
  字對（--pairs K，第 12 輪）：距離 1..K 的兩個字 P{d}|a|b。所有候選字數相同（同一串音節），
     兩個候選的分數差只來自「碰到兩者不同位置」的字對，所以只算這些，不必展開整句。
用法：python3 experiments/s2/rerank.py prep <名稱> <句子檔或 rows 檔> [--limit N] [--profile chat]
      python3 experiments/s2/rerank.py train <名稱>... --out <模型>
      python3 experiments/s2/rerank.py eval <模型> <名稱>...
"""
import argparse
import collections
import math
import os
import pickle
import random
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "reference", "proto"))
import ime  # noqa: E402
from eval import lenient  # noqa: E402  單字對照＋異體詞表（docs/PLAN.md §S2v）
import lm as L  # noqa: E402

WORK = os.path.expanduser("~/.cache/shanjie/work/s2/rerank")
HAN = re.compile(r"[一-鿿]{4,30}")
DENSE = ["total", "lex", "lm", "nw", "n1", "nov"]


def setup():
    lm = L.BigramLM(os.path.join(ROOT, "data", "lm", "bigram.sjlm"))
    base = ime.Lexicon(os.path.join(ROOT, "data", "lexicon", "mcbpmf-data.txt"),
                       overlay=os.path.join(ROOT, "data", "lexicon", "overlay-add.tsv"))
    ov = {l.split("\t")[1] for l in open(os.path.join(ROOT, "data", "lexicon", "overlay-add.tsv"), encoding="utf-8")}
    return lm, L.cap_overlay(base, ov, lm), ov


def features(lex, lm, lam, ov, syls, total, ws):
    """回傳 (稠密 dict, 稀疏 list)。詞的讀音由詞長從 syls 依序切出。"""
    lexsum = lmsum = 0.0
    pos, prev, sparse = 0, "<s>", []
    for i, w in enumerate(ws):
        key = tuple(syls[pos:pos + len(w)]); pos += len(w)
        lp = dict(lex.by_reading[key])[w]
        lexsum += lp
        lmsum += math.log10(lm.prob(prev, w, 10 ** lp))
        if len(w) == 1:
            nxt = ws[i + 1] if i + 1 < len(ws) else "</s>"
            sparse += [f"U|{w}", f"L|{prev}|{w}", f"R|{w}|{nxt}"]
        prev = w
    dense = {"total": total, "lex": lexsum, "lm": lmsum, "nw": len(ws), "n1": sum(len(w) == 1 for w in ws),
             "nov": sum(w in ov for w in ws)}
    return dense, sparse


def prep(name, path, limit, profile):
    lm, lex, ov = setup()
    lam = L.PROFILES[profile]
    rows = []
    if path.endswith(".txt") and "|" in open(path, encoding="utf-8").readline():
        for line in open(path, encoding="utf-8"):
            p = line.rstrip("\n").split("|")
            if len(p) == 3:
                rows.append((p[1], p[2].split()))
    else:
        rnd = random.Random(7)
        sents = [s for line in open(path, encoding="utf-8") for s in HAN.findall(line)]
        rnd.shuffle(sents)
        for s in sents:
            syls = lex.to_syllables(s)
            if syls:
                rows.append((s, list(syls)))
            if limit and len(rows) >= limit:
                break
    data = []
    for t, syls in rows[:limit] if limit else rows:
        nb = L.decode(lex, syls, lm, profile)
        cands = [("".join(ws), features(lex, lm, lam, ov, syls, sc, ws)) for sc, ws in nb]
        data.append((t, cands))
    os.makedirs(WORK, exist_ok=True)
    pickle.dump(data, open(os.path.join(WORK, f"{name}.pkl"), "wb"))
    ok = sum(lenient(c[0][0]) == lenient(t) for t, c in data)
    print(f"{name}: {len(data)} 句，現行第一名正確 {ok}")


def score(w, f):
    d, sp = f
    return sum(w.get(k, 0.0) * d[k] for k in DENSE) + sum(w.get(k, 0.0) for k in sp)


def pairs(s, pos, K):
    """碰到 pos 中任一位置、距離 1..K 的字對特徵（同一對只算一次）。"""
    out = set()
    for i in pos:
        for d in range(1, K + 1):
            if i + d < len(s):
                out.add((i, d))
            if i - d >= 0:
                out.add((i - d, d))
    return [f"P{d}|{s[i]}|{s[i + d]}" for i, d in out]


def diff_pos(a, b):
    return [i for i, (x, y) in enumerate(zip(a, b)) if x != y]


def best_of(w, cands, K):
    """最高分的候選。K>0 時字對分數相對第一名計算（共同的字對互相抵銷）。"""
    if not K:
        return max(cands, key=lambda x: score(w, x[1]))
    ref = cands[0][0]
    def sc(c):
        p = diff_pos(c[0], ref)
        return score(w, c[1]) + sum(w.get(k, 0.0) for k in pairs(c[0], p, K)) - sum(w.get(k, 0.0) for k in pairs(ref, p, K))
    return max(cands, key=sc)


def train(names, out, epochs=3, K=0):
    data = [x for n in names for x in pickle.load(open(os.path.join(WORK, f"{n}.pkl"), "rb"))]
    w, acc, c = collections.defaultdict(float, {"total": 1.0}), collections.defaultdict(float), 1
    for ep in range(epochs):
        random.Random(ep).shuffle(data)
        upd = 0
        for t, cands in data:
            tt = lenient(t)
            gold_s, gold = next(((s, f) for s, f in cands if lenient(s) == tt), (None, None))
            if gold is None:
                continue
            best = best_of(w, cands, K)
            if lenient(best[0]) == tt:
                c += 1; continue
            if K:
                p = diff_pos(gold_s, best[0])
                for k in pairs(gold_s, p, K):
                    w[k] += 1; acc[k] += c
                for k in pairs(best[0], p, K):
                    w[k] -= 1; acc[k] -= c
            for k in DENSE:
                d = gold[0][k] - best[1][0][k]
                w[k] += d; acc[k] += c * d
            for k in gold[1]:
                w[k] += 1; acc[k] += c
            for k in best[1][1]:
                w[k] -= 1; acc[k] -= c
            upd += 1; c += 1
        print(f"epoch {ep}: {upd} updates", flush=True)
    avg = {k: w[k] - acc[k] / c for k in set(w) | set(acc)}
    avg["__pairs__"] = K
    pickle.dump(avg, open(out, "wb"))
    print(f"features {len(avg)}; dense", {k: round(avg.get(k, 0), 4) for k in DENSE})


def evaluate(model, names):
    w = pickle.load(open(model, "rb"))
    K = w.pop("__pairs__", 0)
    for n in names:
        data = pickle.load(open(os.path.join(WORK, f"{n}.pkl"), "rb"))
        base = sum(lenient(c[0][0]) == lenient(t) for t, c in data)
        new = sum(lenient(best_of(w, c, K)[0]) == lenient(t) for t, c in data)
        print(f"{n}: {len(data)} 句  原排序 {base}  重排 {new}  ({new - base:+d})")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prep"); p.add_argument("name"); p.add_argument("path"); p.add_argument("--limit", type=int)
    p.add_argument("--profile", default="chat")
    t = sub.add_parser("train"); t.add_argument("names", nargs="+"); t.add_argument("--out", required=True)
    t.add_argument("--epochs", type=int, default=3)
    t.add_argument("--pairs", type=int, default=0, help="字對特徵的最大距離（0＝不用）")
    e = sub.add_parser("eval"); e.add_argument("model"); e.add_argument("names", nargs="+")
    a = ap.parse_args()
    if a.cmd == "prep":
        prep(a.name, a.path, a.limit, a.profile)
    elif a.cmd == "train":
        train(a.names, a.out, a.epochs, a.pairs)
    else:
        evaluate(a.model, a.names)


if __name__ == "__main__":
    main()
