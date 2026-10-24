"""S1：由鎖定版本的維基標題 dump 產生詞庫疊加層 data/lexicon/overlay-add.tsv。

契約見 docs/PLAN.md §2 S1；這支腳本就是契約的參考實作。輸出以 CC BY-SA 4.0 釋出（LICENSES/data.md）。
用法：python3 tools/build_overlay.py          產生並寫入
      python3 tools/build_overlay.py --check  重新產生並和 repo 裡的檔案比對，不同就 exit 1
來源下載到 ~/.cache/shanjie/sources/，SHA-256 不符就中止。

SJ-LM-7 契約 §2.1a、§2.3（都預設關，不加旗標時輸出和原來逐位元組相同）：
      --moedict-readings          萌典有收的詞用萌典第一讀音，沒收的用 tools/readings.py 的規則（Reader）；萌典路徑見 readings.MOEDICT（SHANJIE_MOEDICT）
      --reading-report PATH       （搭配上一個）把讀音和原本方法（基底最佳讀音＋normalize）不同的詞寫到 PATH：詞、原讀音、新讀音、依據
      --moedict-supplement FILE…  來源「萌典補詞」：萌典有收、不在基底、疊加層、名單、任何詞包，而且在這些純文字檔（一行一段，.gz／.bz2 照副檔名）裡
                                  子字串出現 ≥ 2 次的詞；讀音＝萌典第一讀音，分數＝同字數的 length_scores（不加手動詞的加分）
      --jobs N                    萌典補詞數子字串用幾個程序
"""
import argparse
import bz2
import collections
import glob
import gzip
import hashlib
import os
import multiprocessing
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
MANUAL = os.path.join(ROOT, "data", "lexicon", "overlay-manual.tsv")   # 維護者手動加的詞，接在產生的列後面（來源 manual）
PACKS = os.path.join(ROOT, "data", "packs")
NAMES = os.path.join(ROOT, "data", "lexicon", "names-add.tsv")
SUPPLEMENT_SRC = "萌典補詞"
SUPPLEMENT_MIN = 2   # 契約 §2.1a：語料裡子字串出現的次數下限
PACK = os.path.join(ROOT, "data", "packs", "acg-add.tsv")   # 手動詞也不能是詞包的詞：引擎把詞包接在疊加層後面，同詞同讀音重複就整個載入失敗
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


def overlay_rows(w, syls, score, src):
    """一個詞的疊加層列：主要列在前，含「一」「不」的變調列緊接其後（同來源，分數 − VARIANT_PENALTY）。build_acg_pack 共用。"""
    rows = [f"{'-'.join(syls)}\t{w}\t{score!r}\t{src}\n"]
    var = sandhi_variant(w, syls)
    if var:
        rows.append(f"{'-'.join(var)}\t{w}\t{round(score - VARIANT_PENALTY, 8)!r}\t{src}\n")
    return rows


def build(moedict_readings=False, reading_report=None, supplement=None, jobs=1):
    """supplement：None，或萌典補詞要數的純文字檔路徑清單。其他參數見模組說明；預設值＝原本的輸出。"""
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
    info = {}                                       # 詞 → (讀音, 同讀音的基底異體詞)
    moe_first = moe_primary_first() if (moedict_readings or supplement) else None
    pick = reading_picker(base, base_syllables(base), moe_first) if moedict_readings else None
    changed = []
    for w in sorted(cand):
        syls = base.to_syllables(w)
        if syls is not None:
            syls = normalize(w, syls)
            if pick:
                new, how = pick(w)
                if new and new != syls:
                    changed.append(f"{w}\t{' '.join(syls)}\t{' '.join(new)}\t{how}\n")
                syls = new or syls
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
        rows += overlay_rows(w, syls, SCORE[len(w)], src)
    overlay_words = {r.split("\t")[1] for r in rows}
    assert not overlay_words & words                # 疊加層和基底的詞表交集必須是 0（手動詞由 manual_rows 自己檢查）
    if supplement:
        sup = supplement_rows(moe_first, words | overlay_words | other_layers() | manual_words(), supplement, length_scores(base), base_syllables(base), jobs)
        rows += sup
        overlay_words |= {r.split("\t")[1] for r in sup}
    rows += manual_rows(words | overlay_words | pack_words(), length_scores(base), base_syllables(base))
    if reading_report:
        with open(reading_report, "w", encoding="utf-8", newline="\n") as f:
            f.write("詞\t原讀音\t新讀音\t依據\n" + "".join(changed))
        print(f"reading report: {len(changed)} words whose reading changes, {reading_report}")
    return rows, removed, variant_removed


def moe_primary_first():
    return readings.moe_primary()


def manual_words(path=MANUAL):
    """overlay-manual.tsv 的詞（手動詞接在萌典補詞後面，兩者不能同詞）。"""
    with open(path, encoding="utf-8-sig") as f:
        return {l.split("\t")[0] for l in f.read().splitlines() if l.strip() and not l.startswith("#")}


def reading_picker(base, syllables, moe):
    """--moedict-readings：詞 → (讀音 list, 依據)。萌典有收（音節數等於字數、每個音節基底都有）用萌典第一讀音，其餘用 readings.Reader；
    兩者都套 normalize（一、不、法、和 回到審訂表的本調，變調列由 overlay_rows 加）。讀不出回 (None, None)。"""
    reader = readings.make_reader(base)

    def pick(w):
        m = moe.get(w)
        if m and len(m) == len(w) and all(s in syllables for s in m):
            return normalize(w, m), "moedict"
        out, _notes, _gaps = reader.read(w)
        return (normalize(w, out), "readings.py") if out else (None, None)
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


def supplement_rows(moe, taken, corpus_paths, scores, syllables, jobs=1):
    """來源「萌典補詞」的列（契約 §2.1a）：moe（詞 → 第一讀音）裡純漢字、字數在 scores 裡、不在 taken、音節數等於字數、音節基底都有的詞，
    在 corpus_paths 裡子字串出現 >= SUPPLEMENT_MIN 次者；讀音＝萌典第一讀音（normalize），分數＝scores[字數]，沒有手動詞的加分。
    詞的 code point 排序；含「一」「不」的詞照 overlay_rows 附變調列。"""
    cand = {w for w, m in moe.items() if MANUAL_WORD.match(w) and len(w) in scores and w not in taken and len(m) == len(w) and all(s in syllables for s in m)}
    counts = count_substrings(corpus_paths, cand, jobs)
    return [r for w in sorted(w for w in cand if counts[w] >= SUPPLEMENT_MIN)
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


def manual_rows(taken, scores, syllables, path=MANUAL):
    """overlay-manual.tsv 的列（詞、讀音、備註；# 開頭與空行略過），照檔案順序。每列都要：三欄；詞是純漢字、字數在 scores 裡（2 字以上）、
    不在 taken（基底、疊加層、詞包）；讀音的音節數等於字數，每個音節都是基底有的音節。產生的列（含 overlay_rows 自動加的變調列）
    同一個（讀音, 詞）不能出現兩次。不符就中止並指出行號。分數＝scores[字數]＋MANUAL_BONUS。"""
    rows, seen = [], set()
    with open(path, encoding="utf-8-sig") as f:
        lines = f.read().splitlines()
    for n, line in enumerate(lines, 1):
        if line.startswith("#") or not line.strip():
            continue
        fields = line.split("\t")
        ok = len(fields) == 3
        if ok:
            w, reading, _note = fields
            syls = reading.split(" ")
            ok = bool(MANUAL_WORD.match(w)) and len(w) in scores and len(syls) == len(w) and all(s in syllables for s in syls) and w not in taken
        if not ok:
            raise SystemExit(f"{path}:{n}: bad manual row")
        for r in overlay_rows(w, syls, round(scores[len(w)] + MANUAL_BONUS, 8), "manual"):
            key = tuple(r.split("\t")[:2])
            if key in seen:
                raise SystemExit(f"{path}:{n}: {w!r} repeats a reading already listed (sandhi rows count)")
            seen.add(key)
            rows.append(r)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--moedict-readings", action="store_true")
    ap.add_argument("--reading-report", metavar="PATH")
    ap.add_argument("--moedict-supplement", nargs="+", metavar="FILE")
    ap.add_argument("--jobs", type=int, default=1)
    a = ap.parse_args()
    if a.reading_report and not a.moedict_readings:
        ap.error("--reading-report needs --moedict-readings")
    rows, removed, variant_removed = build(a.moedict_readings, a.reading_report, a.moedict_supplement, a.jobs)
    if a.check:
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
