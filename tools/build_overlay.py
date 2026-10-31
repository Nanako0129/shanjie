"""S1：由鎖定版本的維基標題 dump 產生詞庫疊加層 data/lexicon/overlay-add.tsv。

契約見 docs/PLAN.md §2 S1；這支腳本就是契約的參考實作。輸出以 CC BY-SA 4.0 釋出（LICENSES/data.md）。
用法：python3 tools/build_overlay.py          產生並寫入
      python3 tools/build_overlay.py --check  重新產生並和 repo 裡的檔案比對，不同就 exit 1
來源下載到 ~/.cache/shanjie/sources/，SHA-256 不符就中止。

SJ-LM-7 契約 §2.1a、§2.3（都預設關，不加旗標時輸出和原來逐位元組相同）：
      --moedict-readings          讀音依序：教育部《國語辭典簡編本》（CONCISED，SHANJIE_CONCISED 可改路徑）→ 萌典第一讀音 → tools/readings.py 的規則（Reader，規則 3b 開）；
                                  萌典路徑見 readings.MOEDICT（SHANJIE_MOEDICT）。簡編本的「變」輕聲與「語音」另寫第二列；讀音有輕聲的詞另寫輕聲換成本調的一列（排在前面）；
                                  詞裡有日文新字體（OpenCC JPShinjitaiCharacters.txt，字不在簡編本與萌典任何詞條）的詞不寫，記進 overlay-jp-removed.tsv。
                                  readings.py 判 CHECK（有歧義）或 GAP 的詞保留原本的讀音，印出個數；異體判斷用原讀音與新讀音兩個（任一個撞到就算）
      --reading-report PATH       （搭配上一個）把讀音和原本方法（基底最佳讀音＋normalize）不同的詞，以及 CHECK／GAP 而保留原讀音、暫定讀音不同的詞寫到 PATH：詞、原讀音、新讀音、依據
      --moedict-supplement FILE…  來源「萌典補詞」：萌典有收、不在基底、疊加層、名單、任何詞包，而且在這些純文字檔（一行一段，.gz／.bz2 照副檔名）裡
                                  子字串出現 ≥ 2 次的詞；讀音＝萌典第一讀音，分數＝同字數的 length_scores（不加手動詞的加分）。
                                  先過和維基標題候選相同的過濾（每個字是基底單字詞、不含簡體專用字），再和它們一起走異體判斷（臺／台 這類
                                  同讀音異體寫法拿掉，記進 overlay-variant-removed.tsv）
      --jobs N                    萌典補詞數子字串用幾個程序
"""
import argparse
import bz2
import collections
import glob
import gzip
import hashlib
import io
import math
import os
import multiprocessing
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

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
MANUAL = os.path.join(ROOT, "data", "lexicon", "overlay-manual.tsv")   # 維護者手動加的詞，接在產生的列後面（來源 manual）
SLANG = os.path.join(ROOT, "data", "lexicon", "slang-add.tsv")   # 網路用語（docs/contracts/net-slang.md），接在手動詞後面（來源 slang）；沒有這個檔時輸出和以前相同
PACKS = os.path.join(ROOT, "data", "packs")
NAMES = os.path.join(ROOT, "data", "lexicon", "names-add.tsv")
SUPPLEMENT_SRC = "萌典補詞"
SUPPLEMENT_MIN = 2   # 契約 §2.1a：語料裡子字串出現的次數下限
PACK = os.path.join(ROOT, "data", "packs", "acg-add.tsv")   # 手動詞也不能是詞包的詞：引擎把詞包接在疊加層後面，同詞同讀音重複就整個載入失敗
IT_PACK = os.path.join(ROOT, "data", "packs", "it-add.tsv")
MEME_PACK = os.path.join(ROOT, "data", "packs", "meme-add.tsv")   # 迷因詞包（#293）；沒有這個檔時是空集合
# 手動詞的分數＝同字數的 length_scores ＋ 這個值（log10）。2026-10-14 量的：書面設定下「米雅歌」輸給「米亞哥」0.25（加 0.25 仍輸，
# 加 0.5 時四個名字（五列）單獨打與放在句子裡，兩種設定 9 句都是第一名；研究紀錄同日）。手動加的是維護者明確要的詞，所以比一般疊加層詞高一點。
# model-v6 §2.8 規則 2（維護者 2026-10-14）：0.5 是 model-v5 上剛好過關的最小值；開 KN 的 model-v6 書面「米雅歌唱得很好」輸，改 0.75。
MANUAL_BONUS = 0.75
# canto-prune（docs/contracts/canto-prune.md，維護者 2026-10-14）：含這些粵語用字、又不是教育部國語辭典詞目（build() 本來就讀的
# moe_titles()）的標題不進候選。「俾」「咩」「喎」（喎斜）「嘞」（好嘞）國語也用，不在內；審查補了嗮 噏 揸 攞 餸 嚫 撳 搣 嗌 掟 喐 睩 嗱。
CANTO_CHARS = frozenset("冇嘅啲喺佢嘢唔咁嚟搵睇畀乜啱攰嗰哋嚿諗噉嗮噏揸攞餸嚫撳搣嗌掟喐睩嗱")
MANUAL_WORD = re.compile(r"^[一-鿿]+$")   # 純漢字，範圍和 S1 的 HAN 相同（U+4E00–U+9FFF），字數不限 2–4
REMOVED = os.path.join(ROOT, "experiments", "s2n", "overlay-removed.tsv")   # S2n：被拿掉的簡體寫法（詞、轉換後的寫法、讀音）
VARIANT_REMOVED = os.path.join(ROOT, "experiments", "s2f", "overlay-variant-removed.tsv")   # S2f §2.5：被拿掉的異體寫法（詞、基底寫法、讀音）
JP_REMOVED = os.path.join(ROOT, "experiments", "s2f", "overlay-jp-removed.tsv")   # 契約 §2.3 修訂四：被拿掉的日文寫法（詞、讀音、觸發字）；只有 --moedict-readings 產生
CACHE = os.path.expanduser("~/.cache/shanjie/sources")
# 契約 §2.3 修訂四：兩個建置依據，雜湊不符就中止（和 SOURCES 一樣不隨 repo 散布）。簡編本 CC BY-ND 3.0 臺灣：只取詞名與讀音；OpenCC 表 Apache-2.0：只過濾
CONCISED_SHA256 = "5bab99dac44ad7a6ccdcf481e770e8aca05ede7445c4cc25b8199e0e5df64cf2"
CONCISED = os.environ.get("SHANJIE_CONCISED") or os.path.join(CACHE, "concised", "dict_concised_2014_20260929.zip")
JP_SHA256 = "12cec7250b873ef52b36d8f92218d4f92c0aaf5d8cd7c58fe42d9785bdcdc43a"   # OpenCC commit 3ac34aa439a9908dd49fa92b5174b46314787ac2
JP = os.path.join(CACHE, "opencc", "JPShinjitaiCharacters.txt")
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


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def fetch(name):
    url, sha = SOURCES[name]
    path = os.path.join(CACHE, name)
    if not os.path.exists(path):
        os.makedirs(CACHE, exist_ok=True)
        urllib.request.urlretrieve(url, path + ".part")
        os.replace(path + ".part", path)
    if sha256_file(path) != sha:
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


def overlay_rows(w, syls, score, src):
    """一個詞的疊加層列：主要列在前，含「一」「不」的變調列緊接其後（同來源，分數 − VARIANT_PENALTY）。build_acg_pack 共用。"""
    rows = [f"{'-'.join(syls)}\t{w}\t{score!r}\t{src}\n"]
    var = sandhi_variant(w, syls)
    if var:
        rows.append(f"{'-'.join(var)}\t{w}\t{round(score - VARIANT_PENALTY, 8)!r}\t{src}\n")
    return rows


Info = collections.namedtuple("Info", "syls twin twin_reading tries extra")   # extra：第一個讀音 syls 之後同分的其他讀音們（--moedict-readings 才有）


def build(moedict_readings=False, reading_report=None, supplement=None, jobs=1):
    """supplement：None，或萌典補詞要數的純文字檔路徑清單。其他參數見模組說明；預設值＝原本的輸出。回 (rows, removed, variant_removed, jp_removed)。"""
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
    rows, removed, variant_removed, jp_removed = [], [], [], []
    moe = moe_titles()
    cand = {w for w in wikt | compounds if not cantonese(w, moe)}   # canto-prune：在候選階段拿掉，S2n／S2f 的落點不會指向被拿掉的詞
    fold = {**bc.MERGE, **bc.TW_VARIANTS}           # S2f §2.5：套到兩邊後字串相同，就是同一個詞的異體寫法
    fold_w = lambda t: "".join(fold.get(c, c) for c in t)
    # 第一輪：哪些詞條符合 (i)(iii)，目標寫法是什麼；第二輪由落點決定（修訂二 7.2.4：避免 A→B 而 B 不在了）
    target = {}
    for w in cand:
        if bc.is_simplified(w) and w not in words and w not in moe:
            w2 = bc.convert(w, *conv)
            if w2 != w:
                target[w] = w2
    moe_first = moe_primary_first() if (moedict_readings or supplement) else None
    concised = load_concised() if (moedict_readings or supplement) else None
    syllables = base_syllables(base)
    tones = base_tones() if concised is not None else None
    jp_drop = jp_drop_set({c for t in [*concised, *moe] for c in t}) if moedict_readings else frozenset()   # 日文新字體：字不在簡編本與萌典的任何詞條
    sup_cand = set()
    if supplement:                                  # 萌典補詞：先挑出來，再和維基標題一起走下面的異體／重複判斷（契約 §2.1a）
        # 候選的讀音＝簡編本 → 萌典（契約 §2.3 修訂四的順序）；音節條件對那個讀音檢查
        dict_first = {w: p.syls for w in moe_first if (p := dict_pick(w, concised, moe_first, syllables, fold_w)).syls}
        sup_cand = supplement_words(dict_first, words | cand | other_layers() | manual_words(), supplement, length_scores(base), syllables, jobs,
                                    chars=chars, simp_only=simp_only)
        cand |= sup_cand
    info = {}                                       # 詞 → Info(寫出的讀音, 同讀音的基底異體詞, 那個讀音, 異體判斷用的讀音們)
    pick = reading_picker(base, syllables, moe_first, concised, fold_w) if moedict_readings else None
    changed, kept_old, fell_same = [], set(), collections.Counter()
    for w in sorted(cand):
        syls = base.to_syllables(w)
        if syls is not None:
            syls = normalize(w, syls)
            old = syls                              # today's reading: the variant rules check it as well as the new one
            if hit := next((c for c in w if c in jp_drop), None):   # 日文寫法：先於字典查詢與正規化
                jp_removed.append(f"{w}\t{'-'.join(syls)}\t{hit}\n")
                continue
            extras = []                             # 字典來的第二列（簡編本的 變 輕聲、語音）
            if w in sup_cand:
                p = dict_pick(w, concised, moe_first, syllables, fold_w)   # 萌典補詞的讀音＝簡編本 → 萌典第一讀音（它沒有「現在的讀音」）
                syls = old = normalize(w, p.syls)
                extras = [e for e in (normalize(w, x) for x in p.extras) if e != syls]
            elif pick:
                p = pick(w)
                new = p.syls
                if p.how in ("CHECK", "GAP"):        # readings.py is not sure (CHECK) or its reading is not in the lexicon (GAP): the word keeps its reading
                    if new and new != syls:
                        changed.append(f"{w}\t{' '.join(syls)}\t{' '.join(new)}\t{p.how} (kept)\n")
                        kept_old.add(w)
                elif new:
                    if new != syls:                  # 只列寫出的讀音和現在不同的；退下來卻停在現在讀音的只計數
                        changed.append(f"{w}\t{' '.join(syls)}\t{' '.join(new)}\t{p.how}{f' ({p.fell})' if p.fell else ''}\n")
                    elif p.fell:
                        fell_same[p.fell] += 1
                    syls, extras = new, p.extras
            tone = None
            if pick or w in sup_cand:               # 第二列與輕聲的本調列（契約 §2.3 修訂四）；只有新讀音的來源才有，單獨開萌典補詞時維基標題照舊
                changed += [f"{w}\t{' '.join(syls)}\t{' '.join(v)}\t{tag}\n" for v, tag in p.listed]
                changed += [f"{w}\t{' '.join(syls)}\t{' '.join(e)}\t第二列（簡編本變／語音）\n" for e in extras]
                tone, how = tone_row(w, syls, tones, p.tone)
                if tone:
                    changed.append(f"{w}\t{' '.join(syls)}\t{' '.join(tone)}\t輕聲本調列（排在前；{how}）\n")
                elif how:
                    changed.append(f"{w}\t{' '.join(syls)}\t\t輕聲無本調（{how}，不寫第二列）\n")
            allr = ([tone] if tone else []) + [syls, *extras]
            tries = list(dict.fromkeys([*map(tuple, allr), tuple(old)]))   # the written readings first, then today's
            twin = next(((b, r) for r in tries for b, _ in base.by_reading[r] if fold_w(b) == fold_w(w)), (None, None))
            info[w] = Info(allr[0], twin[0], twin[1], tries, allr[1:])
    # S2f 修訂二 7.2.3／7.2.4，分兩階段（S2n 與延伸規則可能方向相反：占中 經簡體句轉換是 佔中，佔中 的 fold 是 占中，
    # 互相指向會繞圈）：第一階段只用 S2n 與原 §2.5 決定拿掉誰；第二階段對留下的詞套延伸規則；落點最後照完整的規則算。
    def first(x):
        """第一階段：('s2n'|'twin'|None)。S2n 在目標有落點時成立：目標在基底，或有讀音（有讀音的詞不論留下或被拿掉都有落點）。"""
        if x in target and (target[x] in words or target[x] in info):
            return "s2n"
        return "twin" if info[x].twin else None

    why = {x: first(x) for x in info}
    kept1 = {x for x, y in why.items() if y is None}
    for x in sorted(kept1):
        f = fold_w(x)
        if f != x and (any(b == f for r in info[x].tries for b, _ in base.by_reading[r]) or (f in kept1 and set(info[f].tries) & set(info[x].tries))):
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
        to = info[x].twin if y == "twin" else landing(target[x] if y == "s2n" else fold_w(x), busy | {x})
        memo[x] = to
        return to

    sup_rows, sup_scores = [], length_scores(base) if sup_cand else None
    for w in sorted(cand):                          # 依詞的 code point 排序；核心照檔案順序接在基底後面
        if w not in info:
            continue
        syls = info[w].syls
        y, to = why[w], landing(w)
        if y == "twin":                             # the removed-variant row records the reading the twin was found at
            syls = list(info[w].twin_reading)
        # S2n §2.2：簡體寫法拿掉（簡體句轉換會變成另一個寫法、本身不在基底也不是萌典詞目），替代寫法記目標的落點
        if y == "s2n":
            removed.append(f"{w}\t{to}\t{'-'.join(syls)}\n")
            continue
        if y:                                       # S2f §2.5 與修訂二的延伸：只是基底詞或留下的疊加層詞的異體寫法，就拿掉
            variant_removed.append(f"{w}\t{to}\t{'-'.join(syls)}\n")
            continue
        if w in sup_cand:                           # 分數＝同字數的 length_scores，沒有手動詞的加分；列接在產生的列後面、手動列前面
            sup_rows += word_rows(w, [syls, *info[w].extra], sup_scores[len(w)], SUPPLEMENT_SRC)
            continue
        src = "wikt" if w in wikt else "zhwiki"
        rows += word_rows(w, [syls, *info[w].extra], SCORE[len(w)], src)
    rows += sup_rows
    overlay_words = {r.split("\t")[1] for r in rows}
    assert not overlay_words & words                # 疊加層和基底的詞表交集必須是 0（手動詞由 manual_rows 自己檢查）
    rows += tail_rows(words | overlay_words, length_scores(base), syllables, rows)
    if pick:
        print(f"--moedict-readings: {len(kept_old)} words keep their reading because readings.py marks them CHECK or GAP"
              + (f" (listed in {reading_report})" if reading_report else " (give --reading-report to list them)"))
    if reading_report:
        with open(reading_report, "w", encoding="utf-8", newline="\n") as f:
            f.write("詞\t原讀音\t新讀音\t依據\n" + "".join(changed))
        print(f"reading report: {len(changed)} rows (readings that change, the CHECK and GAP words that keep theirs, the second rows and the base-tone rows), {reading_report}")
    if pick:
        print(f"--moedict-readings: {sum(fell_same.values())} words fell back to a later source (concised -> Moedict -> rules) and end on today's reading (not in the report): "
              + "; ".join(f"{k} {n}" for k, n in fell_same.most_common()))
        print(f"--moedict-readings: {len(jp_removed)} words with a Japanese-form character are not written (listed in {os.path.relpath(JP_REMOVED, ROOT)})")
    return rows, removed, variant_removed, jp_removed



TAIL_TAGS = ("manual", "slang", "colloq")   # 檔尾的三段，依這個順序


def rebuild_tails(out=OUT):
    """overlay-add.tsv 產生的列（標籤不是 TAIL_TAGS）照舊，檔尾照 tail_rows 重算；給只改了手動詞、網路用語或 colloq 規則的時候用，不必有 dump。"""
    with open(out, encoding="utf-8") as f:
        kept = [l for l in f if l.rstrip("\n").split("\t")[3] not in TAIL_TAGS]
    base = ime.Lexicon(BASE)
    return kept + tail_rows(set(base.by_word) | {l.split("\t")[1] for l in kept}, length_scores(base), base_syllables(base), kept)


def tail_rows(taken, scores, syllables, generated=(), manual=MANUAL, slang=SLANG):
    """overlay-add.tsv 的檔尾：手動詞（manual），網路用語（slang，檔案存在才有），再接 generated 裡含「幀」的列的 R7 口語讀音變體（colloq，
    docs/contracts/frame-reading.md；分數照抄來源，已有的（讀音, 詞）不產生）。網路用語也不能是名單層、兩個詞包或手動詞的詞。"""
    meme = pack_words(MEME_PACK) if os.path.exists(MEME_PACK) else set()
    taken = taken | pack_words() | pack_words(IT_PACK) | meme | names_words()   # 同詞同讀音和詞包、名單層重複，引擎整個載入失敗
    rows = manual_rows(taken, scores, syllables, manual)
    if os.path.exists(slang):
        rows += slang_behind(manual_rows(taken | {r.split("\t")[1] for r in rows}, scores, syllables, slang, "slang"), boosted_words(slang))
    seen = {tuple(r.split("\t")[:2]) for r in (*generated, *rows)}
    for r in generated:
        reading, word, score, _ = r.rstrip("\n").split("\t")[:4]
        for rule in build_sandhi.R7_RULES:
            new = rule(word, reading.split("-"))
            if new and ("-".join(new), word) not in seen:
                seen.add(("-".join(new), word))
                rows.append(f"{'-'.join(new)}\t{word}\t{score}\tcolloq\n")
    return rows


# docs/contracts/net-slang.md §2（維護者 2026-10-24「用量多的留第一」）：同一個讀音有好幾個網路用語時，檔案裡排在前面的（PTT 用量多的）留第一，
# 後面的寫法分數再扣這個值、只當候選。1.0 是取整的保守值，和手動詞加分 0.75 同一個量級：兩個都加過分，扣 1.0 後一定排在後面。
SLANG_BEHIND = 1.0
BOOST_MAX = 3.0   # net-slang-2.md §3：加分 min(3, ceil((gap + 0.25) * 2) / 2)


def boosted_words(path):
    """有第四欄加分的詞（網路用語）。"""
    with open(path, encoding="utf-8-sig") as f:
        return {l.split("\t")[0] for l in f if not l.startswith("#") and len(l.rstrip("\n").split("\t")) == 4}


def slang_behind(rows, boosted=frozenset()):
    """網路用語的列照檔案順序：一個讀音上第 k 個網路用語（k 從 0 起）的列，分數扣 k × SLANG_BEHIND。加分只能放在第一名（k = 0）：
    放在後面的列，加分 ≥ SLANG_BEHIND 時會和第一名打平或超過，違反「用量多的留第一」，所以中止。"""
    seen, out = {}, []
    for r in rows:
        key, w, score, tag = r.rstrip("\n").split("\t")
        words = seen.setdefault(key, [])
        if w not in words:
            words.append(w)
        k = words.index(w)          # 這個讀音上第 k 個網路用語（0 是第一名）：扣 k 倍，後面的寫法彼此也不同分
        if k and w in boosted:
            raise SystemExit(f"slang-add.tsv: {w!r} has a boost but is behind another word on {key} (rank {k}); a boost only goes on the first")
        if k:
            r = f"{key}\t{w}\t{round(float(score) - k * SLANG_BEHIND, 8)!r}\t{tag}\n"
        out.append(r)
    return out



def moe_primary_first():
    return readings.moe_primary()


def manual_words(paths=None):
    """overlay-manual.tsv 與 slang-add.tsv 的詞（手動詞、網路用語都接在萌典補詞後面，不能同詞）。沒有網路用語檔時只讀手動詞。"""
    out = set()
    for path in paths or (MANUAL, SLANG):
        if os.path.exists(path):
            with open(path, encoding="utf-8-sig") as f:
                out |= {l.split("\t")[0] for l in f.read().splitlines() if l.strip() and not l.startswith("#")}
    return out


Concised = collections.namedtuple("Concised", "syls variants")   # 簡編本一個詞名取的那一列：注音一式（音節 list）、[(變體類型, 變體注音 list)]
Pick = collections.namedtuple("Pick", "syls how extras fell listed tone", defaults=(None,))   # 讀音、依據、第二列們、退下來的原因（報告用）、字典有但不寫的變體 [(讀音, 標籤)]、簡編本給的本調列（沒有是 None）


def check_sha(path, sha, name):
    if sha256_file(path) != sha:
        sys.exit(f"SHA-256 mismatch for {name} ({path}); the file is not the one the contract pins")


def concised_syls(s):
    """簡編本的注音欄 → 音節 list（空白或全形空白分隔）；輕聲寫在音節前（˙ㄌㄜ），轉成 repo 的寫法（ㄌㄜ˙）。"""
    return [x[1:] + "˙" if x.startswith("˙") else x for x in re.split(r"[ \u3000]+", s.strip()) if x]


def load_concised(path=None):
    """教育部《國語辭典簡編本》xlsx（在 zip 裡）→ {詞名: Concised}。先核對 zip 的 SHA-256；只用標準庫。欄位照表頭找：字詞名、多音排序、注音一式、
    變體類型（表頭以此開頭；值 變／又音／語音／讀音）、變體注音。一個詞名有好幾列取多音排序最小的一列（同序取檔案順序在前）；
    變體只取這一列自己的。多音排序空白排最後。契約 §2.3 修訂四。"""
    path = path or CONCISED
    check_sha(path, CONCISED_SHA256, "the concised dictionary")
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with zipfile.ZipFile(path) as z:
        x = zipfile.ZipFile(io.BytesIO(z.read(next(n for n in z.namelist() if n.endswith(".xlsx")))))
    strings = ["".join(t.text or "" for t in [*si.findall(f"{ns}t"), *si.findall(f"{ns}r/{ns}t")]) for si in ET.fromstring(x.read("xl/sharedStrings.xml"))]
    best, head = {}, {}
    for _, el in ET.iterparse(io.BytesIO(x.read("xl/worksheets/sheet1.xml"))):
        if el.tag != ns + "row":
            continue
        cells = {}
        for c in el:
            v = c.find(ns + "v")
            if v is not None:
                cells[re.match(r"[A-Z]+", c.get("r")).group()] = strings[int(v.text)] if c.get("t") == "s" else v.text
        el.clear()
        if not head:                                # 第一列是表頭
            head = {name: col for col, name in cells.items()}
            col = {k: next((c for n, c in head.items() if n.startswith(k)), None) for k in ("字詞名", "多音排序", "注音一式", "變體類型", "變體注音")}
            if missing := [k for k, c in col.items() if c is None]:
                sys.exit(f"the concised dictionary has no column {'、'.join(missing)} in its header; the file is not the one the contract pins")
            continue
        w, syls = cells.get(col["字詞名"], "").strip(), concised_syls(cells.get(col["注音一式"], ""))
        order = float(cells.get(col["多音排序"]) or math.inf)   # 空白排在所有有編號的列後面；float 容許 "1.0"
        if w and syls and (w not in best or order < best[w][0]):
            kind, var = cells.get(col["變體類型"], "").strip(), concised_syls(cells.get(col["變體注音"], ""))
            best[w] = (order, Concised(syls, [(kind, var)] if kind and var else []))
    return {w: c for w, (_, c) in best.items()}


def jp_drop_set(dict_chars, path=None):
    """契約 §2.3 修訂四：OpenCC JPShinjitaiCharacters.txt 裡不是自己的對應字（新字體）、而且不在 dict_chars（簡編本與萌典所有詞條的字）的字。"""
    path = path or JP
    check_sha(path, JP_SHA256, "OpenCC JPShinjitaiCharacters.txt")
    out = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#") or "\t" not in line:
                continue
            k, vs = line.rstrip("\n").split("\t")
            if k not in vs.split() and k not in dict_chars:
                out.add(k)
    return out


def unusable(w, syls, syllables):
    """簡編本與萌典共用的條件：音節數等於字數、每個音節基底都有。合格回 None，否則回原因。"""
    if len(syls) != len(w):
        return "音節數不等於字數"
    return None if all(s in syllables for s in syls) else "有基底沒有的音節"


def concised_tone(w, c, syllables):
    """簡編本那一列的主讀音有輕聲，而某個「變」正是它的本調（只有主讀音輕聲的位置換成非輕聲，其餘音節相同、合 unusable 的條件）→ 那個變體；沒有回 None。"""
    neu = lambda s: s.endswith("˙")
    for kind, v in c.variants:
        if kind == "變" and len(v) == len(c.syls) and v != c.syls and all(a == b or (neu(a) and not neu(b)) for a, b in zip(c.syls, v)) \
                and not unusable(w, v, syllables):
            return v
    return None


def concised_variants(w, c, syllables):
    """簡編本那一列的變體 → (寫成第二列的讀音們, [(讀音, 標籤)] 不寫的)。變：只差 一／不 的不寫（overlay_rows 補變調列）；本調（concised_tone）不在這裡，當本調列；
    變體的輕聲位置以外每個音節都和主讀音相同、而且有音節變輕聲的，寫成第二列；其他不寫。語音：第二列。又音、讀音：不寫。要寫的都得合 unusable 的條件，不合就不寫、記原因。"""
    extra, listed = [], []
    tone = concised_tone(w, c, syllables)
    for kind, v in c.variants:
        if kind in ("又音", "讀音"):
            listed.append((v, f"簡編本{kind}（不寫）"))
            continue
        if kind == "變":
            if v == tone:
                continue
            same = len(v) == len(c.syls)
            if same and all(a == b or (i < len(w) and w[i] in "一不") for i, (a, b) in enumerate(zip(c.syls, v))):
                continue
            neu = lambda s: s.endswith("˙")
            if not (same and all(a == b or (neu(b) and not neu(a)) for a, b in zip(c.syls, v)) and any(neu(b) and not neu(a) for a, b in zip(c.syls, v))):
                listed.append((v, "簡編本變（不是輕聲也不是 一／不 變調，不寫）"))
                continue
        why = unusable(w, v, syllables)
        if why:
            listed.append((v, f"簡編本{kind}（{why}，不寫）"))
        else:
            extra.append(v)
    return extra, listed


def dict_pick(w, concised, moe, syllables, fold_w):
    """字典給 w 的讀音：簡編本 → 萌典第一讀音（詞和 fold_w 正規化後的詞都查；寫出的詞不變）。每一邊都要合 unusable 的條件，不合就退到下一個，
    原因記在 fell。兩邊都沒有回 Pick(None, …)。讀音還沒套 normalize。"""
    fell = []
    for tag, table, name in (("concised", concised, "簡編本"), ("moedict", moe, "萌典")):
        for k in dict.fromkeys((w, fold_w(w))):
            e = table.get(k)
            if e is None:
                continue
            main = list(e.syls) if tag == "concised" else list(e)
            why = unusable(w, main, syllables)
            if why:
                fell.append(f"{name}不合：{why}")
                continue
            extras, listed = concised_variants(w, e, syllables) if tag == "concised" else ([], [])
            return Pick(main, tag, extras, "；".join(fell) or None, listed, concised_tone(w, e, syllables) if tag == "concised" else None)
    return Pick(None, None, [], "；".join(fell) or None, [])


TONE_MARKS = "ˊˇˋ˙"


def rime(syl):
    """音節去掉聲調符號（一聲沒有符號）。"""
    return syl.rstrip(TONE_MARKS)


def base_tones(path=BASE):
    """字 → [(讀音, 分數)]：基底裡這個字當單字詞的非輕聲讀音，照這個字自己的列在 mcbpmf-data.txt 裡的順序。輕聲換本調用（tone_row）。"""
    out = collections.defaultdict(list)
    with open(path, encoding="utf-8") as f:
        for line in f:
            parts = line.split()
            if line[0] in "#_" or len(parts) != 3 or len(parts[1]) != 1 or "-" in parts[0] or parts[0].endswith("˙"):
                continue
            out[parts[1]].append((parts[0], float(parts[2])))
    return out


def tone_row(w, syls, tones, given=None):
    """syls 有輕聲時回 (換成本調的讀音 list 或 None, 依據)，沒有輕聲回 (None, None)。依據：簡編本變（given，簡編本自己給的本調，直接用它）／同韻最高分
    （有的位置沒有同韻讀音，那個位置保留輕聲，依據另註）／沒有同韻的非輕聲讀音（沒有任何位置能換：回 None，不寫第二列）。
    本調的規則和 docs/contracts/neutral-tolerance.md §1 相同：輕聲音節的本調＝這個字的非輕聲單字讀音裡同韻（去掉聲調符號後相同）分數最高的；
    平手取這個字自己的列在 mcbpmf-data.txt 裡最前面的（tones 的串列順序，max 取第一個）；沒有同韻的非輕聲讀音時那個位置保留輕聲
    （核心不收沒有聲調的 ㄉㄜ、ㄌㄜ，一聲的後備寫了也打不到）。結果過 normalize。"""
    pos = [i for i, s in enumerate(syls) if s.endswith("˙")]
    if not pos:
        return None, None
    if given:
        return normalize(w, given), "簡編本變"
    out, kept = list(syls), False
    for i in pos:
        same = [x for x in tones.get(w[i], ()) if rime(x[0]) == rime(syls[i])]
        if same:
            out[i] = max(same, key=lambda x: x[1])[0]
        else:
            kept = True
    if out == list(syls):
        return None, "沒有同韻的非輕聲讀音"
    return normalize(w, out), "同韻最高分，其他輕聲位置保留輕聲" if kept else "同韻最高分"


def word_rows(w, readings, score, src):
    """一個詞的幾個讀音（第一個在前，同分）各自過 overlay_rows；同一個（讀音, 詞）只留第一列。"""
    out, seen = [], set()
    for syls in readings:
        for r in overlay_rows(w, syls, score, src):
            if r.split("\t")[0] not in seen:
                seen.add(r.split("\t")[0])
                out.append(r)
    return out


# 契約 §2.3 修訂四驗收（2026-10-26，維護者選「修幾個字再抽一次」）：國文導師判讀規則類 34 個，錯的 10 個集中在這幾個字——
# 為（作為、頗為 讀 ㄨㄟˊ）、町（台灣的町名讀 ㄉㄧㄥ）、蛤（貝類 ㄍㄜˊ）、吧（迪吧 是 bar）、萎、凊（規則選到大陸音）。規則改音時這幾個字保留原讀音。
RULE_KEEP_CHARS = frozenset("為町蛤吧萎凊")


def reading_picker(base, syllables, moe, concised, fold_w):
    """--moedict-readings：詞 → Pick。字典（簡編本，再萌典第一讀音）有收且合 unusable 的條件就用字典；其餘用 readings.Reader；
    都套 normalize（一、不、法、和 回到審訂表的本調，變調列由 overlay_rows 加）。讀不出回 Pick(None, …)。
    readings.py 判成 CHECK（有歧義）或 GAP 的詞回 (它的暫用讀音, "CHECK" 或 "GAP")，build() 讓它保留原本的讀音（只列進報告）。"""
    reader = readings.make_reader(base, rule_3b=True)    # the SJ-LM-7 lexicon steps use rule 3b (contract 2.3)

    def pick(w):
        p = dict_pick(w, concised, moe, syllables, fold_w)
        if p.syls:
            syls = normalize(w, p.syls)
            return p._replace(syls=syls, extras=[e for e in (normalize(w, x) for x in p.extras) if e != syls])
        out, notes, gaps = reader.read(w)
        if not out:
            return p
        out = normalize(w, out)
        today = base.to_syllables(w)
        if today is not None:                       # 規則不改這幾個字的讀音（那個位置保留原讀音）
            today = normalize(w, today)
            out = [t if c in RULE_KEEP_CHARS else o for c, o, t in zip(w, out, today)]
        return p._replace(syls=out, how="GAP" if gaps else "CHECK" if notes else "readings.py")
    return pick


def other_layers():
    """名單與所有詞包的字串：萌典補詞不能和它們相交（pack-oov：詞包字串和各層沒有交集）。"""
    out = set()
    for p in [NAMES, *sorted(glob.glob(os.path.join(PACKS, "*-add.tsv")))]:
        out |= pack_words(p)
    return out


def _open_text(path):
    return (bz2.open(path, "rt", encoding="utf-8", errors="replace") if path.endswith(".bz2") else
            gzip.open(path, "rt", encoding="utf-8", errors="replace") if path.endswith(".gz") else open(path, encoding="utf-8", errors="replace"))


_CAND, _LENS, _FIRST = frozenset(), (), frozenset()


def _count_chunk(lines):
    n = collections.Counter()
    for line in lines:
        for i, c in enumerate(line):
            if c in _FIRST:
                for L in _LENS:
                    if line[i:i + L] in _CAND:
                        n[line[i:i + L]] += 1
    return n


def _init_count(cand, lens):
    global _CAND, _LENS, _FIRST
    _CAND, _LENS, _FIRST = cand, lens, frozenset(c[0] for c in cand)


def _chunks(paths, size=5000):
    buf = []
    for p in paths:
        with _open_text(p) as f:
            for line in f:
                buf.append(line.rstrip("\n"))
                if len(buf) == size:
                    yield buf
                    buf = []
    if buf:
        yield buf


def count_substrings(paths, candidates, jobs=1):
    """candidates 在 paths（一行一段文字）裡的子字串出現次數（重疊也算，跨行不算）。"""
    cand, lens = frozenset(candidates), tuple(sorted({len(c) for c in candidates}))
    total = collections.Counter()
    if jobs <= 1:
        _init_count(cand, lens)
        for c in _chunks(paths):
            total.update(_count_chunk(c))
        return total
    with multiprocessing.Pool(jobs, initializer=_init_count, initargs=(cand, lens)) as pool:
        for n in pool.imap_unordered(_count_chunk, _chunks(paths)):
            total.update(n)
    return total


def supplement_words(moe, taken, corpus_paths, scores, syllables, jobs=1, chars=None, simp_only=()):
    """來源「萌典補詞」的詞（契約 §2.1a）：moe（詞 → 第一讀音）裡純漢字、字數在 scores 裡、不在 taken、音節數等於字數、音節基底都有的詞，
    在 corpus_paths 裡子字串出現 >= SUPPLEMENT_MIN 次者。和其他疊加層候選同一組過濾：chars 不是 None 時每個字都要是基底的單字詞，
    不含 simp_only 的字（build() 傳進來；之後的異體／重複判斷由 build() 對它們一起做）。"""
    cand = {w for w, m in moe.items() if MANUAL_WORD.match(w) and len(w) in scores and w not in taken and len(m) == len(w) and all(s in syllables for s in m)
            and (chars is None or all(c in chars for c in w)) and not any(c in simp_only for c in w)}
    counts = count_substrings(corpus_paths, cand, jobs)
    return {w for w in cand if counts[w] >= SUPPLEMENT_MIN}


def supplement_rows(moe, taken, corpus_paths, scores, syllables, jobs=1, **filters):
    """supplement_words 的列：讀音＝萌典第一讀音（normalize），分數＝scores[字數]，沒有手動詞的加分；詞的 code point 排序，
    含「一」「不」的詞照 overlay_rows 附變調列。build() 另外讓這些詞走異體判斷，所以實際寫檔的列由 build() 產生。"""
    return [r for w in sorted(supplement_words(moe, taken, corpus_paths, scores, syllables, jobs, **filters))
            for r in overlay_rows(w, normalize(w, moe[w]), scores[len(w)], SUPPLEMENT_SRC)]


def cantonese(w, headwords):
    """canto-prune：詞含 CANTO_CHARS 任一字、又不是 headwords（萌典詞目）裡的詞。手動詞不套用（manual_rows 不呼叫它）。"""
    return not CANTO_CHARS.isdisjoint(w) and w not in headwords


def length_scores(base):
    """長度 → 基底同字數詞條分數的第 25 百分位；某長度的基底詞條少於 1,000 條就沿用上一個長度（5 字以上才會發生）。
    2–4 字就是 SCORE；build_acg_pack.scores 也用這一個。"""
    sc = {}
    for n in range(2, 11):
        got = sorted(s for syls, ents in base.by_reading.items() if len(syls) == n for _, s in ents)
        sc[n] = got[len(got) // 4] if len(got) >= 1000 else sc[n - 1]
    return sc


def base_syllables(base):
    return {s for syls in base.by_reading for s in syls}


def pack_words(path=PACK):
    with open(path, encoding="utf-8") as f:
        return {l.split("\t")[1] for l in f}


def names_words(path=NAMES):
    with open(path, encoding="utf-8") as f:
        return {l.split("\t")[1] for l in f if "\t" in l}


def manual_rows(taken, scores, syllables, path=MANUAL, tag="manual"):
    """overlay-manual.tsv 的列（詞、讀音、備註；# 開頭與空行略過），照檔案順序。每列都要：三欄（網路用語可以有第四欄，見最後）；詞是純漢字、字數在 scores 裡（2 字以上）、
    不在 taken（基底、疊加層、詞包）；讀音的音節數等於字數，每個音節都是基底有的音節。產生的列（含 overlay_rows 自動加的變調列）
    同一個（讀音, 詞）不能出現兩次。不符就中止並指出行號。分數＝scores[字數]＋MANUAL_BONUS＋可選的第四欄「加分」（只有網路用語，tag="slang"；
    0.5 的倍數、0 < 加分 ≤ BOOST_MAX；維護者 2026-10-25：網路用語輸給沒人這樣講的組合時加分，見 net-slang-2.md §3）。"""
    rows, seen = [], set()
    with open(path, encoding="utf-8-sig") as f:
        lines = f.read().splitlines()
    for n, line in enumerate(lines, 1):
        if line.startswith("#") or not line.strip():
            continue
        fields = line.split("\t")
        ok = len(fields) in (3, 4)
        boost = 0.0
        if ok and len(fields) == 4:
            try:
                boost = float(fields[3])
            except ValueError:
                ok = False
            ok = ok and tag == "slang" and math.isfinite(boost) and 0 < boost <= BOOST_MAX and (boost * 2).is_integer()
        if ok:
            w, reading = fields[:2]
            syls = reading.split(" ")
            ok = bool(MANUAL_WORD.match(w)) and len(w) in scores and len(syls) == len(w) and all(s in syllables for s in syls) and w not in taken
        if not ok:
            raise SystemExit(f"{path}:{n}: bad manual row")
        for r in overlay_rows(w, syls, round(scores[len(w)] + MANUAL_BONUS + boost, 8), tag):
            key = tuple(r.split("\t")[:2])
            if key in seen:
                raise SystemExit(f"{path}:{n}: {w!r} repeats a reading already listed (sandhi rows count)")
            seen.add(key)
            rows.append(r)
    return rows


def main():
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--tails-only", action="store_true", help="不讀 dump：保留 overlay-add.tsv 產生的列，只重算檔尾的手動詞、網路用語與 colloq 變體")
    ap.add_argument("--moedict-readings", action="store_true")
    ap.add_argument("--reading-report", metavar="PATH")
    ap.add_argument("--moedict-supplement", nargs="+", metavar="FILE")
    ap.add_argument("--jobs", type=int, default=1)
    a = ap.parse_args()
    if a.reading_report and not a.moedict_readings:
        ap.error("--reading-report needs --moedict-readings")
    if a.tails_only:
        rows = rebuild_tails()
        with open(OUT + ".tmp", "w", encoding="utf-8") as f:
            f.writelines(rows)
        os.replace(OUT + ".tmp", OUT)
        print(f"wrote {len(rows)} rows to {os.path.relpath(OUT, ROOT)} (tails only)")
        return
    rows, removed, variant_removed, jp_removed = build(a.moedict_readings, a.reading_report, a.moedict_supplement, a.jobs)
    outputs = [(OUT, rows), (REMOVED, removed), (VARIANT_REMOVED, variant_removed)] + ([(JP_REMOVED, jp_removed)] if a.moedict_readings else [])   # 日文寫法檔只有 --moedict-readings 才有
    if a.check:
        same = all(os.path.exists(path) and open(path, encoding="utf-8").readlines() == lines for path, lines in outputs)   # 檔案不在＝不同
        print(f"{len(rows)} rows, {len(removed)} removed, {len(variant_removed)} variant-removed"
              + (f", {len(jp_removed)} jp-removed" if a.moedict_readings else "") + f"; {'matches' if same else 'DIFFERS FROM'} the files in the repo")
        sys.exit(0 if same else 1)
    os.makedirs(os.path.dirname(VARIANT_REMOVED), exist_ok=True)
    if not a.moedict_readings and os.path.exists(JP_REMOVED):   # 預設的疊加層不拿掉日文寫法：舊檔會寫著已經回到疊加層的詞
        os.remove(JP_REMOVED)
    for path, lines in outputs:
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            f.writelines(lines)
        os.replace(path + ".tmp", path)
    print(f"wrote {len(rows)} rows to {os.path.relpath(OUT, ROOT)}, {len(removed)} removed rows to {os.path.relpath(REMOVED, ROOT)}, {len(variant_removed)} variant-removed rows to {os.path.relpath(VARIANT_REMOVED, ROOT)}"
          + (f", {len(jp_removed)} jp-removed rows to {os.path.relpath(JP_REMOVED, ROOT)}" if a.moedict_readings else ""))


if __name__ == "__main__":
    main()
