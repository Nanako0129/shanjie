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
OUT = os.environ.get("S2_WORK") or os.path.expanduser("~/.cache/shanjie/work/s2")   # S2_WORK：寫到別處（S2n 重建時不蓋掉舊的計數）
DUMP = os.path.join(SRC, "zhwiki-20261001-pages-articles.xml.bz2")


def load_conv():
    """回傳 (詞組表, 字表, 最長詞組)，給「繁體句」用。只收「簡體專用字」：本身也是正確繁體的字（例：吃、后、里、游）不轉，
    否則會把原本正確的繁體字改掉（2026-10-03 的 bug：吃→喫、后→後、里→裏）。詞組只在含簡體專用字時才收。
    S2n：同時填好「簡體句」用的全域表（SIMP_*、MARKERS、TRAD_ONLY），見 convert()。"""
    simp_only, phrase, char = set(), {}, {}
    keys, vals, first_char, simp_phrase = set(), set(), {}, {}
    for line in open(os.path.join(SRC, "opencc", "STCharacters.txt"), encoding="utf-8"):
        p = line.rstrip("\n").split("\t")
        if len(p) == 2 and not line.startswith("#"):   # 檔頭的 `# Format: key<TAB>value(s)` 不是對照
            keys.add(p[0]); vals.update("".join(p[1].split(" "))); first_char[p[0]] = p[1].split(" ")[0]
            if p[0] not in p[1].split(" "):
                simp_only.add(p[0]); char[p[0]] = p[1].split(" ")[0]
    for f in ["STPhrases.txt", "TWPhrases.txt"]:
        for line in open(os.path.join(SRC, "opencc", f), encoding="utf-8"):
            if line.startswith("#") or "\t" not in line:
                continue
            k, v = line.rstrip("\n").split("\t")
            if f == "TWPhrases.txt":
                TW_PHRASE[k] = v.split(" ")[0]
            else:
                simp_phrase[k] = v.split(" ")[0]
                if any(c in simp_only for c in k):
                    phrase[k] = v.split(" ")[0]
    char.update(VARIANTS)
    SIMP_PHRASE.clear(); SIMP_PHRASE.update(simp_phrase)
    SIMP_CHAR.clear(); SIMP_CHAR.update({c: (c if c in TAIWAN_KEEP else v) for c, v in first_char.items()})
    MARKERS.clear(); MARKERS.update(c for c, v in first_char.items() if c != v)   # 標記字：第一個對照不是自己（含 后、于、里 這類）
    SIMP_ONLY.clear(); SIMP_ONLY.update(simp_only)                                 # 簡體專用字：對照裡不含自己
    TRAD_ONLY.clear(); TRAD_ONLY.update(first_char[c] for c in MARKERS)            # 繁體專用字：標記字的第一個對照（像、待、座 不在其中）
    SIMP_MAXP[0] = max(map(len, simp_phrase))
    TW_MAXP[0] = max(map(len, TW_PHRASE), default=1)
    return phrase, char, max(map(len, phrase))


# OpenCC 只轉簡體；維基常見的繁體異體字另外換成台灣用字
VARIANTS = {"爲": "為", "衆": "眾", "綫": "線", "麪": "麵", "僞": "偽", "裏": "裡", "峯": "峰", "羣": "群", "啓": "啟", "敎": "教", "着": "著", "説": "說"}


# S2n 簡體句用的表（load_conv 填）：全部 STPhrases、STCharacters 第一個對照、簡體專用字、繁體專用字
SIMP_PHRASE, SIMP_CHAR, MARKERS, SIMP_ONLY, TRAD_ONLY, SIMP_MAXP = {}, {}, set(), set(), set(), [0]
# 台灣用字例外（S2n 契約 §2.1）：簡體句的字表這一步保留原字，不換成 STCharacters 的第一個對照（吃→喫、岩→巖 在台灣不是正確用字）。
# 挑法：2026-10-05 在維基前 25,000 篇被判為繁體句的句子裡，原字出現次數 >= 3 倍第一個對照、且原字 >= 30 次；
# 比例 吃 28.8、皂 78.5、唇 12.2、岩 9.1、岳 5.0、咸 3.4（experiments/s2n/pick_exceptions.py，輸出在契約 §2.1）。
# 后 于 里 台 干 余 不在此列：契約表明定要轉。詞組那一步照舊，所以 岩石、范围 這類由詞組決定。
TAIWAN_KEEP = set("吃皂唇岩岳咸")
TW_CHAR = {**VARIANTS, "臺": "台"}   # 簡體句轉完後整句套一遍的台灣用字


TW_PHRASE, TW_MAXP = {}, [1]   # 台灣用詞（TWPhrases，鍵是繁體詞組），在簡轉繁之後第二遍套用


_FIRST = {}   # (id(table), len(table)) → {首字: 該首字開頭的最長鍵}，讓沒有詞組可比對的位置不必切片查表（輸出和逐長度試完全相同）


def _longest(text, table, maxp, char=None):
    first = _FIRST.get((id(table), len(table)))
    if first is None:
        first = {}
        for k in table:
            if len(k) > first.get(k[0], 0):
                first[k[0]] = len(k)
        _FIRST[(id(table), len(table))] = first
    out, i, n = [], 0, len(text)
    while i < n:
        for L in range(min(first.get(text[i], 0), maxp, n - i), 1, -1):
            w = table.get(text[i:i + L])
            if w:
                out.append(w); i += L; break
        else:
            out.append(char.get(text[i], text[i]) if char else text[i]); i += 1
    return "".join(out)


def is_simplified(text):
    """簡體句（S2n 契約 §2.1）：(i) 有標記字而且沒有繁體專用字，或 (ii) 簡體專用字比繁體專用字多。"""
    m = sum(c in MARKERS for c in text)
    t = sum(c in TRAD_ONLY for c in text)
    return (m > 0 and t == 0) or sum(c in SIMP_ONLY for c in text) > t


def convert(text, phrase, char, maxp):
    """簡體句（S2n）：全部 STPhrases 最長優先，沒蓋到的字用 STCharacters 第一個對照，整句再套台灣用字（VARIANTS、臺→台）。
    其他句（繁體句，含簡繁夾雜）：只轉簡體專用字。兩種都再套台灣用詞。"""
    src = text
    if is_simplified(text):
        text = "".join(TW_CHAR.get(c, c) for c in _longest(text, SIMP_PHRASE, SIMP_MAXP[0], SIMP_CHAR))
    else:
        # 第三次重建：詞組輸出與字表輸出（为→爲）都還帶著 STCharacters 的寫法，整句輸出再套一遍 VARIANTS（不含 臺→台）
        text = "".join(VARIANTS.get(c, c) for c in _longest(text, phrase, maxp, char))
    text = _longest(text, TW_PHRASE, TW_MAXP[0]) if TW_PHRASE else text
    # 喫：STPhrases 的「吃了」「吃不出来」等詞組輸出喫；輸入句本來沒有喫就換回吃
    return text if "喫" in src else text.replace("喫", "吃")


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


def _init(trigram=False):
    _W["trigram"] = trigram
    _W["lex"] = ime.Lexicon(os.path.join(ROOT, "data", "lexicon", "mcbpmf-data.txt"),
                            overlay=os.path.join(ROOT, "data", "lexicon", "overlay-add.tsv"))
    _W["conv"] = load_conv()


def count_batch(texts):
    lex, (phrase, char, maxp) = _W["lex"], _W["conv"]
    uni, bi, tri, sents = collections.Counter(), collections.Counter(), collections.Counter(), 0
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
                if _W.get("trigram"):
                    seq = ["<s>", "<s>"] + ws + ["</s>"]
                    for i in range(2, len(seq)):
                        tri[(seq[i - 2], seq[i - 1], seq[i])] += 1
    return uni, bi, tri, sents


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
    ap.add_argument("--trigram", action="store_true", help="也算 trigram（記憶體用量大，請搭配較少的篇數）")
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    uni, bi, tri = collections.Counter(), collections.Counter(), collections.Counter()
    sents = arts = 0
    with mp.Pool(a.procs, initializer=_init, initargs=(a.trigram,)) as pool:
        for u, b, t, s_ in pool.imap_unordered(count_batch, batches(articles(a.articles))):
            uni.update(u); bi.update(b); tri.update(t); sents += s_; arts += 200
            if arts % 10000 == 0:
                print(f"~{arts} articles, {sents} runs, {len(uni)} words, {len(bi)} bigrams", flush=True)
    arts = min(arts, a.articles)
    out = {"uni": uni, "bi": bi, "articles": arts, "runs": sents}
    if a.trigram:
        out["tri"] = tri
    pickle.dump(out, open(os.path.join(OUT, f"counts-{arts}{'-tri' if a.trigram else ''}.pkl"), "wb"))
    print(f"done: {arts} articles, {sents} runs, {sum(uni.values())} tokens, {len(uni)} types, {len(bi)} bigram types")


if __name__ == "__main__":
    main()
