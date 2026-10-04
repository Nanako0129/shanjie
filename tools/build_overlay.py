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
import build_sandhi  # noqa: E402
import ime  # noqa: E402

BASE = os.path.join(ROOT, "data", "lexicon", "mcbpmf-data.txt")
OUT = os.path.join(ROOT, "data", "lexicon", "overlay-add.tsv")
CACHE = os.path.expanduser("~/.cache/shanjie/sources")
DUMPS = "https://dumps.wikimedia.org"
SOURCES = {  # 檔名: (網址, SHA-256)；Wikimedia 2026-10-01 dump 的 sha1 已和官方 sha1sums.txt 比對過
    "zhwiki": (f"{DUMPS}/zhwiki/20261001/zhwiki-20261001-all-titles-in-ns0.gz",
               "016e97bf584196a0eb0522ab9b6300ffccae6fb64d2188bd81a53182a28d6d67"),
    "enwiktionary": (f"{DUMPS}/enwiktionary/20261001/enwiktionary-20261001-all-titles-in-ns0.gz",
                     "1c840da0ddb78eed78e6f1b6fe613c1a2eced952801fd7d676f4dac9e581d4fd"),
    "zhwiktionary": (f"{DUMPS}/zhwiktionary/20261001/zhwiktionary-20261001-all-titles-in-ns0.gz",
                     "95e915cd85992b4fe990187dca845ea85e257deba39d8054b014781015d3f7f0"),
    "STCharacters": ("https://raw.githubusercontent.com/BYVoid/OpenCC/3ac34aa439a9908dd49fa92b5174b46314787ac2/data/dictionary/STCharacters.txt",
                     "a0ca1601c70648cf48b33c3c6210ccbecc5c7eead4b4c3daf76587ba2c03582b"),
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


def build():
    base = ime.Lexicon(BASE)
    words = set(base.by_word)                       # 基底詞表＝解析後（套用 S0 的行過濾）的詞，不分讀音
    chars = {w for w in words if len(w) == 1}
    multi = {w for w in words if len(w) >= 2}
    for n, want in SCORE.items():
        got = sorted(s for syls, ents in base.by_reading.items() if len(syls) == n for _, s in ents)
        assert got[len(got) // 4] == want, (n, got[len(got) // 4])
    simp_only = set()                               # 簡體字，且它的繁體對應裡不含自己（例：「干」對應含「干」，不算）
    for line in open(fetch("STCharacters"), encoding="utf-8"):
        p = line.rstrip("\n").split("\t")
        if len(p) == 2 and p[0] not in p[1].split(" "):
            simp_only.add(p[0])

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
    rows = []
    for w in sorted(wikt | compounds):              # 依詞的 code point 排序；核心照檔案順序接在基底後面
        syls = base.to_syllables(w)
        if syls is None:
            continue
        syls = normalize(w, syls)
        src = "wikt" if w in wikt else "zhwiki"
        rows.append(f"{'-'.join(syls)}\t{w}\t{SCORE[len(w)]!r}\t{src}\n")
        var = sandhi_variant(w, syls)
        if var:                                     # 主要列在前，變調列緊接其後（同來源，分數 − VARIANT_PENALTY）
            rows.append(f"{'-'.join(var)}\t{w}\t{round(SCORE[len(w)] - VARIANT_PENALTY, 8)!r}\t{src}\n")
    assert not {r.split("\t")[1] for r in rows} & words   # 疊加層和基底的詞表交集必須是 0
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    rows = build()
    if ap.parse_args().check:
        same = open(OUT, encoding="utf-8").readlines() == rows
        print(f"{len(rows)} rows; {'matches' if same else 'DIFFERS FROM'} {os.path.relpath(OUT, ROOT)}")
        sys.exit(0 if same else 1)
    with open(OUT + ".tmp", "w", encoding="utf-8") as f:
        f.writelines(rows)
    os.replace(OUT + ".tmp", OUT)
    print(f"wrote {len(rows)} rows to {os.path.relpath(OUT, ROOT)}")


if __name__ == "__main__":
    main()
