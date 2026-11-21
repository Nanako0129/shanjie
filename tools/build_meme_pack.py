"""迷因詞包建置工具：由候選檔、兩份論壇 A 用量計數檔與出貨的 data/lm，
產生 data/packs/meme-add.tsv（疊加層格式，標籤 meme）、meme-sources.tsv、meme.json，報告放 build/meme-pack/。
分數、讀音、排序、同音衝突偵測共用 tools/build_acg_pack.py；出貨 data/lm 的核對、讀音覆寫檔、寫檔共用 tools/build_it_pack.py（import）。
`--pack beauty`（docs/spec/lexicon-and-packs.md §8.2）：同一支工具建美妝詞包，標籤 beauty、所有檔名的 meme 換成 beauty、種子 20261108、
自己的 beauty-add.tsv 不算去重；--exclude／--exclude-private 剔除的字串在 collisions 處置檔同一處生效；(b) 的參考名單只收過了門檻的候選；
兩個詞包的 .json 的 candidate_files 都只記檔名（兩個候選檔同名就停）。以下說明以迷因的檔名為例。
解碼用的詞包明列：`--pack meme`（預設）用 acg,it,beauty（迷因以外預設開的詞包；經典台詞詞包即使 quotes-add.tsv 在也不帶），`--pack beauty` 用 acg,it。
輸出的列先放聊天沒有詞包時打不對的詞，再放本來就對的，最後放處置檔 `+` 列的詞（只有迷因；先後不影響分數）。
迷因詞包（docs/spec/lexicon-and-packs.md 的迷因詞包一節）只收審定清單裡的詞：
  --allow FILE（預設 data/packs/meme-allow.tsv；`--pack meme` 的 --emit-x 與最後一段都要它存在，--emit-x 只檢查並解析、輸出不看清單；`--pack beauty` 拒絕這個旗標）：
  UTF-8，`詞<TAB>類別`，類別是 meme 或 offensive；`#` 與空行略過；別的類別、缺類別欄、重複的詞（NFKC 之後比）都以訊息點名行號後停止。
  最後一段：解碼表核對之後、判規則之前，不在清單裡的候選拿掉（stages 的 not_allowed）；它們不擋同讀音的清單詞，也不在 (b) 的參考名單裡。
  清單詞聊天與書面第一名都已經是它也照收（`already_right` 只對 --pack beauty 丟）；讓不讓第一名、`x_other_pack`、(c) 不變。
  同讀音：處置檔有 `+` 列點名兩個詞，judge 兩個都留；沒有 `+` 列照舊只留文章數多的。
  --pack meme 拒絕 --exclude 與 --exclude-private（清單是唯一的剔除路徑）。
  報告 allow-missing.tsv：清單裡每個沒進 meme-add.tsv 的詞一列 `詞<TAB>類別<TAB>原因<TAB>細節`，原因：not_a_candidate、in_lexicon_or_pack、below_threshold、
  unreadable、excluded_by_collisions_file、same_reading、x_not_fewer、x_other_pack、rule_c；收進的詞數加這份表的列數不等於清單筆數就在寫完報告後以非 0 結束（不寫 data/packs）。
  meme-sources.tsv 在 sources 後多一欄 class；meme.json 的 allow 記清單檔名、SHA-256、筆數與各類別收進的詞數；
  words-all.txt（報告）：收進的詞（讀音是 CHECK 的除外），`|詞|讀音`，照 meme-add.tsv 的順序、每詞取第一列（變調列不另列）；之後接另外的讀音各一列（--extra-readings）。
全程不連網。迷因詞包的輸出以 CC BY-SA 4.0 釋出；美妝詞包是 CC0（LICENSES/data.md）。同一份輸入建兩次，輸出逐位元組相同。

候選檔（--cand，可多個，一個檔一個來源）：UTF-8 文字，一行一個，`字串<TAB>來源`；來源欄可省（用檔名去掉副檔名），`#` 開頭與空行略過。
  字串含 ASCII 英數的整串不收（數量列在報告）；其餘照標點（任何非漢字）切開，每段各自當候選；只收 2–10 個漢字的段。
計數檔（--counts）：計數工具的輸出。前三行檔頭 `# tree_sha256 X`、`# input_sha256 Y`、`# tool_commit Z`，
  之後每列 `字串<TAB>文章數<TAB>看板數`。三個檔頭欄位都記進 meme.json。計數檔的 input_sha256 要等於它的輸入字串檔的 SHA-256：
  第一次計數的輸入是本工具 `--emit-candidates` 寫的 meme-candidates.txt，第二次是 `--emit-x` 寫的 meme-x-strings.txt；兩份計數檔的頁面目錄雜湊要相同。檔頭欄位名以 `_tree_sha256` 結尾的舊寫法也讀（當成 tree_sha256）。

另外的讀音（只有 --pack meme、最後一段）：--extra-readings 同詞包目錄的 meme-extra-readings.tsv（`詞<TAB>讀音`，沒有就沒有）。收進詞包的詞每個另外的讀音多一列
  （含變調列）、和主要讀音同分，同音衝突照樣偵測；words-all.txt 在原本的列之後接上這些讀音各一列（確認過的讀音，主要讀音是 CHECK 的詞也列）。解碼表只看主要讀音。

三個階段（每一階段都用 --cand；讀音覆寫檔 --readings 同詞包目錄的 meme-readings.tsv，格式同 it-readings.tsv，沒有就沒有）：
  1. --emit-candidates                 寫 WORK/meme-candidates.txt（一行一個，去重、照位元組排序）給 main 做第一次計數
  2. --emit-x --counts C1              收進門檻（看板 ≥ 3 且文章 ≥ 10）的候選，用出貨 CLI（上面那一組詞包開，沒有迷因本身）解碼，
                                       寫 WORK/meme-x.tsv（候選、讀音、聊天的第一名 X、書面的第一名；書面的只用在「已經打得對」）、
                                       meme-x-strings.txt（一行一個 X，去重、照位元組排序）、meme-x.json（兩者的 SHA-256）
  3. --counts C1 C2                    核對 meme-x.tsv 的雜湊，讀第二次計數（對 meme-x-strings.txt）後依「讓不讓第一名」的規則建置
     同音衝突偵測的「開」那次，預設把詞包的列接在 it-add.tsv 後面（近似）；`--native-pack`（只有 --pack meme）改成真的 `--packs acg,it,meme,beauty`。
"""
import argparse
import collections
import glob
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import build_it_pack as IT  # noqa: E402

B = IT.B
PACKS = B.PACKS
TAG = "meme"
MIN_BOARDS, MIN_ARTICLES = 3, 10    # 用量門檻：事先固定，量之前 commit
MIN_WORDS = 10                      # 迷因詞包的停止條件：收錄後不到 10 個詞就停。審定清單本來就不長；低於這個數多半是輸入出了問題
MIN_WORDS_BEAUTY = 30               # 美妝詞包的停止條件：不到 30 個詞就停
BONUS = 3.0                         # docs/spec/lexicon-and-packs.md §6.2「分數」：同資訊詞包，每列的 log10 分數加 3
SEEDS, CHECK_N = {"meme": 20261025, "beauty": 20261108}, 60        # 各詞包檢查集的固定種子與抽樣數
SEED = SEEDS[TAG]
DECODE_PACKS = {"meme": ("acg", "it", "beauty"), "beauty": ("acg", "it")}     # 解碼開著的詞包：該詞包以外預設開的詞包，經典台詞詞包不在內
ALLOW_CLASSES = ("meme", "offensive")
HEAD_FIELDS = ("tree_sha256", "input_sha256", "tool_commit")
ASCII_ALNUM = re.compile(r"[A-Za-z0-9]")
NON_HAN = re.compile(r"[^一-鿿]+")


def bsorted(items):
    return sorted(items, key=lambda s: s.encode("utf-8"))


def lines_text(items):
    return "".join(s + "\n" for s in bsorted(set(items)))


# ---------------------------------------------------------------- 輸入

def read_candidates(paths):
    """候選檔 → ({候選: {來源}}, 統計, {給的路徑: 檔案雜湊})。整串含英數的不收（NFKC 之後比，全形英數也算）；其餘照標點切段，段落要 2–10 個漢字。
    來源欄省略時用檔名去掉副檔名；雜湊記在給的路徑下，不同資料夾的同名檔不會互相蓋掉。"""
    cands, files = collections.defaultdict(set), {}
    st = collections.Counter()
    for p in paths:
        files[p] = B.sha256_file(p)
        default = os.path.splitext(os.path.basename(p))[0]
        with open(p, encoding="utf-8") as f:
            for l in f:
                l = l.rstrip("\n")
                if not l.strip() or l.startswith("#"):
                    continue
                s, *rest = l.split("\t")
                src = rest[0].strip() if rest and rest[0].strip() else default
                st["lines"] += 1
                s = unicodedata.normalize("NFKC", s)            # 全形英數、康熙部首、相容表意字都先正規化，再判斷與切段
                if ASCII_ALNUM.search(s):
                    st["dropped_ascii"] += 1
                    continue
                for piece in (x for x in NON_HAN.split(s) if x):
                    if B.HAN.match(piece):
                        cands[piece].add(src)
                    else:
                        st["dropped_length"] += 1
    st["candidates"] = len(cands)
    return dict(cands), dict(st), files


def read_counts(path):
    """計數檔 → (檔頭三個欄位, {字串: (文章數, 看板數)})。缺檔頭欄位就中止。"""
    head, counts = {}, {}
    with open(path, encoding="utf-8") as f:
        for l in f:
            l = l.rstrip("\n")
            if l.startswith("#"):
                k, _, v = l.lstrip("# ").partition(" ")
                k = "tree_sha256" if k.endswith("_tree_sha256") else k      # older counts files carry a prefixed name
                head[k] = v.strip()
            elif l.strip():
                s, a, b = l.split("\t")
                counts[s] = (int(a), int(b))
    miss = [k for k in HEAD_FIELDS if not head.get(k)]
    if miss:
        raise SystemExit(f"{path}: counts header lacks {miss}")
    return {k: head[k] for k in HEAD_FIELDS}, counts


def have_words():
    """詞庫任何一層：基底、疊加層（網路用語的列也在 overlay-add.tsv 裡，標籤 slang）、sandhi、名單層含增補。"""
    return frozenset(B.lexicon_words())


def pack_words(packs_dir=PACKS, tag=TAG):
    """data/packs 裡每個詞包檔（動漫、資訊、經典台詞…，除了本詞包自己）的詞。"""
    out = set()
    for f in sorted(glob.glob(os.path.join(packs_dir, "*-add.tsv"))):
        if os.path.basename(f) != f"{tag}-add.tsv":
            with open(f, encoding="utf-8") as fh:
                out |= {l.split("\t")[1] for l in fh if l.strip() and not l.startswith("#")}
    return frozenset(out)


def read_extra(path):
    """另外的讀音（data/packs/meme-extra-readings.tsv）→ [(詞, [音節])]（檔案順序）。`詞<TAB>讀音（空白分隔）`，一列一個另外的讀音；`#` 開頭是註解；檔不在＝沒有。
    檔頭的 BOM 略過（編輯器存成 UTF-8 加 BOM 時，第一列的詞不會帶著它）。壞行、音節數不等於字數、同一個詞重複同一個讀音，以點名行號的訊息停止。"""
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8-sig") as f:
        for n, l in enumerate(f, 1):
            l = l.rstrip("\n")
            if not l.strip() or l.startswith("#"):
                continue
            cols = l.split("\t")
            if len(cols) != 2 or not cols[0].strip() or not cols[1].split():
                raise SystemExit(f"{path}:{n}: expected `word<TAB>reading`, got {l!r}")
            w, r = unicodedata.normalize("NFKC", cols[0].strip()), cols[1].split()
            if len(r) != len(w):
                raise SystemExit(f"{path}:{n}: {w} has {len(w)} characters, the reading has {len(r)} syllables")
            if (w, r) in out:
                raise SystemExit(f"{path}:{n}: {w} {' '.join(r)} is listed twice")
            out.append((w, r))
    return out


def read_allow(path):
    """審定清單 → ({詞: 類別}（檔案順序）, 檔案 SHA-256)。詞照候選的方式 NFKC 正規化；類別只收 meme／offensive；壞行與重複的詞以點名行號的訊息停止。"""
    if not os.path.exists(path):
        raise SystemExit(f"{path}: the allow list is missing (--pack meme needs --allow)")
    allow = {}
    with open(path, encoding="utf-8") as f:
        for n, l in enumerate(f, 1):
            l = l.rstrip("\n")
            if not l.strip() or l.startswith("#"):
                continue
            cols = l.split("\t")
            if len(cols) != 2 or cols[1].strip() not in ALLOW_CLASSES:
                raise SystemExit(f"{path}:{n}: expected `word<TAB>meme|offensive`, got {l!r}")
            w = unicodedata.normalize("NFKC", cols[0].strip())
            if not w:
                raise SystemExit(f"{path}:{n}: empty word")
            if w in allow:
                raise SystemExit(f"{path}:{n}: duplicate word {w!r}")
            allow[w] = cols[1].strip()
    return allow, B.sha256_file(path)


def read_exclude(path, must_exist=True):
    """--exclude／--exclude-private 的檔：`字串<TAB>…`，只讀第一欄（NFKC 之後比，同候選）；`#` 與空行略過。回傳 (字串集合, 檔案 SHA-256)；
    沒給檔或預設檔不存在（must_exist=False）就是 (空集合, None)。"""
    if path is None or (not must_exist and not os.path.exists(path)):
        return frozenset(), None
    with open(path, encoding="utf-8") as f:
        strings = {unicodedata.normalize("NFKC", l.split("\t")[0].strip()) for l in f if l.strip() and not l.startswith("#")}
    return frozenset(strings - {""}), B.sha256_file(path)


# ---------------------------------------------------------------- 解碼（出貨的 data/lm；動漫、資訊詞包開）

def decoder(packs_dir=PACKS, tag=TAG, native=False):
    """decode(pairs, 設定, meme=None)：出貨 CLI 解碼每個 (詞, 讀音)，開 DECODE_PACKS[tag] 的詞包（meme：acg,it,beauty；beauty：acg,it），回傳第一名；旗標同資訊詞包的解碼器（THREADS、SHIP_ARGS）。
    meme 給了（detect_collisions 寫的臨時資料夾，裡面是要測的詞包列）就是「開」那次：native=False 時把那些列（acg-add.tsv）接在 it-add.tsv 後面一起載入
    （近似：把詞包的列走資訊詞包的路徑）；native=True 時那個資料夾裡是 {tag}-add.tsv，連同詞包本身 `--packs` 帶上 acg,it,{tag},beauty 的真位元（只有 tag=meme）。"""
    names = DECODE_PACKS[tag]
    for name in names:
        if not os.path.exists(os.path.join(packs_dir, name + "-add.tsv")):
            raise SystemExit(f"{packs_dir}/{name}-add.tsv is missing (the shipping decode needs the {', '.join(names)} packs)")

    def decode(pairs, profile, meme=None):
        with tempfile.TemporaryDirectory() as d:
            for name in names:
                if name != "it" or not meme or native:
                    shutil.copy(os.path.join(packs_dir, name + "-add.tsv"), os.path.join(d, name + "-add.tsv"))
            if meme and native:
                shutil.copy(os.path.join(meme, f"{tag}-add.tsv"), os.path.join(d, f"{tag}-add.tsv"))
            elif meme:
                with open(os.path.join(d, "it-add.tsv"), "wb") as out:
                    for src in (os.path.join(packs_dir, "it-add.tsv"), os.path.join(meme, "acg-add.tsv")):
                        with open(src, "rb") as f:
                            shutil.copyfileobj(f, out)
            on = [n for n in ("acg", "it", tag, "beauty") if n in names or (native and meme and n == tag)]
            rows, dump = os.path.join(d, "rows.txt"), os.path.join(d, "dump.txt")
            with open(rows, "w", encoding="utf-8") as f:
                f.writelines(f"|{w}|{' '.join(syls)}\n" for w, syls in pairs)
            subprocess.run([B.eval_bin(), "--lm", B.LM, "--profile", profile, "--rows", rows, "--dump", dump, "--packs", ",".join(on), "--packs-dir", d,
                            "--threads", str(B.THREADS), *IT.SHIP_ARGS], check=True, stdout=subprocess.DEVNULL)
            first = {}
            with open(dump, encoding="utf-8") as f:
                for l in f:
                    r, k, w, _ = l.rstrip("\n").split("\t")
                    if k == "1":
                        first[int(r)] = w
            assert len(first) == len(pairs), "the evaluation CLI skipped rows"
            return [first[i + 1] for i in range(len(pairs))]
    return decode


# ---------------------------------------------------------------- 第一段：收進門檻、讀音、X

def prepare(cands, counts, have, packwords, readings=None, readings_tsv=None):
    """去重、用量門檻、讀音。回傳 ({詞: (文章, 看板)}, {詞: 讀音}, {詞: 是否 CHECK}, 統計, 讀不出來的詞)。"""
    st = {"candidates": len(cands)}
    words = bsorted(cands)
    st["dedupe_lexicon"] = sum(w in have for w in words)
    words = [w for w in words if w not in have]
    st["dedupe_packs"] = sum(w in packwords for w in words)
    words = [w for w in words if w not in packwords]
    st["no_count"] = sum(w not in counts for w in words)         # 計數檔沒有這個字串＝沒有論壇 A 用量，不收（例如只出現在迷因網站 A 的候選）
    ok = [w for w in words if w in counts and counts[w][1] >= MIN_BOARDS and counts[w][0] >= MIN_ARTICLES]
    st["below_threshold"] = len(words) - st["no_count"] - len(ok)
    words = ok
    rd = (readings or B.make_readings)(words)
    for w, r in (IT.read_fixed(readings_tsv) if readings_tsv else {}).items():     # 確認過的讀音取代讀音工具的結果，也不再是 CHECK
        if w in words:
            if len(r) != len(w):
                raise SystemExit(f"{readings_tsv}: {w} has {len(r)} syllables")
            rd[w] = (r, False)
    unread = [w for w in words if w not in rd]
    words = [w for w in words if w in rd]
    st["unreadable"] = len(unread)
    st["admitted"] = len(words)
    return {w: counts[w] for w in words}, {w: rd[w][0] for w in words}, {w: rd[w][1] for w in words}, st, unread


def x_rows(reading, decode):
    """每個收進門檻的候選的 (候選, 讀音, X, 書面第一名)。X 是聊天設定的第一名，判斷用（書面靠書面集合的 0 改壞守門）；書面第一名只用在
    「已經打得對」（docs/spec/lexicon-and-packs.md §6.2：聊天與書面的第一名都已經是候選）。"""
    words = bsorted(reading)
    pairs = [(w, reading[w]) for w in words]
    return [(w, " ".join(reading[w]), x, f) for w, x, f in zip(words, decode(pairs, "chat"), decode(pairs, "formal"))]


def write_x(work, rows, tag=TAG):
    os.makedirs(work, exist_ok=True)
    tsv = "".join("\t".join(r) + "\n" for r in rows)
    strings = lines_text(r[2] for r in rows)
    meta = {f"{tag}-x.tsv": IT.sha256_text(tsv), f"{tag}-x-strings.txt": IT.sha256_text(strings)}
    for n, t in ((f"{tag}-x.tsv", tsv), (f"{tag}-x-strings.txt", strings), (f"{tag}-x.json", json.dumps(meta, indent=1, sort_keys=True) + "\n")):
        with open(os.path.join(work, n), "w", encoding="utf-8", newline="\n") as f:
            f.write(t)
    return meta


def read_x(work, tag=TAG):
    """meme-x.tsv → ([(候選, 讀音, X, 書面第一名)], 雜湊)；它或 meme-x-strings.txt 的雜湊和第一段記的不同（有人改過）就中止。"""
    def text(n):
        with open(os.path.join(work, n), encoding="utf-8") as f:
            return f.read()
    if not os.path.exists(os.path.join(work, f"{tag}-x.json")):
        raise SystemExit(f"{work}/{tag}-x.json is missing: run --emit-x first (with the same --work)")
    meta = json.loads(text(f"{tag}-x.json"))
    for n, sha in meta.items():
        if IT.sha256_text(text(n)) != sha:
            raise SystemExit(f"{work}/{n} does not match the SHA-256 --emit-x recorded: it was changed after the first stage")
    return [tuple(l.split("\t")) for l in text(f"{tag}-x.tsv").splitlines()], meta


# ---------------------------------------------------------------- 第二段：讓不讓第一名

def judge(admitted, reading, xs, fs, counts2, packwords=frozenset(), tag=TAG, plus=()):
    """讓不讓第一名的規則。xs：{候選: 聊天的 X}；fs：{候選: 書面的第一名}；counts2：第二次計數 {X 字串: (文章, 看板)}。
    回傳 (留下的詞, 丟掉的 [(候選, 原因, 對方, 候選文章, 對方文章)])。
    同讀音的兩個以上候選只讓文章數最多的（同數照字串）去判，其餘丟掉；plus：處置檔 `+` 列 [(讀音, 詞, 詞)]，兩個詞都在候選裡、讀音相同的那一對兩個都留、各自去判（只有迷因傳）；
    tag=beauty：已經打得對（聊天與書面的第一名都是候選）就不收；迷因照收；
    X 是別的詞包的詞：先出貨的詞包勝，不收（詞包之間的衝突）；其餘看聊天的 X：X 的文章 ≥ 候選的不收，少於才收。聊天已經對、書面沒對的候選：沒有 X 要讓位，詞包只幫書面，收。"""
    best = {}
    for w in sorted(reading, key=lambda w: (-admitted[w][0], w.encode())):
        best.setdefault(tuple(reading[w]), w)

    def paired(w):
        rs = " ".join(reading[w])
        return any(r == rs and w in (a, b) and (b if w == a else a) in reading and " ".join(reading[b if w == a else a]) == rs for r, a, b in plus)
    kept, dropped = [], []
    for w in bsorted(reading):
        a, win = admitted[w][0], best[tuple(reading[w])]
        x = xs[w]
        if w != win and not paired(w):
            dropped.append((w, "same_reading", win, a, admitted[win][0]))
        elif tag == "beauty" and x == w and fs[w] == w:
            dropped.append((w, "already_right", x, a, a))
        elif x == w:
            kept.append(w)
        elif x in packwords:
            dropped.append((w, "x_other_pack", x, a, counts2.get(x, (0, 0))[0]))
        elif x not in counts2:
            raise SystemExit(f"{x!r} (first place of {w!r}) is not in the second counts file: the counts file does not match meme-x-strings.txt")
        elif counts2[x][0] >= a:
            dropped.append((w, "x_not_fewer", x, a, counts2[x][0]))
        else:
            kept.append(w)
    return kept, dropped


def build(cands, counts1, x_table, counts2, decode, readings=None, have=frozenset(), packwords=frozenset(),
          collisions_tsv=None, readings_tsv=None, sc=None, info=None, tag=TAG, exclude=None, native=False, allow=None, extra=()):
    """extra：read_extra 的列（只有迷因）：收進詞包的詞另外的讀音，各多一列（含變調列）、和主要讀音同分，同音衝突照樣偵測；詞沒收進來或和主要讀音相同就停。
    最後一段。x_table：read_x 的列。tag：詞包（meme|beauty），決定檔名與 (b) 的參考名單。exclude：--exclude／--exclude-private 的字串（None＝這個詞包沒有剔除）。native：偵測衝突時把詞包的列寫成 {tag}-add.tsv 交給 decode（--native-pack）。allow：{詞: 類別}（只有迷因；None＝不篩）：不在清單的候選在解碼表核對之後、判規則之前拿掉；info 給了就多放 allow_missing（清單裡沒收進來的詞與原因）與 add_order。回傳 (檔案, manifest, 未處置的 (a)(b) 衝突, 丟掉的 [(候選, 原因, 對方, 候選文章, 對方文章)], 讀不出來的詞, 被 (c) 規則丟掉的 ldrop)。"""
    collisions_tsv = collisions_tsv or os.path.join(PACKS, f"{tag}-collisions.tsv")
    admitted, reading, check, st, unread = prepare(cands, counts1, have, packwords, readings, readings_tsv)
    if sorted((w, " ".join(r)) for w, r in reading.items()) != sorted((w, r) for w, r, *_ in x_table):
        raise SystemExit("the candidates, counts or readings are not the ones meme-x.tsv was made from: run --emit-x again")
    xs, fs = {w: x for w, _, x, _ in x_table}, {w: f for w, _, _, f in x_table}
    admitted0 = admitted
    if allow is not None:
        st["not_allowed"] = sum(w not in allow for w in reading)
        admitted, reading = ({w: v for w, v in d.items() if w in allow} for d in (admitted, reading))
    plus = [(r[0], r[1], r[2][1:]) for r in (B.read_tsv(collisions_tsv) if os.path.exists(collisions_tsv) else []) if r[2].startswith("+")] if tag == TAG else []
    sc = sc or {k: round(v + BONUS, 8) for k, v in B.scores(B.base_lexicon()).items()}
    excluded, decided = B.read_collisions(collisions_tsv)       # 處置檔排除的詞先拿掉，再選同讀音的贏家（被排除的不能擋住同音的詞）
    st[f"excluded_by_{tag}_collisions_tsv"] = sum(w in excluded for w in reading)
    # beauty 的 (b) 參考名單（docs/spec/lexicon-and-packs.md §8.2）：過了門檻、不在詞庫與其他詞包的候選（prepare 已篩），剔除的不算；剔除的詞和處置檔排除的詞同一處拿掉（judge 之前）。
    # 處置檔排除的詞照舊留在參考名單裡（它們只是不進詞包）。meme 的參考名單是全部候選。
    ref = set(admitted) - set(exclude or ()) if tag == "beauty" else (set(cands) & set(allow) if allow is not None else set(cands))
    if exclude is not None:
        st["excluded_by_exclude"] = sum(w in exclude for w in reading)
        excluded = {**excluded, **{w: None for w in exclude}}
    admitted, reading = ({w: v for w, v in d.items() if w not in excluded} for d in (admitted, reading))
    words, dropped = judge(admitted, reading, xs, fs, counts2, packwords, tag, plus)
    ex = collections.defaultdict(list)
    keys = lambda w, syls: [list(syls)] + [v for v in [B.bo.sandhi_variant(w, list(syls))] if v]     # a reading and its 一/不 row, as pack_rows writes them
    for w, r in extra:
        if w in reading and list(reading[w]) == r:
            raise SystemExit(f"extra readings: {w} {' '.join(r)} is the word's main reading")
        if w in reading and any(k in keys(w, reading[w]) for k in keys(w, r)):
            # the extra row (or its 一/不 row) would repeat a key the main reading already writes: two rows of one (reading, word) with different scores
            raise SystemExit(f"extra readings: {w} {' '.join(r)} repeats a row of the word's main reading (its 一/不 variant)")
        if any(k in keys(w, q) for q in ex[w] for k in keys(w, r)):
            # the same between two extra readings of one word: one is the other's 一/不 row, or their 一/不 rows are the same
            raise SystemExit(f"extra readings: {w} {' '.join(r)} repeats a row of another extra reading of the word (its 一/不 variant)")
        ex[w].append(r)
    reasons = collections.Counter(d[1] for d in dropped)
    # (b)：詞包詞搶走參考名單裡別的字串的第一名
    rank = B.collision_rank(B.read_tsv(collisions_tsv) if os.path.exists(collisions_tsv) else [])
    existing = frozenset(have) | packwords
    ldrop = []
    for _ in range(B.LEXICON_RULE_ROUNDS):
        order = B.ordered(words, lambda w: admitted[w][0], rank, reading, extra=ex)
        rows = B.pack_rows(order, reading, sc, extra=ex, tag=tag)
        # (c)：迷因列把任何既有詞（詞庫任何一層或動漫、資訊詞包）擠下第一名就丟（變調列、書面也算；先出貨的勝）。例外：聊天、主要讀音上被擠下的正是 X，
        # 而且 X 不是別的詞包的詞——這一條讀音已經由 judge 的 X 規則判給迷因了。
        found = B.detect_collisions(words, reading, rows, ref, decode, existing=existing, extra=ex, pack_file=f"{tag}-add.tsv" if native else "acg-add.tsv")
        hit = {}
        for r, v in sorted(found.items()):
            for e in v:
                w, cond, prof, o, _ = e
                if cond == "c" and not (prof == "chat" and r == " ".join(reading[w]) and o == xs[w] and o not in packwords):
                    hit.setdefault(w, (r, e))
        if not hit:
            break
        ldrop += [(r, w, o, prof) for w, (r, (_, _, prof, o, _)) in sorted(hit.items())]
        words = [w for w in words if w not in hit]
    else:
        raise SystemExit(f"type (c) collisions not settled after {B.LEXICON_RULE_ROUNDS} rounds")
    missing = [w for w in ex if w not in words]
    if missing:        # 另外的讀音只給收進詞包的詞：詞沒收進來（不在清單、被規則丟掉，含另外的讀音上的 (c)）就停，不寫 data/packs
        why = {w: f"{w} (rule (c): it displaces {o} on {r}, {prof})" for r, w, o, prof in ldrop}
        raise SystemExit(f"extra readings: not shipped in the pack: {'; '.join(why.get(w, w) for w in missing)}")
    if tag == "beauty":
        found = exempt_judged_x(found, reading, xs)
    keep_now = B.first_named(rank, set(words))
    col = B.open_collisions(found, decided, keep_now, set(), frozenset(), {(r.split("\t")[0].replace("-", " "), r.split("\t")[1]) for r in rows})
    srcs = ["word\tsources\t" + ("class\t" if allow is not None else "") + "articles\tboards\tnote\n"]
    for w in order:
        srcs.append(f"{w}\t{','.join(sorted(cands[w]))}\t{allow[w] + chr(9) if allow is not None else ''}{admitted[w][0]}\t{admitted[w][1]}\t{'CHECK' if check[w] else ''}\n")
    manifest = {
        "words": len(words), "rows": len(rows), "bonus": BONUS, "thresholds": {"min_boards": MIN_BOARDS, "min_articles": MIN_ARTICLES},
        "sources": dict(sorted(collections.Counter(s for w in words for s in cands[w]).items())),
        "stages": dict(st, **{"dropped_" + k: reasons.get(k, 0) for k in ("same_reading", "already_right", "x_other_pack", "x_not_fewer")},
                       dropped_by_c_rule=len(ldrop)),
        "unresolved_collision_readings": len(col),
        **({"extra_readings": len(extra)} if extra else {}),
    }
    if info is not None:
        info.update(order=order, reading=reading, check={w: check[w] for w in reading}, extra=list(extra))
    if tag == TAG:      # 列的先後：聊天沒有詞包時打不對的詞，再來本來就對的，最後處置檔 `+` 列的詞；各組內維持原順序，分數不變
        plus_w = {w for _, a, b in plus for w in (a, b)}
        rows = sorted(rows, key=lambda r: 2 if r.split("\t")[1] in plus_w else int(xs[r.split("\t")[1]] == r.split("\t")[1]))
        if info is not None:
            info["add_order"] = list(dict.fromkeys(r.split("\t")[1] for r in rows))
    if info is not None and allow is not None:
        info["allow_missing"] = allow_missing(allow, set(words), cands, have, packwords, admitted0, unread, excluded, dropped, ldrop, counts1)
    return {f"{tag}-add.tsv": "".join(rows), f"{tag}-sources.tsv": "".join(srcs)}, manifest, col, dropped, unread, ldrop


def allow_missing(allow, shipped, cands, have, packwords, admitted0, unread, excluded, dropped, ldrop, counts1):
    """清單裡每個沒收進來的詞 → [(詞, 類別, 原因, 細節)]（清單的順序）。原因照規則的先後；找不到原因的詞不列，收進數加列數就對不上清單，main 會以非 0 結束。"""
    dmap, lmap = {d[0]: d for d in dropped}, {w: (r, o, prof) for r, w, o, prof in ldrop}
    out = []
    for w, cls in allow.items():
        if w in shipped:
            continue
        if w not in cands:
            why = ("not_a_candidate", "")
        elif w in have or w in packwords:
            why = ("in_lexicon_or_pack", "lexicon" if w in have else "pack")
        elif w not in admitted0:
            why = ("unreadable", "") if w in unread else ("below_threshold", "{} articles {} boards".format(*counts1[w]) if w in counts1 else "no count")
        elif w in excluded:
            why = ("excluded_by_collisions_file", excluded[w] or "")
        elif w in dmap:
            d = dmap[w]
            why = (d[1], f"{d[2]} ({d[4]} articles) vs {d[3]}")
        elif w in lmap:
            r, o, prof = lmap[w]
            why = ("rule_c", f"{o} on {r} ({prof})")
        else:
            continue
        out.append((w, cls, *why))
    return out


def exempt_judged_x(found, reading, xs):
    """beauty 的 (b) 豁免（docs/spec/lexicon-and-packs.md §8.2）：聊天、主要讀音上被擠下的第一名就是 judge 為這個讀音比過文章數的那個 X，不算 (b)（同 (c) 的豁免），
    同樣條件的 (c) 也不算未處置；其他讀音（含變調列）、書面、或被擠下的不是 X，照舊列出。"""
    # (c) rows with the same exemption are the ones the (c) loop in build() kept on purpose (the judge gave that reading to the pack word):
    # listing them as open collisions counted settled readings (2026-11-08: 28 of the 34 after the classification rounds)
    keep = lambda r, e: not (e[1] in ("b", "c") and e[2] == "chat" and r == " ".join(reading[e[0]]) and e[3] == xs[e[0]])
    out = {r: [e for e in v if keep(r, e)] for r, v in found.items()}
    return {r: v for r, v in out.items() if v}


def check_words(info, seed=SEED, n=CHECK_N):
    """檢查集：收進詞包的詞（讀音是 CHECK 的不抽，沒人確認過的讀音沒對不能算詞包的錯）用固定種子抽 n 個，不到 n 個就全收。列的格式：前文|句子|讀音。"""
    pool = [w for w in bsorted(info["order"]) if not info["check"][w]]
    return [f"|{w}|{' '.join(info['reading'][w])}\n" for w in random.Random(seed).sample(pool, min(n, len(pool)))]


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pack", choices=sorted(SEEDS), default="meme")
    ap.add_argument("--cand", nargs="+", required=True)
    ap.add_argument("--counts", nargs="+", default=[])
    ap.add_argument("--emit-candidates", action="store_true")
    ap.add_argument("--emit-x", action="store_true")
    ap.add_argument("--work")
    ap.add_argument("--out", default=PACKS)
    ap.add_argument("--collisions")
    ap.add_argument("--readings")
    ap.add_argument("--native-pack", action="store_true", help="只有 --pack meme、最後一段：衝突偵測「開」的那次用真的 --packs acg,it,meme,beauty，不把列接在 it 檔後面")
    ap.add_argument("--allow", help="只有 --pack meme：審定清單 `詞<TAB>類別`（meme|offensive），預設 data/packs/meme-allow.tsv；--emit-x 與最後一段都要它存在")
    ap.add_argument("--extra-readings", help="只有 --pack meme、最後一段：另外的讀音 `詞<TAB>讀音`，預設 data/packs/meme-extra-readings.tsv；檔不存在就沒有")
    ap.add_argument("--exclude", help="字串<TAB>…（只讀第一欄）；beauty 預設 data/packs/beauty-exclude.tsv（不存在就是空集合），meme 預設沒有")
    ap.add_argument("--exclude-private", help="同 --exclude；beauty.json 只記它的 SHA-256 與筆數，不記路徑與內容")
    a = ap.parse_args()
    tag = a.pack
    if a.native_pack and tag != TAG:
        sys.exit("--native-pack only applies to --pack meme (the beauty decode has no pack bit of its own to turn on)")
    if tag != TAG and a.allow:
        sys.exit("--allow only applies to --pack meme")
    if tag != TAG and a.extra_readings:
        sys.exit("--extra-readings only applies to --pack meme")
    if tag == TAG and (a.exclude or a.exclude_private):
        sys.exit("--pack meme does not take --exclude or --exclude-private: the allow list (--allow) is the only way to leave a candidate out")
    a.work = a.work or os.path.join(ROOT, "build", f"{tag}-pack")
    a.collisions = a.collisions or os.path.join(PACKS, f"{tag}-collisions.tsv")
    a.readings = a.readings or os.path.join(PACKS, f"{tag}-readings.tsv")
    cands, cst, cfiles = read_candidates(a.cand)
    names = [os.path.basename(p) for p in cfiles]          # docs/spec/lexicon-and-packs.md §8.2：candidate_files 只記檔名，私人路徑不進公開檔
    if len(set(names)) != len(names):
        sys.exit(f"--pack {tag}: the --cand files must have different names ({tag}.json records them by name)")
    cfiles = {os.path.basename(p): h for p, h in cfiles.items()}
    cand_text = lines_text(cands)
    os.makedirs(a.work, exist_ok=True)
    if a.emit_candidates:
        with open(os.path.join(a.work, f"{tag}-candidates.txt"), "w", encoding="utf-8", newline="\n") as f:
            f.write(cand_text)
        print(f"candidates {len(cands)} ({cst}); wrote {a.work}/{tag}-candidates.txt sha256 {IT.sha256_text(cand_text)}")
        return
    need = 1 if a.emit_x else 2
    if len(a.counts) != need:
        sys.exit(f"{'--emit-x' if a.emit_x else 'the final stage'} takes {need} counts file(s) (candidates{'' if a.emit_x else ', then X'})")
    allow, allow_sha = read_allow(a.allow or os.path.join(PACKS, "meme-allow.tsv")) if tag == TAG else (None, None)     # --emit-x only checks it parses: its outputs do not depend on the list
    h1, counts1 = read_counts(a.counts[0])
    if h1["input_sha256"] != IT.sha256_text(cand_text):
        sys.exit(f"{a.counts[0]}: input_sha256 is not that of the candidates (--emit-candidates) these --cand files give")
    IT.check_shipping_lm()
    have, packwords = have_words(), (pack_words() if tag == TAG else pack_words(tag=tag))      # meme: the call the existing tests stub
    dec = decoder(tag=tag, native=a.native_pack)
    if a.emit_x:
        _, reading, _, st, unread = prepare(cands, counts1, have, packwords, B.make_readings, a.readings)
        meta = write_x(a.work, x_rows(reading, dec), tag)
        print(f"{st}\nwrote {a.work}/{tag}-x.tsv, {tag}-x-strings.txt, {tag}-x.json: {meta}")
        return
    x_table, xmeta = read_x(a.work, tag)
    h2, counts2 = read_counts(a.counts[1])
    if h2["input_sha256"] != xmeta[f"{tag}-x-strings.txt"]:
        sys.exit(f"{a.counts[1]}: input_sha256 is not that of {tag}-x-strings.txt")
    if h2["tree_sha256"] != h1["tree_sha256"]:
        sys.exit("the two counts files were made from different page directories (tree_sha256)")
    # --exclude：beauty 預設 data/packs/beauty-exclude.tsv（沒有就空集合）；明給的檔一定要存在。meme 沒給就沒有剔除，輸出不變。
    default_exclude = os.path.join(PACKS, "beauty-exclude.tsv") if tag == "beauty" else None
    ex, ex_sha = read_exclude(a.exclude or default_exclude, must_exist=bool(a.exclude))
    exp, exp_sha = read_exclude(a.exclude_private)
    exclude = (ex | exp) if (tag == "beauty" or a.exclude or a.exclude_private) else None
    info = {}
    files, manifest, col, dropped, unread, ldrop = build(cands, counts1, x_table, counts2, dec, have=have, packwords=packwords, collisions_tsv=a.collisions,
                                                           readings_tsv=a.readings, info=info, tag=tag, exclude=exclude, native=a.native_pack, allow=allow,
                                                           extra=read_extra(a.extra_readings or os.path.join(PACKS, "meme-extra-readings.tsv")) if tag == TAG else ())
    manifest.update(version=IT.sha256_text(files[f"{tag}-add.tsv"])[:8], candidate_files=cfiles, candidate_stats=cst,
                    counts_candidates=dict(h1, sha256=B.sha256_file(a.counts[0])), counts_x=dict(h2, sha256=B.sha256_file(a.counts[1])),
                    dropped_unreadable=len(unread))
    manifest[f"{tag}_x"] = xmeta
    if ex_sha:
        manifest["exclude"] = {"sha256": ex_sha, "strings": len(ex)}
    if exp_sha:
        manifest["exclude_private"] = {"sha256": exp_sha, "strings": len(exp)}
    if allow is not None:
        shipped = collections.Counter(allow[w] for w in info["order"])
        manifest["allow"] = {"file": os.path.basename(a.allow or "meme-allow.tsv"), "sha256": allow_sha, "rows": len(allow), "shipped": {c: shipped.get(c, 0) for c in ALLOW_CLASSES}}
    checks = sorted((w, " ".join(info["reading"][w])) for w, c in info["check"].items() if c)
    report = {"dropped.tsv": "candidate\treason\tother\tcandidate_articles\tother_articles\n" + "".join("\t".join(map(str, d)) + "\n" for d in dropped),
              "collisions.txt": B.collisions_text(col, {"unclassified_groups": []}, ldrop), "unreadable.txt": "".join(w + "\n" for w in unread),
              "check-readings.txt": "".join(f"{w}\t{r}\n" for w, r in checks), "words.txt": "".join(check_words(info, SEEDS[tag])),
              **({"allow-missing.tsv": "".join("\t".join(r) + "\n" for r in info["allow_missing"]),
                  "words-all.txt": "".join(f"|{w}|{' '.join(info['reading'][w])}\n" for w in info["add_order"] if not info["check"][w])
                                    + "".join(f"|{w}|{' '.join(r)}\n" for w, r in info["extra"])} if allow is not None else {}),
              "report.txt": json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True) + "\n"}
    for n, t in report.items():
        with open(os.path.join(a.work, n), "w", encoding="utf-8", newline="\n") as f:
            f.write(t)
    print(report["report.txt"], end="")
    if allow is not None and len(info["order"]) + len(info["allow_missing"]) != len(allow):
        sys.exit(f"STOP: {len(info['order'])} shipped + {len(info['allow_missing'])} in allow-missing.tsv != {len(allow)} allow-list words; nothing written to {a.out}")
    min_words = MIN_WORDS if tag == TAG else MIN_WORDS_BEAUTY
    if manifest["words"] < min_words:      # 停止條件：先回報各來源的數量再問；data/packs 一個檔都不寫
        sys.exit(f"STOP: {manifest['words']} words, fewer than {min_words}; nothing written to {a.out}; report the per-source counts before going on")
    IT.write_all(files, manifest, a.out, f"{tag}.json")


if __name__ == "__main__":
    main()
