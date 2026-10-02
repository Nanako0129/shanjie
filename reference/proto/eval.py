"""量測：unigram lattice vs. LLM 重排，以及學習策略的副作用。

用法：
  .venv/bin/python eval.py                      # 只跑 unigram 與學習模擬
  .venv/bin/python eval.py Qwen/Qwen3-0.6B-Base  # 再加上一或多個模型的重排
"""
import json
import os
import random
import re
import sys
import time

from ime import (BEAM, ContextKeyed, GlobalBoost, Lexicon, NoLearning, Promotion,
                 decode, make_scorer, rerank, segment_words)

HERE = os.path.dirname(__file__)
MOEDICT = os.path.join(HERE, "..", "repos", "moedict-data", "dict-revised_bkup.json")
SAMPLE = os.path.join(HERE, "tests", "moedict_sample.txt")
CJK = re.compile(r"^[一-鿿]+$")
# 寬鬆比對：上下文本來就判斷不了的人稱、以及兩種寫法都算對的異體字
LENIENT = str.maketrans("她妳它牠嘗周臺裏", "他你他他嚐週台裡")


def load_rows(name):
    rows = []
    for line in open(os.path.join(HERE, "tests", name), encoding="utf-8"):
        line = line.rstrip("\n")
        if line and not line.startswith("#"):
            ctx, sent = line.split("|", 1)
            rows.append((ctx, sent))
    return rows


def load_moedict(lex, n=300, seed=0):
    """萌典例句（「如：「…」」裡的現代用例），切到標點為止，取 5–20 字。結果快取成檔以便重現。"""
    if os.path.exists(SAMPLE):
        return [("", s) for s in open(SAMPLE, encoding="utf-8").read().split()]
    segs = set()
    for e in json.load(open(MOEDICT, encoding="utf-8")):
        for h in e.get("heteronyms", []):
            for d in h.get("definitions", []):
                for ex in d.get("example", []):
                    for q in re.findall(r"「([^」]+)」", ex):
                        for seg in re.split(r"[，。、；：？！『』（）…—\s]", q):
                            if 5 <= len(seg) <= 20 and CJK.match(seg) and lex.to_syllables(seg):
                                segs.add(seg)
    pick = random.Random(seed).sample(sorted(segs), n)
    open(SAMPLE, "w", encoding="utf-8").write("\n".join(pick) + "\n")
    return [("", s) for s in pick]


def evaluate(lex, rows, lm=None):
    sent_ok = char_ok = chars = oracle = lenient = 0
    misses, latency = [], []
    for ctx, truth in rows:
        syls = lex.to_syllables(truth)
        nbest = decode(lex, syls)
        texts = ["".join(ws) for _, ws in nbest]
        oracle += truth in texts
        if lm is None:
            out = texts[0]
        else:
            t = time.perf_counter()
            out = rerank(lm, ctx, nbest)
            latency.append(time.perf_counter() - t)
        sent_ok += out == truth
        lenient += out.translate(LENIENT) == truth.translate(LENIENT)
        char_ok += sum(a == b for a, b in zip(out, truth))
        chars += len(truth)
        if out != truth:
            misses.append((truth, out))
    n = len(rows)
    res = {"n": n, "sent_acc": round(sent_ok / n, 3), "lenient_acc": round(lenient / n, 3),
           "char_acc": round(char_ok / chars, 4), f"oracle@{BEAM}": round(oracle / n, 3)}
    if latency:
        latency.sort()
        res["p50_ms"] = round(1000 * latency[len(latency) // 2])
        res["p95_ms"] = round(1000 * latency[int(len(latency) * 0.95)])
    return res, misses


# 學習模擬：使用者在情境 A 改選冷門同音詞一次，之後在情境 B（該用常用詞）與情境 A' 打字。
LEARN_CASES = [
    # (改選時的句子, 之後該用常用詞的句子們, 之後該用冷門詞的句子們)
    ("蛋糕你嚐嚐看", ["輸入法常常選錯字", "他常常遲到", "我們常常見面"], ["蛋糕你嚐嚐", "湯你嚐嚐"]),
    ("下週是期中考", ["其中一個沒來", "我們在其中找到答案"], ["期中報告明天交", "下週是期中考"]),
    ("大家有異議嗎", ["這件事很有意義", "人生的意義"], ["你有異議嗎", "大家有異議嗎"]),
]


def simulate(lex):
    print("\n## 學習模擬：在情境 A 改選冷門詞一次（或三次）後，其他句子的正確率")
    print(f"{'策略':14s}{'改選次數':>6s}{'常用詞句':>10s}{'冷門詞句':>10s}")
    for times in (1, 3):
        for cls in (NoLearning, GlobalBoost, ContextKeyed, Promotion):
            good = bad = good_n = bad_n = 0
            for teach, commons, rares in LEARN_CASES:
                learner = cls() if cls is NoLearning else cls(lex)
                words = segment_words(lex, teach)
                for _ in range(times):
                    out_words = decode(lex, lex.to_syllables(teach), learner)[0][1]
                    # 找出使用者改掉的那個詞，記下它的前一個詞與讀音
                    for i, (w, syls) in enumerate(words):
                        if w not in out_words:
                            prev = words[i - 1][0] if i else "<s>"
                            learner.observe(prev, syls, None, w)
                for s in commons:
                    good += "".join(decode(lex, lex.to_syllables(s), learner)[0][1]) == s
                    good_n += 1
                for s in rares:
                    bad += "".join(decode(lex, lex.to_syllables(s), learner)[0][1]) == s
                    bad_n += 1
            print(f"{cls.__name__:14s}{times:>6d}{good:>7d}/{good_n:<4d}{bad:>7d}/{bad_n:<4d}")


def main(models):
    lex = Lexicon()
    sets = {"同音陷阱集": load_rows("homophones.txt"), "日常驗證集": load_rows("daily.txt"),
            "萌典例句": load_moedict(lex)}
    if os.environ.get("IME_SETS"):   # 例：IME_SETS=daily，只重跑某幾組；用 ASCII 別名避開 cmd.exe 編碼
        alias = {"trap": "同音陷阱集", "daily": "日常驗證集", "moedict": "萌典例句"}
        want = {alias[k] for k in os.environ["IME_SETS"].split(",")}
        sets = {k: v for k, v in sets.items() if k in want}
    for name, rows in sets.items():
        rows[:] = [r for r in rows if lex.to_syllables(r[1])]
        res, misses = evaluate(lex, rows)
        print(f"\n## {name}  unigram  {res}")
        if name != "萌典例句":
            for t, o in misses:
                print(f"   ✗ {t} → {o}")
    simulate(lex)
    for m in models:
        lm = make_scorer(m)
        lm.score("", ["暖機"])
        for name, rows in sets.items():
            res, misses = evaluate(lex, rows, lm)
            print(f"\n## {name}  {m}  {res}")
            if name != "萌典例句":
                for t, o in misses:
                    print(f"   ✗ {t} → {o}")
        del lm


if __name__ == "__main__":
    main(sys.argv[1:])
