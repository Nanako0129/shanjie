"""S2 實驗：從中文維基 dump 統計詞的 unigram／bigram（只訓練、不散布原文）。

串流讀 bz2，去掉 wiki 標記，用 OpenCC 對照表（commit 3ac34aa，Apache-2.0）轉台灣繁體，
在句末標點切句，用善解詞庫（基底＋疊加層）的最高分切分斷詞，計數寫到 ~/.cache/shanjie/work/s2/。
用法：python3 experiments/s2/build_counts.py [--articles N]
"""
import argparse
import bz2
import collections
import os
import pickle
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "reference", "proto"))
import ime  # noqa: E402

SRC = os.path.expanduser("~/.cache/shanjie/sources")
OUT = os.path.expanduser("~/.cache/shanjie/work/s2")
DUMP = os.path.join(SRC, "zhwiki-20261001-pages-articles.xml.bz2")


def load_conv():
    """回傳 (詞組表, 字表, 最長詞組)。只收「簡體專用字」：本身也是正確繁體的字（例：吃、后、里、游）不轉，
    否則會把原本正確的繁體字改掉（2026-10-03 的 bug：吃→喫、后→後、里→裏）。詞組只在含簡體專用字時才收。"""
    simp_only, phrase, char = set(), {}, {}
    for line in open(os.path.join(SRC, "opencc", "STCharacters.txt"), encoding="utf-8"):
        p = line.rstrip("\n").split("\t")
        if len(p) == 2 and p[0] not in p[1].split(" "):
            simp_only.add(p[0]); char[p[0]] = p[1].split(" ")[0]
    for f in ["STPhrases.txt", "TWPhrases.txt"]:
        for line in open(os.path.join(SRC, "opencc", f), encoding="utf-8"):
            if line.startswith("#") or "\t" not in line:
                continue
            k, v = line.rstrip("\n").split("\t")
            if f == "TWPhrases.txt":
                TW_PHRASE[k] = v.split(" ")[0]
            elif any(c in simp_only for c in k):
                phrase[k] = v.split(" ")[0]
    char.update(VARIANTS)
    return phrase, char, max(map(len, phrase))


# OpenCC 只轉簡體；維基常見的繁體異體字另外換成台灣用字
VARIANTS = {"爲": "為", "衆": "眾", "綫": "線", "麪": "麵", "僞": "偽", "裏": "裡", "峯": "峰", "羣": "群", "啓": "啟", "敎": "教"}


TW_PHRASE = {}   # 台灣用詞（TWPhrases，鍵是繁體詞組），在簡轉繁之後第二遍套用


def _longest(text, table, maxp, char=None):
    out, i, n = [], 0, len(text)
    while i < n:
        for L in range(min(maxp, n - i), 1, -1):
            w = table.get(text[i:i + L])
            if w:
                out.append(w); i += L; break
        else:
            out.append(char.get(text[i], text[i]) if char else text[i]); i += 1
    return "".join(out)


def convert(text, phrase, char, maxp):
    """兩段：簡轉繁（詞組＋簡體專用字），再套台灣用詞。"""
    text = _longest(text, phrase, maxp, char)
    return _longest(text, TW_PHRASE, max(map(len, TW_PHRASE), default=1)) if TW_PHRASE else text


MARKUP = [
    (re.compile(r"<!--.*?-->", re.S), ""),
    (re.compile(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>", re.S), ""),
    (re.compile(r"\{\|.*?\|\}", re.S), ""),
    (re.compile(r"-\{(?:[^{}|]*\|)?([^{}]*?)\}-"), r"\1"),
    (re.compile(r"\[\[(?:[^\]|]*\|)?([^\]|]*)\]\]"), r"\1"),
    (re.compile(r"\[https?://\S+ ([^\]]*)\]"), r"\1"),
    (re.compile(r"<[^>]+>"), ""),
    (re.compile(r"'{2,}"), ""),
]
TEMPLATE = re.compile(r"\{\{[^{}]*\}\}")
SENT = re.compile(r"[^。！？；\n]+")
HAN = re.compile(r"[一-鿿]+")


def articles(limit):
    text_re = re.compile(r"<text[^>]*>(.*?)</text>", re.S)
    buf, n = "", 0
    with bz2.open(DUMP, "rt", encoding="utf-8") as f:
        for line in f:
            buf += line
            if "</page>" in line:
                if "<ns>0</ns>" in buf and "#REDIRECT" not in buf.upper() and "#重定向" not in buf:
                    m = text_re.search(buf)
                    if m:
                        yield m.group(1)
                        n += 1
                        if n >= limit:
                            return
                buf = ""


def segment(lex, text):
    """最高分切分（和核心的 segment_spans 同一規則：嚴格 > 保留第一個最佳）。"""
    n = len(text)
    best = [(-1e18, 0)] * (n + 1)
    best[0] = (0.0, 0)
    for i in range(1, n + 1):
        for L in range(1, min(lex.max_len, i) + 1):
            w = lex.by_word.get(text[i - L:i])
            if w and best[i - L][0] > -1e18:
                s = best[i - L][0] + w[1]
                if s > best[i][0]:
                    best[i] = (s, i - L)
    if best[n][0] <= -1e18:
        return None
    out, i = [], n
    while i > 0:
        j = best[i][1]; out.append(text[j:i]); i = j
    return out[::-1]


_W = {}


def _init():
    _W["lex"] = ime.Lexicon(os.path.join(ROOT, "data", "lexicon", "mcbpmf-data.txt"),
                            overlay=os.path.join(ROOT, "data", "lexicon", "overlay-add.tsv"))
    _W["conv"] = load_conv()


def count_batch(texts):
    lex, (phrase, char, maxp) = _W["lex"], _W["conv"]
    uni, bi, sents = collections.Counter(), collections.Counter(), 0
    for raw in texts:
        t = raw.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").replace("&quot;", '"')
        for _ in range(3):
            t = TEMPLATE.sub("", t)
        for pat, rep in MARKUP:
            t = pat.sub(rep, t)
        for s in SENT.findall(t):
            for run in HAN.findall(convert(s, phrase, char, maxp)):
                if len(run) < 2:
                    continue
                ws = segment(lex, run)
                if not ws:
                    continue
                sents += 1
                prev = "<s>"
                for w in ws:
                    uni[w] += 1; bi[(prev, w)] += 1; prev = w
                bi[(prev, "</s>")] += 1
    return uni, bi, sents


def batches(it, size=200):
    b = []
    for x in it:
        b.append(x)
        if len(b) == size:
            yield b; b = []
    if b:
        yield b


def main():
    import multiprocessing as mp
    ap = argparse.ArgumentParser()
    ap.add_argument("--articles", type=int, default=50_000)
    ap.add_argument("--procs", type=int, default=max(1, os.cpu_count() - 2))
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    uni, bi = collections.Counter(), collections.Counter()
    sents = arts = 0
    with mp.Pool(a.procs, initializer=_init) as pool:
        for u, b, s_ in pool.imap_unordered(count_batch, batches(articles(a.articles))):
            uni.update(u); bi.update(b); sents += s_; arts += 200
            if arts % 10000 == 0:
                print(f"~{arts} articles, {sents} runs, {len(uni)} words, {len(bi)} bigrams", flush=True)
    arts = min(arts, a.articles)
    pickle.dump({"uni": uni, "bi": bi, "articles": arts, "runs": sents}, open(os.path.join(OUT, f"counts-{arts}.pkl"), "wb"))
    print(f"done: {arts} articles, {sents} runs, {sum(uni.values())} tokens, {len(uni)} types, {len(bi)} bigram types")


if __name__ == "__main__":
    main()
