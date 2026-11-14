"""量測：unigram lattice vs. LLM 重排，以及學習策略的副作用。

用法：
  .venv/bin/python eval.py                      # 只跑 unigram 與學習模擬
  .venv/bin/python eval.py Qwen/Qwen3-0.6B-Base  # 再加上一或多個模型的重排
"""
import hashlib
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
# 寬鬆比對：上下文本來就判斷不了的人稱、以及兩種寫法都算對的異體字。
# 先套這張單字對照，再套教育部異體詞表 eval/variants.tsv（docs/spec/evaluation.md §1.3）。
CHARMAP = str.maketrans("她妳它牠嘗周臺裏", "他你他他嚐週台裡")
VARIANTS = os.environ.get("SHANJIE_VARIANTS") or os.path.join(HERE, "..", "..", "eval", "variants.tsv")
_table = None


def load_variants(path):
    """異體表：# 開頭是說明行，其餘每行剛好「異體<TAB>標準形」；欄數不對或異體重複就報錯。不做任何轉換。"""
    table = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#"):
                continue
            p = line.rstrip("\n").split("\t")
            if len(p) != 2 or not p[0] or not p[1]:
                raise ValueError("variants.tsv: bad line")
            if p[0] in table:
                raise ValueError("variants.tsv: duplicate variant")
            table[p[0]] = p[1]
    return table


def lenient(s):
    """先套單字對照，再由左到右最長匹配，把異體換成標準形。"""
    global _table
    if _table is None:
        t = load_variants(VARIANTS)
        _table = (t, max(map(len, t), default=0))
    table, longest = _table
    s = s.translate(CHARMAP)
    out, i = [], 0
    while i < len(s):
        for n in range(min(longest, len(s) - i), 0, -1):
            c = table.get(s[i:i + n])
            if c is not None:
                out.append(c)
                i += n
                break
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


# --- judge-v2（docs/spec/evaluation.md §3.5–§3.7；core/src/eval.rs 的 Forms、Accept、Judge2 的對照實作）---
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(HERE)))
FORMS = os.environ.get("SHANJIE_FORMS") or os.path.join(ROOT, "eval", "forms.tsv")
FORMS_EXTRA = os.environ.get("SHANJIE_FORMS_EXTRA") or os.path.join(ROOT, "eval", "forms-extra.tsv")


def _lines(text):
    """和 Rust 的 str::lines 一樣只在 \\n 切行（結尾的換行不多出一個空行）。"""
    lines = text.split("\n")
    return lines[:-1] if lines[-1] == "" else lines


class Forms:
    """合法寫法表：詞對（由左到右最長匹配、單趟）再單字對（看該位置的讀音）。每道替換等長。"""

    def __init__(self):
        self.words, self.chars = {}, {}

    def parse(self, text):
        for i, line in enumerate(_lines(text), 1):
            if line.startswith("#"):
                continue
            f = line.split("\t")
            if len(f) == 3 and f[0] == "word" and f[1] and len(f[1]) == len(f[2]) and f[1] not in self.words:
                self.words[f[1]] = f[2]
            elif len(f) == 4 and f[0] == "char" and len(f[1]) == 1 and len(f[2]) == 1 and "" not in f[3].split(",") and f[1] not in self.chars:
                self.chars[f[1]] = (f[2], f[3].split(","))
            else:
                raise ValueError(f"forms table: bad line {i}")
        return self

    def add_extra(self, text):
        """forms-extra.tsv：六欄 word 寫法 標準形 讀音 次數(a/b) 依據；詞對併進同一個集合，寫法不可重複。"""
        for i, line in enumerate(_lines(text), 1):
            if line.startswith("#"):
                continue
            f = line.split("\t")
            ok = len(f) == 6 and f[0] == "word" and f[1] and len(f[1]) == len(f[2]) and f[3] and f[5]
            a, _, b = f[4].partition("/") if len(f) == 6 else ("", "", "")
            ok = ok and a.isascii() and a.isdigit() and b.isascii() and b.isdigit()
            if not ok or f[1] in self.words:
                raise ValueError(f"forms-extra table: bad line {i}")
            self.words[f[1]] = f[2]
        return self

    def apply(self, s, syls):
        longest = max(map(len, self.words), default=0)
        out, i = [], 0
        while i < len(s):
            for n in range(min(longest, len(s) - i), 0, -1):
                c = self.words.get(s[i:i + n])
                if c is not None:
                    out.append(c)
                    i += n
                    break
            else:
                out.append(s[i])
                i += 1
        if len(out) != len(syls):
            return "".join(out)
        return "".join(self.chars[c][0] if c in self.chars and y in self.chars[c][1] else c for c, y in zip(out, syls))


def load_forms_v2():
    """forms-v2：forms.tsv 加 forms-extra.tsv。回傳 (Forms, forms.tsv 的 SHA-256 前 8 碼, forms-extra.tsv 的前 8 碼)（摘要行 +jv2: 的 A、B 段）。"""
    fb, eb = open(FORMS, "rb").read(), open(FORMS_EXTRA, "rb").read()
    f = Forms().parse(fb.decode("utf-8")).add_extra(eb.decode("utf-8"))
    return f, hashlib.sha256(fb).hexdigest()[:8], hashlib.sha256(eb).hexdigest()[:8]


def load_accept(path, raw_rows):
    """逐列可接受答案檔：每行 列號 列SHA-256 整句 依據；回傳 ({列號: [句子]}, 檔案 SHA-256 前 8 碼)。列號那一列（raw_rows 是各列原文）的雜湊不符就 ValueError。"""
    raw = open(path, "rb").read()
    got, shas = {}, {}
    for i, line in enumerate(_lines(raw.decode("utf-8")), 1):
        if line.startswith("#"):
            continue
        f = line.split("\t")
        row = f[0]
        ok = len(f) == 4 and row.isascii() and row.isdigit() and not row.startswith("0")
        ok = ok and len(f[1]) == 64 and all(c in "0123456789abcdef" for c in f[1]) and f[2] and f[3]
        ok = ok and shas.setdefault(int(row), f[1]) == f[1] and f[2] not in got.get(int(row), [])
        if not ok:
            raise ValueError(f"accept file: bad line {i}")
        got.setdefault(int(row), []).append(f[2])
    for row, sha in shas.items():
        if row > len(raw_rows) or hashlib.sha256(raw_rows[row - 1].encode("utf-8")).hexdigest() != sha:
            raise ValueError(f"accept file does not match the rows (row {row})")
    return got, hashlib.sha256(raw).hexdigest()[:8]


def judge2(forms, gold, syls, accepted=()):
    """一列的 judge-v2 判定函式：候選與正解（與每個可接受答案）各自先 lenient（L1）再套 forms-v2（L2）後相等，就是對（L3 是可接受答案那一支）。"""
    l2 = lambda x: forms.apply(lenient(x), syls)
    ok_set = {l2(gold)} | {l2(a) for a in accepted}
    return lambda cand: l2(cand) in ok_set


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
    sent_ok = char_ok = chars = oracle = len_ok = 0
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
        len_ok += lenient(out) == lenient(truth)
        char_ok += sum(a == b for a, b in zip(out, truth))
        chars += len(truth)
        if out != truth:
            misses.append((truth, out))
    n = len(rows)
    res = {"n": n, "sent_acc": round(sent_ok / n, 3), "lenient_acc": round(len_ok / n, 3),
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
    if sys.argv[1:2] == ["--lenient-dump"]:   # S2v 對照：每行印 lenient(行)
        for line in open(sys.argv[2], encoding="utf-8"):
            print(lenient(line.rstrip("\n")))
    else:
        main(sys.argv[1:])
