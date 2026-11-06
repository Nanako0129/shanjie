"""S2 實驗：從中文維基 dump 統計詞的 unigram／bigram（只訓練、不散布原文）。

串流讀 bz2，去掉 wiki 標記，用 OpenCC 對照表（commit 3ac34aa，Apache-2.0）加上台灣用量的轉換表 tw-conv.tsv 轉台灣繁體（維基是 mixed 來源，docs/contracts/tw-conv.md），
在句末標點切句，用善解詞庫（基底＋疊加層）的最高分切分斷詞，計數寫到 ~/.cache/shanjie/work/s2/。
用法：python3 experiments/s2/build_counts.py [--articles N]
"""
import argparse
import bz2
import collections
import hashlib
import html
import math
import os
import pickle
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "reference", "proto"))
import ime  # noqa: E402

if __name__ == "__main__":   # run as a script, holdout_guard's `import build_counts` must see this module (convert() reads load_conv()'s globals), not a second copy
    sys.modules["build_counts"] = sys.modules["__main__"]

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


# docs/contracts/tw-conv.md: native Taiwan text is never converted; Simplified sentences go through the table experiments/s2/tw-conv.tsv
# (built from Taiwan's own usage by build_tw_conv.py); the eight hand-written exception tables of S2f/S2n are gone.
TW_CONV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tw-conv.tsv")
KINDS = ("native", "mixed")   # contract 2.1: the caller says which kind every input is; there is no default


def read_opencc():
    """The hash-checked OpenCC tables as {"st": {key: [values]}, "stp": {key: first value}, "twv": {key: first value}, "twp": {key: first value}}
    (STCharacters, STPhrases, TWVariants without identity rows, TWPhrases). load_conv() and experiments/s2/build_tw_conv.py (docs/contracts/tw-conv.md) share it."""
    def rows(name, sha):
        for line in open(_checked(name, sha), encoding="utf-8"):
            p = line.rstrip("\n").split("\t")
            if len(p) == 2 and not line.startswith("#"):
                yield p[0], p[1].split(" ")
    twv = {k: v[0] for k, v in rows("TWVariants.txt", "245b94eb5842957e735dd44b7e7d4ff469a3643126cc8fa511adda5281e9cb86") if v[0] != k}
    return {"st": dict(rows("STCharacters.txt", "a0ca1601c70648cf48b33c3c6210ccbecc5c7eead4b4c3daf76587ba2c03582b")),
            "stp": {k: v[0] for k, v in rows("STPhrases.txt", PHRASE_SHA["STPhrases.txt"])},
            "twv": twv, "twp": {k: v[0] for k, v in rows("TWPhrases.txt", PHRASE_SHA["TWPhrases.txt"])}}


def lexicon_word_chars():
    """Every character that occurs in a word of two or more characters of the base lexicon (single-character entries also carry Simplified
    characters at the lowest score, so they say nothing)."""
    return {c for w in ime.Lexicon(os.path.join(ROOT, "data", "lexicon", "mcbpmf-data.txt")).by_word if len(w) >= 2 for c in w}


def true_simplified(st, word_chars):
    """docs/contracts/tw-conv.md 2.2, set S: keys of STCharacters whose values do not contain the key and that no lexicon word of two or more characters uses."""
    return {k for k, v in st.items() if k not in v and k not in word_chars}


def read_tw_conv(path=TW_CONV):
    """The tw-conv.tsv rows as {section: {key: chosen value}}: `manual` (value@date) beats the rule; a `nofold` fold row chooses the key itself.
    A missing file stops with the command that makes it."""
    if not os.path.isfile(path):
        sys.exit(f"{path} is missing: run experiments/s2/build_tw_conv.py (docs/contracts/tw-conv.md 2.3)")
    out = {"fold": {}, "char": {}, "phrase": {}, "term": {}, "n": {}}   # "n": the set N (contract 7) as {key: chosen value}, the char rows flagged in column 7
    for line in open(path, encoding="utf-8"):
        if line.startswith("#") or not line.strip():
            continue
        sec, key, value, status, manual, _, rare = (line.rstrip("\n").split("\t") + [""] * 7)[:7]
        out[sec][key] = manual.split("@")[0] if manual else (key if sec == "fold" and status != "fold" else value)
        if rare == "N":
            out["n"][key] = out[sec][key]
    return out


TW_VARIANTS = {}   # TWVariants.txt's first value of every entry (identity rows left out); load_conv() fills it. Only the sets T and the fold candidates come from it
TRAD_SIMP_ONLY = set()   # set S (contract 2.2): true Simplified characters. Also what the Traditional-sentence path converts and what the corpus cleaners drop
MARKERS = set()   # set M: keys whose first STCharacters value is not themselves (含 后、于、里、台、床)
TRAD_ONLY = set()   # set T: Traditional evidence characters (values that are no key, the fold targets, the TWVariants targets), minus M (contract 2.2)
RARE = {}   # set N (contract 7): {key: chosen value} of the keys Taiwan almost never writes (装 痒 苹 价); converted on every path that skips the single-character table
FOLD = {}   # fold map {X: Y} of tw-conv.tsv (the rows folded by the PTT counts); applied last, to every kind of sentence
FOLD_TRANS = {}   # str.translate table of FOLD
TW_PHRASE, TW_MAXP = {}, [1]   # the mainland-term subset of TWPhrases (tw-conv.tsv `term` rows), Simplified sentences only


def load_conv():
    """Fills the module tables (S, M, T, FOLD, TW_VARIANTS, TW_PHRASE) and returns (phrase, char, longest phrase) for convert():
    phrase = STPhrases with tw-conv.tsv's chosen values, char = STCharacters' first value with tw-conv.tsv's chosen values."""
    o = read_opencc()
    st, tw = o["st"], read_tw_conv()
    S = true_simplified(st, lexicon_word_chars())
    FOLD.clear(); FOLD.update({x: y for x, y in tw["fold"].items() if x != y})
    FOLD_TRANS.clear(); FOLD_TRANS.update({ord(x): y for x, y in FOLD.items()})
    TW_VARIANTS.clear(); TW_VARIANTS.update(o["twv"])
    TRAD_SIMP_ONLY.clear(); TRAD_SIMP_ONLY.update(S)
    MARKERS.clear(); MARKERS.update(k for k, v in st.items() if v[0] != k)
    TRAD_ONLY.clear(); TRAD_ONLY.update(({c for v in st.values() for x in v for c in x if c not in st} | set(FOLD.values()) | set(o["twv"].values())) - MARKERS)
    RARE.clear(); RARE.update(tw["n"])
    TW_PHRASE.clear(); TW_PHRASE.update(tw["term"])
    TW_MAXP[0] = max(map(len, TW_PHRASE), default=1)
    phrase = {**o["stp"], **tw["phrase"]}
    char = {**{k: v[0] for k, v in st.items()}, **tw["char"]}
    return phrase, char, max(map(len, phrase))


# S2f 2.4: build_lm merges these spellings of one word (週／周、唸／念、嚐／嘗、它／牠、妳 are used differently in Taiwan, so they are not here) and
# build_overlay does the same to the overlay words; the holdout guard compares on it. A merge, not a conversion: it is unchanged by tw-conv.
MERGE = {"爲": "為", "衆": "眾", "綫": "線", "麪": "麵", "僞": "偽", "裏": "裡", "峯": "峰", "羣": "群", "啓": "啟", "敎": "教", "着": "著", "説": "說",
         "佔": "占", "佈": "布", "祕": "秘", "臺": "台", "牀": "床", "汙": "污"}
PHRASE_SHA = {"STPhrases.txt": "f6eab5e5c6dd7640597878d3dfc6599ee1279d2bc91561eadd8e114194e2925a",
              "TWPhrases.txt": "bcb435b744ee3e522beb9b18fcc5486a36ed4763c6aa642ce18112fb5d604e31"}


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


def sentence_kind(text):
    """Contract 2.2. R, M, T = how many characters of the sentence are in S, M, T. "simp": R >= 1 and R >= T. "marker": R = 0, M >= 1, T = 0
    (only marker characters: 之后他去了; 奇希里斯 and 后里 are in this class too, which is why it converts less). Otherwise "trad"."""
    r = m = t = 0
    for c in text:
        r += c in TRAD_SIMP_ONLY
        m += c in MARKERS
        t += c in TRAD_ONLY
    if r and r >= t:
        return "simp"
    return "marker" if not r and m and not t else "trad"


def is_simplified(text):
    return sentence_kind(text) != "trad"


def fold_tw(text):
    """Only the fold rows of tw-conv.tsv (contract 2.3): what any text may be changed by, native or converted."""
    return text.translate(FOLD_TRANS)


_CUT = {}   # (id(table), what) → (table, its cut-down copy); the table is kept so its id is never reused by another object


def _cut(table, what, keep):
    hit = _CUT.get((id(table), what))
    if hit is None or hit[0] is not table or hit[2] != (len(table), len(TRAD_SIMP_ONLY), len(RARE)):
        hit = _CUT[(id(table), what)] = (table, keep(), (len(table), len(TRAD_SIMP_ONLY), len(RARE)))
    return hit[1]


def _s_phrases(phrase):
    """The entries of `phrase` whose key has a character of S."""
    return _cut(phrase, "s", lambda: {k: v for k, v in phrase.items() if any(c in TRAD_SIMP_ONLY for c in k)})


def _only(char, kind):
    """`char` cut down to the characters a path may convert: S and N on the Traditional path, N on the marker-only path (cached per table)."""
    return _cut(char, kind, lambda: {c: char[c] for c in (TRAD_SIMP_ONLY | RARE.keys() if kind == "trad" else RARE.keys()) if c in char})


def convert(text, phrase, char, maxp):
    """The `mixed` conversion (contract 2.4); a `native` input is never given to it. simp: STPhrases longest first (chosen values), the remaining
    characters from `char`, the mainland-term subset of TWPhrases longest first, fold. marker (only marker characters): the phrases, then only the
    characters of N, the terms, fold. trad (also Traditional with a few Simplified characters): the phrases whose key has a character of S
    (longest first), then the characters of S and N not covered by one, fold; no other phrase, no terms."""
    kind = sentence_kind(text)
    if kind == "trad":
        s_phrase = _s_phrases(phrase)
        text = _longest(text, s_phrase, max(map(len, s_phrase), default=1), _only(char, "trad"))
        return text.translate(FOLD_TRANS)
    text = _longest(text, phrase, maxp, char if kind == "simp" else _only(char, "marker"))
    return _longest(text, TW_PHRASE, TW_MAXP[0]).translate(FOLD_TRANS)



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


def fold(text):
    """MERGE (臺/台、佔/占...) applied to every character: the holdout guard compares on this fold, as the model counts variants as one word."""
    return "".join(MERGE.get(c, c) for c in text)


def check_kind(kind):
    """The input kind of docs/contracts/tw-conv.md 2.1: "native" (never converted) or "mixed" (sentence by sentence, convert()). No default."""
    if kind not in KINDS:
        raise ValueError(f"input kind must be one of {KINDS}, got {kind!r}")
    return kind


def text_runs(line, conv, kind):
    """One plain-text line's Han runs (build_counts_text.main's loop, before its length filter). conv = load_conv()'s tuple; kind: see check_kind().
    A `native` line is not converted at all; a `mixed` line is converted as one sentence."""
    return HAN.findall(line if check_kind(kind) == "native" else convert(line, *conv))


def article_runs(raw, conv, mw=None, mwconv=None):
    """One wiki article's Han runs, in count_batch's order: entity unescape, [mw conversion], TEMPLATE x3, MARKUP, SENT, convert, HAN.
    The one copy of this pipeline: count_batch, build_counts_text, holdout_guard and s7a prep.py all call it (docs/contracts/holdout-refresh.md section 8).
    A wiki article is a `mixed` input (tw-conv contract 2.1)."""
    for s in article_sentences(raw, conv, mw, mwconv):
        yield from HAN.findall(s)


def article_text(raw, mw=None, mwconv=None):
    """The article body before the SENT split and before convert(): entity unescape, [mw conversion], TEMPLATE x3, MARKUP. Paragraphs are its
    lines. experiments/sj-lm-7/wikinews.py exports this layer so a reader (build_counts_text, holdout_guard --text) converts exactly once."""
    t = raw.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").replace("&quot;", '"')
    if mw:   # S2w：整篇先交給 MediaWiki 的 zh-tw 轉換（站上轉換表、NoteTA 群組、條目規則），之後的句子不再呼叫 convert()
        t = mwconv.convert(t, mw)
    for _ in range(3):
        t = TEMPLATE.sub("", t)
    for pat, rep in MARKUP:
        t = pat.sub(rep, t)
    return t


def article_sentences(raw, conv, mw=None, mwconv=None):
    """article_runs' steps up to (not including) the HAN split: one converted sentence per SENT match. tools/build_digits.py swaps the
    HAN split for one that keeps digit runs (docs/contracts/digit-context.md C.3); article_runs itself is unchanged."""
    for s in SENT.findall(article_text(raw, mw, mwconv)):
        yield s if mw else convert(s, *conv)


def articles(limit, dump=None, meta=False):
    """Main-namespace non-redirect pages in dump order: the wikitext, or with meta=True (title, revid, wikitext) (XML-unescaped title; revid
    is the first <revision><id>, "" when absent). Default: exactly the old output."""
    text_re = re.compile(r"<text[^>]*>(.*?)</text>", re.S)
    title_re, rev_re = re.compile(r"<title>(.*?)</title>", re.S), re.compile(r"<revision>\s*<id>(\d+)</id>")
    buf, n = "", 0
    with bz2.open(dump or DUMP, "rt", encoding="utf-8") as f:
        for line in f:
            buf += line
            if "</page>" in line:
                if "<ns>0</ns>" in buf and "#REDIRECT" not in buf.upper() and "#重定向" not in buf:
                    m = text_re.search(buf)
                    if m:
                        if meta:
                            t, r = title_re.search(buf), rev_re.search(buf)
                            yield html.unescape(t.group(1)).strip() if t else "", r.group(1) if r else "", m.group(1)
                        else:
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


def guard_matcher(rows):
    """--holdout-guard (docs/contracts/holdout-refresh.md section 8), called once in main() before any Pool exists: None when off, else the
    Matcher over the held-out rows (`rows` = row files; empty = every .txt under eval/holdout/). Prints how many rows were loaded and skipped.
    A bad rows file stops here (SystemExit, message = path, line, field count only), never inside a worker initializer."""
    if rows is None:
        return None
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import holdout_guard
    try:
        matcher, _, loaded, skipped = holdout_guard.build_matcher(list(rows), load_conv())
    except (holdout_guard.GuardError, OSError) as e:
        sys.exit(f"holdout-guard: {e}")
    print(f"holdout-guard: {loaded} rows loaded, {skipped} skipped as shorter than {holdout_guard.MIN_LEN} characters", flush=True)
    return matcher


def _init(trigram=False, expected=False, mw=False, extra_lexicons=(), holdout_guard=None):
    """holdout_guard: None, or the Matcher guard_matcher() built in main (already validated)."""
    _W["mw"] = None
    if mw:   # S2w：每個 worker 載一次 mwdata.json（契約 §3.2）
        sys.path.insert(0, os.path.join(ROOT, "experiments", "s2w"))
        import mwconv
        _W["mw"] = mwconv.load()
        _W["mwconv"] = mwconv
    _W["trigram"] = trigram
    _W["expected"] = expected
    _W["lex"] = ime.Lexicon(os.path.join(ROOT, "data", "lexicon", "mcbpmf-data.txt"),
                            overlay=[os.path.join(ROOT, "data", "lexicon", "overlay-add.tsv"), *extra_lexicons])   # --extra-lexicon：疊加層格式，接在 overlay-add.tsv 後面
    _W["pack"] = set()
    if extra_lexicons:
        # model-v5 契約 §8（加法計數）：一般的詞與二元組照沒有詞包的詞庫算（lex0），詞包詞只「加上」自己的單詞與
        # 含它的二元組（lex1）。直接用 lex1 會讓詞包裡的兩字片段（拉斯、萊恩）吃掉常用字對的計數，不開詞包時打不出來。
        _W["lex0"] = ime.Lexicon(os.path.join(ROOT, "data", "lexicon", "mcbpmf-data.txt"),
                                 overlay=[os.path.join(ROOT, "data", "lexicon", "overlay-add.tsv")])
        _W["pack"] = set(_W["lex"].by_word) - set(_W["lex0"].by_word)
        _W["pack_lens"] = sorted({len(w) for w in _W["pack"]})
    _W["conv"] = load_conv()
    _W["guard"] = holdout_guard


def run_expected(run):
    """--expected 的一段連續漢字：沒有詞包時就是 expected_counts(lex, run)。有詞包時是加法計數（契約 §8）：
    lex0（不含詞包）的全部單詞與二元組，加上 lex1（含詞包）裡詞包詞的單詞、以及一端是詞包詞的二元組。
    lex0 切不開的段落整段不計（和沒有詞包時相同）；段落裡沒有詞包詞的字串時不必算 lex1。"""
    pack = _W["pack"]
    if not pack:
        return expected_counts(_W["lex"], run)
    r0 = expected_counts(_W["lex0"], run)
    if r0 is None:
        return None
    n = len(run)
    if not any(run[i:i + L] in pack for i in range(n) for L in _W["pack_lens"] if i + L <= n):
        return r0
    r1 = expected_counts(_W["lex"], run)
    if r1 is None:
        return r0
    uni, bi = r0
    for w, e in r1[0].items():
        if w in pack:
            uni[w] += e
    for k, e in r1[1].items():
        if k[0] in pack or k[1] in pack:
            bi[k] += e
    return uni, bi


def count_batch(texts):
    return _count(texts)[:4]


def _count(texts):
    lex = _W["lex"]
    guard, skipped = _W.get("guard"), 0
    uni, bi, tri, sents = collections.Counter(), collections.Counter(), collections.Counter(), 0
    for raw in texts:
        for run in article_runs(raw, _W["conv"], _W["mw"], _W.get("mwconv")):
            if len(run) < 2:
                continue
            if guard is not None and guard.any(fold(run)):
                skipped += 1
                continue
            if _W["expected"]:
                r = run_expected(run)
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
    return uni, bi, tri, sents, skipped


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
    ap.add_argument("--mw", action="store_true", help="S2w：用 MediaWiki 的 zh-tw 轉換（zhconv-rs ＋ $S2_WORK/mwdata.json），取代 convert()")
    ap.add_argument("--extra-lexicon", action="append", default=[], metavar="FILE", help="model-v5：疊加層格式（讀音\\t詞\\t分數\\t來源）的檔加進斷詞詞庫，可給多次")
    ap.add_argument("--shard", metavar="K/N", help="只數第 i 篇（i %% N == K）、寫 counts-<篇數>-shard<K>of<N>.pkl；N 個程序各給一個 K 平行跑，計數相加就是全部（SJ-LM-7：主程序合併是單核瓶頸）。預設關")
    ap.add_argument("--holdout-guard", nargs="*", default=None, metavar="ROWS", help="預設關。命中保留集句子的漢字段不計數（契約 holdout-refresh §8）；不給檔＝eval/holdout/ 底下全部 .txt")
    a = ap.parse_args()
    if a.expected and a.trigram:
        ap.error("--expected 不算 trigram")
    if a.extra_lexicon and not a.expected:
        ap.error("--extra-lexicon 只用在 --expected（加法計數，model-v5 契約 §8）")
    guard = guard_matcher(a.holdout_guard)
    load_conv()   # in the parent first: a missing or wrong OpenCC file stops here, not in every Pool worker's initializer (which respawns forever)
    os.makedirs(OUT, exist_ok=True)
    uni, bi, tri = collections.Counter(), collections.Counter(), collections.Counter()
    sents = arts = skipped = 0
    with mp.Pool(a.procs, initializer=_init, initargs=(a.trigram, a.expected, a.mw, tuple(dict.fromkeys(os.path.abspath(f) for f in a.extra_lexicon)), guard)) as pool:
        k, n = map(int, a.shard.split("/")) if a.shard else (0, 1)
        mine = (x for i, x in enumerate(articles(a.articles)) if i % n == k)
        for u, b, t, s_, k_ in pool.imap_unordered(_count, batches(mine)):
            uni.update(u); bi.update(b); tri.update(t); sents += s_; skipped += k_; arts += 200
            if arts % 10000 == 0:
                print(f"~{arts} articles, {sents} runs, {len(uni)} words, {len(bi)} bigrams", flush=True)
    arts = min(arts, a.articles) if not a.shard else len(range(k, a.articles, n))
    out = {"uni": uni, "bi": bi, "articles": arts, "runs": sents}
    if a.trigram:
        out["tri"] = tri
    name = f"counts-{a.articles}-shard{k}of{n}" if a.shard else f"counts-{arts}"
    pickle.dump(out, open(os.path.join(OUT, f"{name}{'-tri' if a.trigram else ''}.pkl"), "wb"))
    print(f"done: {arts} articles, {sents} runs, {sum(uni.values())} tokens, {len(uni)} types, {len(bi)} bigram types")
    if a.holdout_guard is not None:
        print(f"holdout-guard: skipped {skipped} runs")


if __name__ == "__main__":
    main()
