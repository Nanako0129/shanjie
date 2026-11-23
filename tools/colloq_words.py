#!/usr/bin/env python3
"""口語變調的詞表（docs/spec/decoder.md §8.7）：哪些詞的哪個位置有口語讀音。辭典只用來決定這件事；出貨的讀音由
tools/build_sandhi.py、tools/build_overlay.py 的規則從詞庫自己的讀音推出，這兩份表不含辭典的注音字串與釋義。

  colloq_words.py            -> tools/colloq-words.tsv：疊字陰平化的詞（規則 1 裡有 level 位置的詞），詞庫的 colloq 列由它決定
                                eval/probe/colloq-probe-words.tsv：口語變調探針的詞表（規則 1、2、3 全部；tools/build_probe.py --colloq 用，不進詞庫）
                                兩份的欄位：詞、位置（從 1 起）、改法 neutral／level、來源，依（詞, 位置）的 UTF-8 位元組序
  colloq_words.py --check    -> 重算並和現有的兩份檔逐位元組比對，不寫檔

來源（兩份都釘 SHA-256，不符就中止；都不隨 repo 散布）：
  - 中華民國教育部（Ministry of Education, R.O.C.）。《重編國語辭典修訂本》（版本編號：dict_revised_2015_20260625）網址：http://dict.revised.moe.edu.tw/
    試算表 dict_revised_1.xlsx，路徑用 SHANJIE_REVISED 指定（預設在快取資料夾）。
  - 中華民國教育部（Ministry of Education, R.O.C.）。《國語辭典簡編本》（版本編號：dict_concised_2014_20260929）網址：http://dict.concised.moe.edu.tw/
    tools/build_overlay.py 的 CONCISED。
兩部都是創用 CC 姓名標示-禁止改作 3.0 臺灣；使用說明全文在 LICENSES/。

規則：
  1.「修訂本變」：變體類型是「變」、變體注音不含「ㄦ」的列，逐音節比主讀音與變體：變體是同韻的輕聲 → neutral；變體是同韻的陰平（沒有聲調符號）→ level；
     「一」「不」只差聲調的位置不算（詞庫自己有變調列）。有任何其他差異（韻不同、別的聲調、音節數不同）的列整列不收。
  2.「修訂本主讀音＋簡編本」：基底的多字詞，修訂本某一列的主讀音有輕聲、基底沒有這個（讀音, 詞）、而且和基底的某個讀音只差聲調；
     再要求那個讀音的每個輕聲位置，在簡編本同一個詞的某一列（注音一式，或變體類型是「變」的變體注音；音節數相同）的同一個位置是同一個輕聲音節。
     位置＝那個讀音的輕聲位置。
  3. 名詞「不是」：「派不是」在規則 1；「不是」「賠不是」比照它（最後一個音節 neutral），來源寫「修訂本變（比照派不是）」。
  詞庫（基底與疊加層）沒有的詞不收。
  只改讀輕聲的詞（規則 1 裡沒有 level 位置的、規則 2、規則 3）不寫進詞庫：核心的輕聲來源照規則讀到它們（docs/spec/decoder.md §6.6），
  這裡只拿來建探針。
"""
import argparse
import collections
import io
import os
import re
import sys
import xml.etree.ElementTree as ET
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import build_overlay as bo  # noqa: E402
import build_sandhi  # noqa: E402

OUT = os.path.join(ROOT, "tools", "colloq-words.tsv")
PROBE = os.path.join(ROOT, "eval", "probe", "colloq-probe-words.tsv")
REVISED_VERSION = "dict_revised_2015_20260625"
CONCISED_VERSION = "dict_concised_2014_20260929"
REVISED_SHA256 = "df94ae4384ae3f33f573ded5c2f142041ea7530d381a285163593d6252ea4a9a"
REVISED = os.environ.get("SHANJIE_REVISED") or os.path.join(bo.CACHE, "revised", "dict_revised_1.xlsx")
SRC_VARIANT, SRC_MAIN, SRC_BU = "修訂本變", "修訂本主讀音＋簡編本", "修訂本變（比照派不是）"
BU_WORDS = ("不是", "賠不是")     # 規則 3
MARKS = "ˊˇˋ˙"
REVISED_LINES = (f"# 中華民國教育部（Ministry of Education, R.O.C.）。《重編國語辭典修訂本》（版本編號：{REVISED_VERSION}）網址：http://dict.revised.moe.edu.tw/\n",
                 f"# dict_revised_1.xlsx sha256 {REVISED_SHA256}\n")
CONCISED_LINES = (f"# 中華民國教育部（Ministry of Education, R.O.C.）。《國語辭典簡編本》（版本編號：{CONCISED_VERSION}）網址：http://dict.concised.moe.edu.tw/\n",
                  f"# dict_concised_2014_20260929.zip sha256 {bo.CONCISED_SHA256}\n")
LICENSE_LINE = "# 創用 CC 姓名標示-禁止改作 3.0 臺灣；只用於決定哪些詞的哪個位置有口語讀音，辭典的注音字串與釋義不在檔內（LICENSES/data.md）\n"
COLUMNS = "# 詞\t位置\t改法\t來源\n"
HEADER = "# python3 tools/colloq_words.py\n" + REVISED_LINES[0] + LICENSE_LINE + REVISED_LINES[1] + COLUMNS                 # tools/colloq-words.tsv：只用修訂本
PROBE_HEADER = ("# python3 tools/colloq_words.py\n" + REVISED_LINES[0] + CONCISED_LINES[0] + LICENSE_LINE + REVISED_LINES[1] + CONCISED_LINES[1] + COLUMNS)


def rime(s):
    return s.rstrip(MARKS)


def neu(s):
    return s.endswith("˙")


def syls(s):
    """辭典的注音欄 → 音節 list（括號裡的標記去掉；輕聲寫在音節前，轉成 repo 的寫法）。"""
    return bo.concised_syls(re.sub(r"[（(][^）)]*[）)]", "", s or ""))


def xlsx_rows(x):
    """xlsx（zipfile.ZipFile）第一張表的每一列 → {表頭名稱: 值}；只用標準庫。"""
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    strings = ["".join(t.text or "" for t in [*si.findall(f"{ns}t"), *si.findall(f"{ns}r/{ns}t")]) for si in ET.fromstring(x.read("xl/sharedStrings.xml"))]
    head = None
    with x.open("xl/worksheets/sheet1.xml") as f:
        for _, el in ET.iterparse(f):
            if el.tag != ns + "row":
                continue
            cells = {}
            for c in el:
                v = c.find(ns + "v")
                if v is not None:
                    cells[re.match(r"[A-Z]+", c.get("r")).group()] = strings[int(v.text)] if c.get("t") == "s" else v.text
            el.clear()
            if head is None:
                head = cells
                continue
            yield {name: cells.get(col, "") for col, name in head.items()}


def dict_rows(x):
    """(詞, 主讀音, 變體類型, 變體注音) 每列；欄位照表頭開頭找（修訂本與簡編本的欄名相同）。"""
    for r in xlsx_rows(x):
        col = lambda k: next((v for n, v in r.items() if n.startswith(k)), None)
        if None in (col("字詞名"), col("注音一式"), col("變體類型"), col("變體注音")):
            sys.exit("the dictionary has no 字詞名／注音一式／變體類型／變體注音 column; the file is not the pinned one")
        yield col("字詞名").strip(), syls(col("注音一式")), col("變體類型").strip(), col("變體注音")


def load_revised(path=None):
    path = path or REVISED
    bo.check_sha(path, REVISED_SHA256, "the revised dictionary")
    return list(dict_rows(zipfile.ZipFile(path)))


def load_concised(path=None):
    path = path or bo.CONCISED
    bo.check_sha(path, bo.CONCISED_SHA256, "the concised dictionary")
    with zipfile.ZipFile(path) as z:
        return list(dict_rows(zipfile.ZipFile(io.BytesIO(z.read(next(n for n in z.namelist() if n.endswith(".xlsx")))))))


def variant_changes(word, main, var):
    """規則 1 的一列：[(位置（從 0 起）, 改法)]；整列不收回 None（沒有差異也是 None）。"""
    if len(main) != len(var) or len(main) != len(word):
        return None
    out = []
    for i, (a, b) in enumerate(zip(main, var)):
        if a == b or (word[i] in "一不" and rime(a) == rime(b) and not neu(b)):
            continue
        if rime(a) != rime(b):
            return None
        if neu(b):
            out.append((i, "neutral"))
        elif b == rime(a):
            out.append((i, "level"))
        else:
            return None
    return out or None


def concised_agrees(reading, concised_readings):
    """規則 2 的交集條件：reading 的每個輕聲位置，在 concised_readings（同一個詞在簡編本的讀音們）某個音節數相同的讀音的同一位置是同一個音節。"""
    return all(any(len(c) == len(reading) and c[i] == s for c in concised_readings) for i, s in enumerate(reading) if neu(s))


def main_groups(revised, base):
    """規則 2 取交集前的組：{(詞, 讀音): 音節 list}。base：詞 → [讀音]（基底的多字詞）。"""
    out = {}
    for w, r, _, _ in revised:
        if len(w) < 2 or w not in base or len(r) != len(w) or not any(neu(s) for s in r) or r in base[w]:
            continue
        if any(all(rime(a) == rime(c) for a, c in zip(b, r)) for b in base[w]):
            out[(w, "-".join(r))] = r
    return out


def build(revised, concised, base, overlay_words):
    """→ {(詞, 位置（從 0 起）): (改法, 來源)}，以及報告用的計數。"""
    out, count = {}, collections.Counter()

    def put(w, i, change, src):
        if out.get((w, i), (change,))[0] != change:
            sys.exit(f"two rules give position {i + 1} of one word different changes")
        out.setdefault((w, i), (change, src))

    for w, main, kind, var in revised:
        if kind != "變":
            continue
        count["variant rows"] += 1
        if "ㄦ" in var:
            continue
        count["variant rows without ㄦ"] += 1
        ch = variant_changes(w, main, syls(var))
        if ch is None or (w not in base and w not in overlay_words):
            continue
        count["variant words"] += 1
        for i, c in ch:
            put(w, i, c, SRC_VARIANT)
    by_word = collections.defaultdict(list)
    for w, main, kind, var in concised:
        by_word[w].append(main)
        if kind == "變" and syls(var):
            by_word[w].append(syls(var))
    groups = main_groups(revised, base)
    count["main groups"] = len(groups)
    count["main groups whose word the concised dictionary has"] = sum(w in by_word for w, _ in groups)
    for (w, _), r in sorted(groups.items()):
        if concised_agrees(r, by_word.get(w, [])):
            count["main groups kept"] += 1
            for i, s in enumerate(r):
                if neu(s):
                    put(w, i, "neutral", SRC_MAIN)
    for w in BU_WORDS:
        if w in base:
            put(w, len(w) - 1, "neutral", SRC_BU)
    return out, count


def level_words(table):
    """詞庫的 colloq 列用的那一部分：有 level 位置的詞（疊字陰平化），它的每個位置都留著（同時標輕聲的音節照改）。"""
    keep = {w for (w, _), (c, _) in table.items() if c == "level"}
    return {k: v for k, v in table.items() if k[0] in keep}


def render(table, header=HEADER):
    rows = sorted(table.items(), key=lambda kv: (kv[0][0].encode(), kv[0][1]))
    return header + "".join(f"{w}\t{i + 1}\t{c}\t{src}\n" for (w, i), (c, src) in rows)


def load(path=OUT):
    """已提交的詞表 → {詞: [(位置（從 0 起）, 改法)]}（位置由小到大）。格式不對就中止。生成器與探針共用。"""
    out = collections.defaultdict(list)
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if line.startswith("#"):
                continue
            p = line.rstrip("\n").split("\t")
            if len(p) != 4 or not p[1].isdigit() or not 1 <= int(p[1]) <= len(p[0]) or p[2] not in ("neutral", "level") or p[3] not in (SRC_VARIANT, SRC_MAIN, SRC_BU):
                sys.exit(f"{os.path.basename(path)} line {n}: expected word<TAB>position<TAB>neutral|level<TAB>source")
            out[p[0]].append((int(p[1]) - 1, p[2]))
    return {w: sorted(v) for w, v in out.items()}


def lexicon():
    base = collections.defaultdict(list)
    for word, s, _, _ in build_sandhi.base_rows():
        if len(word) > 1:
            base[word].append(s)
    with open(build_sandhi.OVERLAY, encoding="utf-8") as f:
        overlay = {l.split("\t")[1] for l in f if l.strip()}
    return base, overlay


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="重算並和現有檔逐位元組比對，不寫檔")
    a = ap.parse_args()
    table, count = build(load_revised(), load_concised(), *lexicon())
    outputs = ((OUT, render(level_words(table))), (PROBE, render(table, PROBE_HEADER)))
    print(dict(count), "probe rows", len(table), "words", len({w for w, _ in table}), "of them with a level position", len({w for w, _ in level_words(table)}), file=sys.stderr)
    for path, text in outputs:
        if a.check:
            if not os.path.exists(path) or open(path, encoding="utf-8").read() != text:
                sys.exit(f"error: {os.path.relpath(path, ROOT)} differs from a fresh build")
            print(f"{os.path.basename(path)}: up to date", file=sys.stderr)
        else:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)


if __name__ == "__main__":
    main()
