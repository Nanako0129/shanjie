"""S2n 第三次重建前的量測：輸出裡剩下的 着 爲 説 喫 各來自哪裡。
來源 × 句型（簡體句／繁體句）× 產生方式（詞組輸出、字表輸出、原文）。維基用第 300–2300 篇，口語用 Common Voice／Tatoeba 原句與 synth.txt。
"stage1" 是 convert 的第一段（詞組＋字表）；簡體句的 stage1 輸出再逐段套 TW_CHAR，和 convert 一致。
用法：python3 experiments/s2n/source_chars.py [維基篇數，預設 2000]"""
import bz2
import collections
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "s2"))
import build_counts as bc  # noqa: E402

phrase, char, maxp = bc.load_conv()
WATCH = "着爲説喫"


def segs(text, table, maxp_, chartab, first):
    """和 build_counts._longest 一樣的比對，但回傳 [(種類, 原文, 輸出)]。"""
    out, i, n = [], 0, len(text)
    while i < n:
        for L in range(min(first.get(text[i], 0), maxp_, n - i), 1, -1):
            w = table.get(text[i:i + L])
            if w:
                out.append(("phrase", text[i:i + L], w)); i += L; break
        else:
            c = chartab.get(text[i], text[i])
            out.append(("char" if c != text[i] else "orig", text[i], c)); i += 1
    return out


def first_index(table):
    f = {}
    for k in table:
        if len(k) > f.get(k[0], 0):
            f[k[0]] = len(k)
    return f


FP, FS = first_index(phrase), first_index(bc.SIMP_PHRASE)
tally = collections.Counter()


def count(src, s):
    simp = bc.is_simplified(s)
    sg = segs(s, bc.SIMP_PHRASE, bc.SIMP_MAXP[0], bc.SIMP_CHAR, FS) if simp else segs(s, phrase, maxp, char, FP)
    for kind, a, b in sg:
        if simp:
            b = "".join(bc.TW_CHAR.get(c, c) for c in b)
        for c in b:
            if c in WATCH:
                tally[(src, "simplified" if simp else "traditional", kind, c)] += 1


def lines_colloquial():
    d = os.path.join(bc.SRC, "colloquial")
    for f in sorted(os.listdir(d)):
        if f.startswith("cv-"):
            for l in open(os.path.join(d, f), encoding="utf-8"):
                yield l.rstrip("\n")
    for l in bz2.open(os.path.join(d, "cmn_sentences.tsv.bz2"), "rt", encoding="utf-8"):
        yield l.rstrip("\n").split("\t")[-1]
    for l in open(os.path.expanduser("~/.cache/shanjie/work/s2/synth.txt"), encoding="utf-8"):
        yield l.rstrip("\n")


for l in lines_colloquial():
    count("colloquial", l)
N = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
for raw in list(bc.articles(300 + N))[300:]:
    t = raw.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").replace("&quot;", '"')
    for _ in range(3):
        t = bc.TEMPLATE.sub("", t)
    for pat, rep in bc.MARKUP:
        t = pat.sub(rep, t)
    for s in bc.SENT.findall(t):
        count("wiki", s)
print("source sentence-type kind char count")
for k, v in sorted(tally.items(), key=lambda kv: (kv[0][3], -kv[1])):
    print(*k, v)
for c in WATCH:
    tot = sum(v for k, v in tally.items() if k[3] == c)
    print(f"TOTAL {c} {tot}")
