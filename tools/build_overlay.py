"""S1：由鎖定版本的維基標題 dump 產生詞庫疊加層 data/lexicon/overlay-add.tsv。

契約見 docs/PLAN.md §2 S1；這支腳本就是契約的參考實作。輸出以 CC BY-SA 4.0 釋出（LICENSES/data.md）。
用法：python3 tools/build_overlay.py          產生並寫入
      python3 tools/build_overlay.py --check  重新產生並和 repo 裡的檔案比對，不同就 exit 1
來源下載到 ~/.cache/shanjie/sources/，SHA-256 不符就中止。
"""
import argparse
import gzip
import hashlib
import os
import re
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "reference", "proto"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "experiments", "s2"))
import build_counts as bc  # noqa: E402  S2n：簡體句判斷與轉換
import build_sandhi  # noqa: E402
import ime  # noqa: E402
import readings  # noqa: E402  萌典路徑

BASE = os.path.join(ROOT, "data", "lexicon", "mcbpmf-data.txt")
OUT = os.path.join(ROOT, "data", "lexicon", "overlay-add.tsv")
REMOVED = os.path.join(ROOT, "experiments", "s2n", "overlay-removed.tsv")   # S2n：被拿掉的簡體寫法（詞、轉換後的寫法、讀音）
VARIANT_REMOVED = os.path.join(ROOT, "experiments", "s2f", "overlay-variant-removed.tsv")   # S2f §2.5：被拿掉的異體寫法（詞、基底寫法、讀音）
CACHE = os.path.expanduser("~/.cache/shanjie/sources")
DUMPS = "https://dumps.wikimedia.org"
SOURCES = {  # 檔名: (網址, SHA-256)；Wikimedia 2026-10-01 dump 的 sha1 已和官方 sha1sums.txt 比對過
    "zhwiki": (f"{DUMPS}/zhwiki/20261001/zhwiki-20261001-all-titles-in-ns0.gz",
               "016e97bf584196a0eb0522ab9b6300ffccae6fb64d2188bd81a53182a28d6d67"),
    "enwiktionary": (f"{DUMPS}/enwiktionary/20261001/enwiktionary-20261001-all-titles-in-ns0.gz",
                     "1c840da0ddb78eed78e6f1b6fe613c1a2eced952801fd7d676f4dac9e581d4fd"),
    "zhwiktionary": (f"{DUMPS}/zhwiktionary/20261001/zhwiktionary-20261001-all-titles-in-ns0.gz",
                     "95e915cd85992b4fe990187dca845ea85e257deba39d8054b014781015d3f7f0"),
}
SCORE = {2: -7.17149945, 3: -7.04116568, 4: -6.60980192}   # 基底同字數詞條分數的第 25 百分位；build() 會重算核對
# 變調列的分數＝主要列 − 這個值（log10，約 1/3）。變調列常和別的詞共用讀音（同一成語的異體寫法、罕見詞），
# 同分時由檔案順序決定誰排第一；降一點讓「已經有這個讀音的詞」贏，變調列只在沒有競爭者時才排第一。
VARIANT_PENALTY = 0.5
HAN = re.compile(r"^[一-鿿]{2,4}$")


def fetch(name):
    url, sha = SOURCES[name]
    path = os.path.join(CACHE, name)
    if not os.path.exists(path):
        os.makedirs(CACHE, exist_ok=True)
        urllib.request.urlretrieve(url, path + ".part")
        os.replace(path + ".part", path)
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    if h.hexdigest() != sha:
        sys.exit(f"SHA-256 mismatch for source {name}; delete {path} to refetch, or the dump changed upstream")
    return path


HE4_PREV = set("唱倡附應酬賡")   # 「和」前一字是這些 → ㄏㄜˋ（唱和、附和）；其他 ㄏㄢˋ → ㄏㄜˊ（疊加層是詞條標題，多為人名地名）
HE4_ANY = set("唱倡")            # 契約 §1 的例子「一唱百和」「一倡一和」讀 ㄏㄜˋ，但「和」前一字是百、一：「和」前面任何位置有唱、倡也算


def normalize(word, syls):
    """S2r-2 §1.1：to_syllables 猜出來的讀音改回審訂表的本調（一 ㄧ、不 ㄅㄨˋ、法 ㄈㄚˇ、和 ㄏㄢˋ→ㄏㄜˊ／ㄏㄜˋ）。"""
    out = list(syls)
    for i, ch in enumerate(word):
        if ch == "一" and out[i] in ("ㄧˊ", "ㄧˋ"):
            out[i] = "ㄧ"
        elif ch == "不" and out[i] == "ㄅㄨˊ":
            out[i] = "ㄅㄨˋ"
        elif ch == "法" and out[i] == "ㄈㄚˋ":
            out[i] = "ㄈㄚˇ"
        elif ch == "和" and out[i] == "ㄏㄢˋ" and word[i + 1:i + 2] in ("麵", "麪", "泥"):
            out[i] = "ㄏㄨㄛˊ"                      # 審訂表：和麵、和泥
        elif ch == "和" and out[i] == "ㄏㄢˋ":
            out[i] = "ㄏㄜˋ" if i > 0 and (word[i - 1] in HE4_PREV or HE4_ANY & set(word[:i])) else "ㄏㄜˊ"
    return out


def sandhi_variant(word, syls):
    """§1.2：主要列的「一」「不」照 build_sandhi.other_reading 換成變調；沒有任何位置換就回 None。"""
    out = list(syls)
    for i, ch in enumerate(word):
        if ch in "一不":
            out[i] = build_sandhi.other_reading(word, syls, i) or out[i]
    return out if out != list(syls) else None


def moe_titles():
    import json
    return {e["title"] for e in json.load(open(readings.MOEDICT, encoding="utf-8")) if e.get("title")}


def build():
    base = ime.Lexicon(BASE)
    words = set(base.by_word)                       # 基底詞表＝解析後（套用 S0 的行過濾）的詞，不分讀音
    chars = {w for w in words if len(w) == 1}
    multi = {w for w in words if len(w) >= 2}
    for n, want in SCORE.items():
        got = sorted(s for syls, ents in base.by_reading.items() if len(syls) == n for _, s in ents)
        assert got[len(got) // 4] == want, (n, got[len(got) // 4])
    conv = bc.load_conv()
    simp_only = bc.TRAD_SIMP_ONLY                   # S2f §2.2：和繁體句同一個縮小後的簡體專用字集合（不含台灣字形：床 灶 粽 秘 庄…）

    def titles(name):
        out = set()
        with gzip.open(fetch(name), "rt", encoding="utf-8") as f:
            for line in f:
                t = line.rstrip("\n")
                if HAN.match(t) and t not in words and all(c in chars for c in t) and not any(c in simp_only for c in t):
                    out.add(t)
        return out

    wikt = titles("enwiktionary") | titles("zhwiktionary")
    compounds = {t for t in titles("zhwiki") if t[:-1] in multi or t[1:] in multi}
    rows, removed, variant_removed = [], [], []
    cand = wikt | compounds
    moe = moe_titles()
    fold = {**bc.MERGE, **bc.TW_VARIANTS}           # S2f §2.5：套到兩邊後字串相同，就是同一個詞的異體寫法
    fold_w = lambda t: "".join(fold.get(c, c) for c in t)
    # 第一輪：哪些詞條符合 (i)(iii)，目標寫法是什麼；第二輪由落點決定（修訂二 7.2.4：避免 A→B 而 B 不在了）
    target = {}
    for w in cand:
        if bc.is_simplified(w) and w not in words and w not in moe:
            w2 = bc.convert(w, *conv)
            if w2 != w:
                target[w] = w2
    info = {}                                       # 詞 → (讀音, 同讀音的基底異體詞)
    for w in sorted(cand):
        syls = base.to_syllables(w)
        if syls is not None:
            syls = normalize(w, syls)
            info[w] = (syls, next((b for b, _ in base.by_reading[tuple(syls)] if fold_w(b) == fold_w(w)), None))
    # S2f 修訂二 7.2.3／7.2.4，分兩階段（S2n 與延伸規則可能方向相反：占中 經簡體句轉換是 佔中，佔中 的 fold 是 占中，
    # 互相指向會繞圈）：第一階段只用 S2n 與原 §2.5 決定拿掉誰；第二階段對留下的詞套延伸規則；落點最後照完整的規則算。
    def first(x):
        """第一階段：('s2n'|'twin'|None)。S2n 在目標有落點時成立：目標在基底，或有讀音（有讀音的詞不論留下或被拿掉都有落點）。"""
        if x in target and (target[x] in words or target[x] in info):
            return "s2n"
        return "twin" if info[x][1] else None

    why = {x: first(x) for x in info}
    kept1 = {x for x, y in why.items() if y is None}
    for x in sorted(kept1):
        f = fold_w(x)
        if f != x and (any(b == f for b, _ in base.by_reading[tuple(info[x][0])]) or (f in kept1 and info[f][0] == info[x][0])):
            why[x] = "fold"
    memo = {}

    def landing(x, busy=frozenset()):
        """落點：基底或留下→自己；S2n→目標的落點；原 §2.5→基底異體詞；延伸→fold 的落點。"""
        if x in words or why.get(x) is None:
            return x
        if x in memo:
            return memo[x]
        assert x not in busy, x                     # 落點不能繞圈
        y = why[x]
        to = info[x][1] if y == "twin" else landing(target[x] if y == "s2n" else fold_w(x), busy | {x})
        memo[x] = to
        return to

    for w in sorted(cand):                          # 依詞的 code point 排序；核心照檔案順序接在基底後面
        if w not in info:
            continue
        syls = info[w][0]
        y, to = why[w], landing(w)
        # S2n §2.2：簡體寫法拿掉（簡體句轉換會變成另一個寫法、本身不在基底也不是萌典詞目），替代寫法記目標的落點
        if y == "s2n":
            removed.append(f"{w}\t{to}\t{'-'.join(syls)}\n")
            continue
        if y:                                       # S2f §2.5 與修訂二的延伸：只是基底詞或留下的疊加層詞的異體寫法，就拿掉
            variant_removed.append(f"{w}\t{to}\t{'-'.join(syls)}\n")
            continue
        src = "wikt" if w in wikt else "zhwiki"
        rows.append(f"{'-'.join(syls)}\t{w}\t{SCORE[len(w)]!r}\t{src}\n")
        var = sandhi_variant(w, syls)
        if var:                                     # 主要列在前，變調列緊接其後（同來源，分數 − VARIANT_PENALTY）
            rows.append(f"{'-'.join(var)}\t{w}\t{round(SCORE[len(w)] - VARIANT_PENALTY, 8)!r}\t{src}\n")
    assert not {r.split("\t")[1] for r in rows} & words   # 疊加層和基底的詞表交集必須是 0
    return rows, removed, variant_removed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    rows, removed, variant_removed = build()
    if ap.parse_args().check:
        same = (open(OUT, encoding="utf-8").readlines() == rows and open(REMOVED, encoding="utf-8").readlines() == removed
                and open(VARIANT_REMOVED, encoding="utf-8").readlines() == variant_removed)
        print(f"{len(rows)} rows, {len(removed)} removed, {len(variant_removed)} variant-removed; {'matches' if same else 'DIFFERS FROM'} the files in the repo")
        sys.exit(0 if same else 1)
    os.makedirs(os.path.dirname(VARIANT_REMOVED), exist_ok=True)
    for path, lines in ((OUT, rows), (REMOVED, removed), (VARIANT_REMOVED, variant_removed)):
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            f.writelines(lines)
        os.replace(path + ".tmp", path)
    print(f"wrote {len(rows)} rows to {os.path.relpath(OUT, ROOT)}, {len(removed)} removed rows to {os.path.relpath(REMOVED, ROOT)}, {len(variant_removed)} variant-removed rows to {os.path.relpath(VARIANT_REMOVED, ROOT)}")


if __name__ == "__main__":
    main()
