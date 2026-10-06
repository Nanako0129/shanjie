"""S2 實驗：從中文維基 dump 統計詞的 unigram／bigram（只訓練、不散布原文）。

串流讀 bz2，去掉 wiki 標記，用 OpenCC 對照表（commit 3ac34aa，Apache-2.0）轉台灣繁體，
在句末標點切句，用善解詞庫（基底＋疊加層）的最高分切分斷詞，計數寫到 ~/.cache/shanjie/work/s2/。
用法：python3 experiments/s2/build_counts.py [--articles N]
"""
import argparse
import bz2
import collections
import hashlib
import math
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


def _checked(name, sha):
    """讀 OpenCC 檔之前核對 SHA-256（同一個 OpenCC commit 3ac34aa…；寫在 docs/PLAN.md 的來源表）。"""
    path = os.path.join(SRC, "opencc", name)
    got = hashlib.sha256(open(path, "rb").read()).hexdigest()
    if got != sha:
        sys.exit(f"SHA-256 mismatch for OpenCC {name}: {got}")
    return path


# S2f 契約 §2.1：台灣字形表。TWVariants.txt 每條取第一個值；排除 4 條（詞庫的成對詞偏好不在轉換後的寫法：
# 污 32:2、癡 12:4、樑 9:9、蔘 1:2，Fable 2026-10-07 逐條對照）；k==v 的恆等列（梁）略過。
TW_VARIANTS_EXCLUDED = {"污", "癡", "樑", "蔘"}


def _load_tw_variants():
    out = {}
    for line in open(_checked("TWVariants.txt", "245b94eb5842957e735dd44b7e7d4ff469a3643126cc8fa511adda5281e9cb86"), encoding="utf-8"):
        p = line.rstrip("\n").split("\t")
        if len(p) == 2 and not line.startswith("#"):
            v = p[1].split(" ")[0]
            if v != p[0] and p[0] not in TW_VARIANTS_EXCLUDED:
                out[p[0]] = v
    return out


TW_VARIANTS = {}   # load_conv() 填（S2f 修訂一：import 不讀檔）；build_overlay.py、build_tune.py（經 build_counts）與之後的 S2w 都用這一個
# 繁體句的簡體專用字要再扣掉的字：OpenCC 沒有換回、但基底詞庫有 5 條以上用到（S2f 契約 §2.2）
TRAD_KEEP = set("秘庄晒霉虱么肴洒痒")


def load_conv():
    """回傳 (詞組表, 字表, 最長詞組)，給「繁體句」用。只收「簡體專用字」：本身也是正確繁體的字（例：吃、后、里、游）不轉，
    否則會把原本正確的繁體字改掉（2026-10-03 的 bug：吃→喫、后→後、里→裏）。詞組只在含簡體專用字時才收。
    S2n：同時填好「簡體句」用的全域表（SIMP_*、MARKERS、TRAD_ONLY），見 convert()。"""
    simp_only, phrase, char = set(), {}, {}
    keys, vals, first_char, simp_phrase = set(), set(), {}, {}
    TW_VARIANTS.clear(); TW_VARIANTS.update(_load_tw_variants())
    for line in open(_checked("STCharacters.txt", "a0ca1601c70648cf48b33c3c6210ccbecc5c7eead4b4c3daf76587ba2c03582b"), encoding="utf-8"):
        p = line.rstrip("\n").split("\t")
        if len(p) == 2 and not line.startswith("#"):   # 檔頭的 `# Format: key<TAB>value(s)` 不是對照
            keys.add(p[0]); vals.update("".join(p[1].split(" "))); first_char[p[0]] = p[1].split(" ")[0]
            if p[0] not in p[1].split(" "):
                simp_only.add(p[0])
    # S2f §2.2：繁體句用縮小後的簡體專用字（扣掉 TW_VARIANTS 的目標字與 TRAD_KEEP）；SIMP_ONLY（簡繁判斷）仍用完整集合
    trad_simp_only = simp_only - set(TW_VARIANTS.values()) - TRAD_KEEP
    for c in trad_simp_only:
        char[c] = first_char[c]
    for f in ["STPhrases.txt", "TWPhrases.txt"]:
        for line in open(_checked(f, PHRASE_SHA[f]), encoding="utf-8"):
            if line.startswith("#") or "\t" not in line:
                continue
            k, v = line.rstrip("\n").split("\t")
            if f == "TWPhrases.txt":
                TW_PHRASE[k] = v.split(" ")[0]
            else:
                simp_phrase[k] = v.split(" ")[0]
                if any(c in trad_simp_only for c in k):
                    phrase[k] = v.split(" ")[0]
    # S2f 修訂一：繁體句的字表只剩縮小後的簡體專用字；VARIANTS 全部交給最後一層（TWPhrases 之後）
    SIMP_PHRASE.clear(); SIMP_PHRASE.update(simp_phrase)
    SIMP_CHAR.clear(); SIMP_CHAR.update({c: (c if c in TAIWAN_KEEP else v) for c, v in first_char.items()})
    MARKERS.clear(); MARKERS.update(c for c, v in first_char.items() if c != v)   # 標記字：第一個對照不是自己（含 后、于、里 這類）
    TRAD_SIMP_ONLY.clear(); TRAD_SIMP_ONLY.update(trad_simp_only)                  # 繁體句的簡體專用字（縮小後）；build_overlay.py 也用
    SIMP_ONLY.clear(); SIMP_ONLY.update(simp_only)                                 # 簡體專用字：對照裡不含自己
    TRAD_ONLY.clear(); TRAD_ONLY.update(first_char[c] for c in MARKERS)            # 繁體專用字：標記字的第一個對照（像、待、座 不在其中）
    SIMP_MAXP[0] = max(map(len, simp_phrase))
    TW_MAXP[0] = max(map(len, TW_PHRASE), default=1)
    POST_SIMP.clear(); POST_SIMP.update({**TW_VARIANTS, **TW_CHAR})
    POST_TRAD.clear(); POST_TRAD.update({**TW_VARIANTS, **VARIANTS})
    # S2f 修訂一 6.2.1：保護詞只看基底詞庫（成員與「不在詞庫」都是）；疊加層本身用到轉換，讀它會循環。
    # 修訂二 7.2.1：只收含 PROTECT_KEYS 的詞；VARIANTS 鍵（裏 羣 啓）與 喫、竈 照常轉換，否則兩字保護詞會在無關文字裡命中（痛苦里→痛苦裏）。
    # ponytail: 「三棱鏡」裡的「三棱」也被保護（詞庫是「三稜鏡」）；要逐詞反查再說。
    base = set(ime.Lexicon(os.path.join(ROOT, "data", "lexicon", "mcbpmf-data.txt")).by_word)
    PROTECT.clear()
    PROTECT.update({w: w for w in base if any(c in PROTECT_KEYS for c in w) and "".join(POST_TRAD.get(c, c) for c in w) not in base})
    PROTECT_MAXP[0] = max(map(len, PROTECT), default=1)
    return phrase, char, max(map(len, phrase))


# OpenCC 只轉簡體；維基常見的繁體異體字另外換成台灣用字
VARIANTS = {"爲": "為", "衆": "眾", "綫": "線", "麪": "麵", "僞": "偽", "裏": "裡", "峯": "峰", "羣": "群", "啓": "啟", "敎": "教", "着": "著", "説": "說"}


# S2n 簡體句用的表（load_conv 填）：全部 STPhrases、STCharacters 第一個對照、簡體專用字、繁體專用字
SIMP_PHRASE, SIMP_CHAR, MARKERS, SIMP_ONLY, TRAD_ONLY, SIMP_MAXP = {}, {}, set(), set(), set(), [0]
TRAD_SIMP_ONLY = set()
# 台灣用字例外（S2n 契約 §2.1）：簡體句的字表這一步保留原字，不換成 STCharacters 的第一個對照（吃→喫、岩→巖 在台灣不是正確用字）。
# 挑法：2026-10-05 在維基前 25,000 篇被判為繁體句的句子裡，原字出現次數 >= 3 倍第一個對照、且原字 >= 30 次；
# 比例 吃 28.8、皂 78.5、唇 12.2、岩 9.1、岳 5.0、咸 3.4（experiments/s2n/pick_exceptions.py，輸出在契約 §2.1）。
# 后 于 里 台 干 余 不在此列：契約表明定要轉。詞組那一步照舊，所以 岩石、范围 這類由詞組決定。
TAIWAN_KEEP = set("吃皂唇岩岳咸秘")   # S2f：加 秘（簡體句保留，秘／祕 由 build_lm 的合併與詞庫分數決定）
# S2f §2.4：build_lm 合併同讀音異體寫法的字表（週／周、唸／念、嚐／嘗、它／牠、妳 台灣用法有分工，不在其中）；build_overlay 的 §2.5 也用（再加 TW_VARIANTS）
MERGE = {**VARIANTS, "佔": "占", "佈": "布", "祕": "秘", "臺": "台", "牀": "床", "汙": "污"}
TW_CHAR = {**VARIANTS, "臺": "台"}   # 簡體句的台灣用字（併進 POST_SIMP）
# S2f §2.3：全部轉換完（含 TWPhrases）之後再套一層；兩種句子各一張表（繁體句不套 臺→台）。重疊的 9 條方向相同（test_convert 檢查）
# load_conv() 填；PROTECT 裡的詞在這一層原樣保留（修訂一 6.2.1）。
POST_SIMP, POST_TRAD, PROTECT, PROTECT_MAXP = {}, {}, {}, [1]
PROTECT_KEYS = set("脣泄棱覈齶")   # 修訂二：TWVariants 獨有、而且會改壞基底詞的鍵（排泄、棱錐、泄殖腔…）
PHRASE_SHA = {"STPhrases.txt": "f6eab5e5c6dd7640597878d3dfc6599ee1279d2bc91561eadd8e114194e2925a",
              "TWPhrases.txt": "bcb435b744ee3e522beb9b18fcc5486a36ed4763c6aa642ce18112fb5d604e31"}


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
    """簡體句（S2n）：全部 STPhrases 最長優先，沒蓋到的字用 STCharacters 第一個對照。
    其他句（繁體句，含簡繁夾雜）：只轉縮小後的簡體專用字（S2f）。兩種都再套台灣用詞（TWPhrases），
    最後一層保護詞原樣保留、其餘逐字套台灣字形表（簡體句另含 臺→台；S2f §2.3 與修訂一）。"""
    simp = is_simplified(text)
    # S2f 修訂一 6.2.2：TWPhrases 之前不套台灣字形或 VARIANTS（OpenCC 的順序），鍵寫成 OpenCC 字形的台灣用詞才對得到
    text = _longest(text, SIMP_PHRASE, SIMP_MAXP[0], SIMP_CHAR) if simp else _longest(text, phrase, maxp, char)
    text = _longest(text, TW_PHRASE, TW_MAXP[0]) if TW_PHRASE else text
    table = POST_SIMP if simp else POST_TRAD   # 喫→吃 也在 TW_VARIANTS 裡，S2n 的「輸入有喫就保留」規則拿掉
    return _longest(text, PROTECT, PROTECT_MAXP[0], table)   # 保護詞原樣保留，其餘逐字套表


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


EXPECTED_MIN = 0.01   # --expected：單一段落裡期望次數低於這個值的詞與二元組不記（S2n 契約 §6.2；事先寫死，用來控制記憶體）


def _lse10(terms):
    m = max(terms)
    return m + math.log10(sum(10 ** (t - m) for t in terms))


def forward_backward(lex, text):
    """詞圖（和 segment() 同一張：每個在 lex.by_word 裡、長度 <= lex.max_len 的子字串是一條邊，分數用 by_word 的 log10p）
    上的前向後向，log10 空間。回傳 (n, ends, starts, alpha, beta)；ends[j]=[(i, w, lp)]、starts[i]=[(j, w, lp)]；沒有任何切法回 None。
    alpha[0]=0、beta[n]=0，Z = beta[0]。"""
    n = len(text)
    ends, starts = [[] for _ in range(n + 1)], [[] for _ in range(n + 1)]
    for j in range(1, n + 1):
        for L in range(1, min(lex.max_len, j) + 1):
            w = lex.by_word.get(text[j - L:j])
            if w:
                ends[j].append((j - L, text[j - L:j], w[1])); starts[j - L].append((j, text[j - L:j], w[1]))
    NEG = -math.inf
    alpha, beta = [NEG] * (n + 1), [NEG] * (n + 1)
    alpha[0] = beta[n] = 0.0
    for j in range(1, n + 1):
        t = [alpha[i] + lp for i, _, lp in ends[j] if alpha[i] > NEG]
        if t:
            alpha[j] = _lse10(t)
    for i in range(n - 1, -1, -1):
        t = [lp + beta[j] for j, _, lp in starts[i] if beta[j] > NEG]
        if t:
            beta[i] = _lse10(t)
    if beta[0] == NEG:
        return None
    return n, ends, starts, alpha, beta


def expected_counts(lex, text, min_count=EXPECTED_MIN):
    """S2n 契約 §6.2：一段連續漢字的期望詞數與期望二元組數（含 <s>、</s> 配對）。回傳 (uni, bi) 兩個 dict；沒有切法回 None。
    詞 w 在 i..j：10^(alpha[i]+lp+beta[j]-Z)；相鄰 a(i..j)、b(j..k)：10^(alpha[i]+lpa+lpb+beta[k]-Z)；小於 min_count 的不記。"""
    fb = forward_backward(lex, text)
    if fb is None:
        return None
    n, ends, starts, alpha, beta = fb
    Z, NEG = beta[0], -math.inf
    lmin = math.log10(min_count) if min_count > 0 else NEG
    uni, bi = collections.defaultdict(float), collections.defaultdict(float)
    for j in range(1, n + 1):
        for i, a, lpa in ends[j]:
            la = alpha[i] + lpa + beta[j] - Z      # 詞 a 的對數期望次數；alpha 或 beta 是 -inf 時也是 -inf，下面的比較會濾掉
            if la < lmin or la == NEG:
                continue
            e = 10 ** la
            uni[a] += e
            if i == 0:
                bi[("<s>", a)] += e
            if j == n:
                bi[(a, "</s>")] += e
            for k, b, lpb in starts[j]:            # 二元組不會大於 a 或 b 的期望次數，所以 la 已經過門檻才需要往下看
                lb = alpha[i] + lpa + lpb + beta[k] - Z
                if lb >= lmin and lb > NEG:
                    bi[(a, b)] += 10 ** lb
    return uni, bi


_W = {}


def _init(trigram=False, expected=False):
    _W["trigram"] = trigram
    _W["expected"] = expected
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
                if _W["expected"]:
                    r = expected_counts(lex, run)
                    if r is None:
                        continue
                    sents += 1
                    for w, e in r[0].items():
                        uni[w] += e
                    for k, e in r[1].items():
                        bi[k] += e
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
    ap.add_argument("--expected", action="store_true", help="詞圖上的期望次數（S2n 契約 §6.2），取代最高分切分；不算 trigram")
    a = ap.parse_args()
    if a.expected and a.trigram:
        ap.error("--expected 不算 trigram")
    os.makedirs(OUT, exist_ok=True)
    uni, bi, tri = collections.Counter(), collections.Counter(), collections.Counter()
    sents = arts = 0
    with mp.Pool(a.procs, initializer=_init, initargs=(a.trigram, a.expected)) as pool:
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
