"""迷因詞包建置工具（契約 docs/contracts/meme-pack.md §2）：由候選檔、兩份 PTT 用量計數檔與出貨的 data/lm，
產生 data/packs/meme-add.tsv（疊加層格式，標籤 meme）、meme-sources.tsv、meme.json，報告放 build/meme-pack/。
分數、讀音、排序、同音衝突偵測共用 tools/build_acg_pack.py；出貨 data/lm 的核對、讀音覆寫檔、寫檔共用 tools/build_it_pack.py（import）。
全程不連網。輸出以 CC BY-SA 4.0 釋出（LICENSES/data.md，由後續的引擎與 App PR 加）。同一份輸入建兩次，輸出逐位元組相同。

候選檔（--cand，可多個，一個檔一個來源）：UTF-8 文字，一行一個，`字串<TAB>來源`；來源欄可省（用檔名去掉副檔名），`#` 開頭與空行略過。
  字串含 ASCII 英數的整串不收（數量列在報告）；其餘照標點（任何非漢字）切開，每段各自當候選；只收 2–10 個漢字的段。
計數檔（--counts）：experiments/names-creators/count_usage.py 的輸出。前三行檔頭 `# ptt_tree_sha256 X`、`# input_sha256 Y`、`# tool_commit Z`，
  之後每列 `字串<TAB>文章數<TAB>看板數`。三個檔頭欄位都記進 meme.json。計數檔的 input_sha256 要等於它的輸入字串檔的 SHA-256：
  第一次計數的輸入是本工具 `--emit-candidates` 寫的 meme-candidates.txt，第二次是 `--emit-x` 寫的 meme-x-strings.txt；兩份計數檔的 PTT 目錄雜湊要相同。

三個階段（每一階段都用 --cand；讀音覆寫檔 --readings 同詞包目錄的 meme-readings.tsv，格式同 it-readings.tsv，沒有就沒有）：
  1. --emit-candidates                 寫 WORK/meme-candidates.txt（一行一個，去重、照位元組排序）給 main 做第一次計數
  2. --emit-x --counts C1              收進門檻（看板 ≥ 3 且文章 ≥ 10）的候選，用出貨 CLI（動漫、資訊詞包開，沒有迷因）解碼，
                                       寫 WORK/meme-x.tsv（候選、讀音、聊天的第一名 X、書面的第一名；書面的只用在「已經打得對」）、
                                       meme-x-strings.txt（一行一個 X，去重、照位元組排序）、meme-x.json（兩者的 SHA-256）
  3. --counts C1 C2                    核對 meme-x.tsv 的雜湊，讀第二次計數（對 meme-x-strings.txt）後依「讓不讓第一名」的規則建置
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
MIN_BOARDS, MIN_ARTICLES = 3, 10    # 契約 §2「用量」：事先固定，量之前 commit
MIN_WORDS = 30                      # 契約 §5 停止：收錄後不到 30 個詞就停
BONUS = 3.0                         # 契約 §2「分數」：同資訊詞包，每列的 log10 分數加 3
SEED, CHECK_N = 20261025, 60        # 契約 §3.5：檢查集的固定種子與抽樣數
HEAD_FIELDS = ("ptt_tree_sha256", "input_sha256", "tool_commit")
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


def pack_words(packs_dir=PACKS):
    """data/packs 裡每個詞包檔（動漫、資訊、經典台詞…，除了本詞包自己）的詞。"""
    out = set()
    for f in sorted(glob.glob(os.path.join(packs_dir, "*-add.tsv"))):
        if os.path.basename(f) != "meme-add.tsv":
            with open(f, encoding="utf-8") as fh:
                out |= {l.split("\t")[1] for l in fh if l.strip() and not l.startswith("#")}
    return frozenset(out)


# ---------------------------------------------------------------- 解碼（出貨的 data/lm；動漫、資訊詞包開）

def decoder(packs_dir=PACKS):
    """decode(pairs, 設定, meme=None)：出貨 CLI 解碼每個 (詞, 讀音)，`--packs acg,it`，回傳第一名；旗標同資訊詞包的解碼器（THREADS、SHIP_ARGS）。
    meme 給了（detect_collisions 寫的臨時資料夾，裡面的 acg-add.tsv 是要測的迷因列）就把那些列接在資訊詞包後面一起載入——CLI 還沒有 meme 詞包，
    同格式的列走資訊詞包的路徑（近似；引擎 PR 之後再用 PACK_MEME 重驗）。"""
    for name in ("acg", "it"):
        if not os.path.exists(os.path.join(packs_dir, name + "-add.tsv")):
            raise SystemExit(f"{packs_dir}/{name}-add.tsv is missing (the shipping decode needs the acg and it packs)")

    def decode(pairs, profile, meme=None):
        with tempfile.TemporaryDirectory() as d:
            shutil.copy(os.path.join(packs_dir, "acg-add.tsv"), os.path.join(d, "acg-add.tsv"))
            with open(os.path.join(d, "it-add.tsv"), "wb") as out:
                for src in (os.path.join(packs_dir, "it-add.tsv"), os.path.join(meme, "acg-add.tsv") if meme else None):
                    if src:
                        with open(src, "rb") as f:
                            shutil.copyfileobj(f, out)
            rows, dump = os.path.join(d, "rows.txt"), os.path.join(d, "dump.txt")
            with open(rows, "w", encoding="utf-8") as f:
                f.writelines(f"|{w}|{' '.join(syls)}\n" for w, syls in pairs)
            subprocess.run([B.eval_bin(), "--lm", B.LM, "--profile", profile, "--rows", rows, "--dump", dump, "--packs", "acg,it", "--packs-dir", d,
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
    st["no_count"] = sum(w not in counts for w in words)         # 計數檔沒有這個字串＝沒有 PTT 用量，不收（例如只有街頭字典的候選）
    ok = [w for w in words if w in counts and counts[w][1] >= MIN_BOARDS and counts[w][0] >= MIN_ARTICLES]
    st["below_threshold"] = len(words) - st["no_count"] - len(ok)
    words = ok
    rd = (readings or B.make_readings)(words)
    for w, r in (IT.read_fixed(readings_tsv) if readings_tsv else {}).items():     # 維護者確認過的讀音取代讀音工具的結果，也不再是 CHECK
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
    """每個收進門檻的候選的 (候選, 讀音, X, 書面第一名)。X 是聊天設定的第一名，判斷用（契約 §2，書面靠 §3.4 守門）；書面第一名只用在
    「已經打得對」（it-pack §2：聊天與書面的第一名都已經是候選）。"""
    words = bsorted(reading)
    pairs = [(w, reading[w]) for w in words]
    return [(w, " ".join(reading[w]), x, f) for w, x, f in zip(words, decode(pairs, "chat"), decode(pairs, "formal"))]


def write_x(work, rows):
    os.makedirs(work, exist_ok=True)
    tsv = "".join("\t".join(r) + "\n" for r in rows)
    strings = lines_text(r[2] for r in rows)
    meta = {"meme-x.tsv": IT.sha256_text(tsv), "meme-x-strings.txt": IT.sha256_text(strings)}
    for n, t in (("meme-x.tsv", tsv), ("meme-x-strings.txt", strings), ("meme-x.json", json.dumps(meta, indent=1, sort_keys=True) + "\n")):
        with open(os.path.join(work, n), "w", encoding="utf-8", newline="\n") as f:
            f.write(t)
    return meta


def read_x(work):
    """meme-x.tsv → ([(候選, 讀音, X, 書面第一名)], 雜湊)；它或 meme-x-strings.txt 的雜湊和第一段記的不同（有人改過）就中止。"""
    def text(n):
        with open(os.path.join(work, n), encoding="utf-8") as f:
            return f.read()
    if not os.path.exists(os.path.join(work, "meme-x.json")):
        raise SystemExit(f"{work}/meme-x.json is missing: run --emit-x first (with the same --work)")
    meta = json.loads(text("meme-x.json"))
    for n, sha in meta.items():
        if IT.sha256_text(text(n)) != sha:
            raise SystemExit(f"{work}/{n} does not match the SHA-256 --emit-x recorded: it was changed after the first stage")
    return [tuple(l.split("\t")) for l in text("meme-x.tsv").splitlines()], meta


# ---------------------------------------------------------------- 第二段：讓不讓第一名

def judge(admitted, reading, xs, fs, counts2, packwords=frozenset()):
    """契約 §2「讓不讓第一名」。xs：{候選: 聊天的 X}；fs：{候選: 書面的第一名}；counts2：第二次計數 {X 字串: (文章, 看板)}。
    回傳 (留下的詞, 丟掉的 [(候選, 原因, 對方, 候選文章, 對方文章)])。
    同讀音的兩個以上候選只讓文章數最多的（同數照字串）去判，其餘丟掉；已經打得對（it-pack §2：聊天與書面的第一名都是候選）就不收；
    X 是別的詞包的詞：先出貨的詞包勝，不收（契約 §2 衝突）；其餘看聊天的 X：X 的文章 ≥ 候選的不收，少於才收。聊天已經對、書面沒對的候選：沒有 X 要讓位，詞包只幫書面，收。"""
    best = {}
    for w in sorted(reading, key=lambda w: (-admitted[w][0], w.encode())):
        best.setdefault(tuple(reading[w]), w)
    kept, dropped = [], []
    for w in bsorted(reading):
        a, win = admitted[w][0], best[tuple(reading[w])]
        x = xs[w]
        if w != win:
            dropped.append((w, "same_reading", win, a, admitted[win][0]))
        elif x == w and fs[w] == w:
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
          collisions_tsv=os.path.join(PACKS, "meme-collisions.tsv"), readings_tsv=None, sc=None, info=None):
    """最後一段。x_table：read_x 的列。回傳 (檔案, manifest, 未處置的 (a)(b) 衝突, 丟掉的 [(候選, 原因, 對方, 候選文章, 對方文章)], 讀不出來的詞, 被 (c) 規則丟掉的 ldrop)。"""
    admitted, reading, check, st, unread = prepare(cands, counts1, have, packwords, readings, readings_tsv)
    if sorted((w, " ".join(r)) for w, r in reading.items()) != sorted((w, r) for w, r, *_ in x_table):
        raise SystemExit("the candidates, counts or readings are not the ones meme-x.tsv was made from: run --emit-x again")
    xs, fs = {w: x for w, _, x, _ in x_table}, {w: f for w, _, _, f in x_table}
    sc = sc or {k: round(v + BONUS, 8) for k, v in B.scores(B.base_lexicon()).items()}
    excluded, decided = B.read_collisions(collisions_tsv)       # 處置檔排除的詞先拿掉，再選同讀音的贏家（被排除的不能擋住同音的詞）
    st["excluded_by_meme_collisions_tsv"] = sum(w in excluded for w in reading)
    admitted, reading = ({w: v for w, v in d.items() if w not in excluded} for d in (admitted, reading))
    words, dropped = judge(admitted, reading, xs, fs, counts2, packwords)
    reasons = collections.Counter(d[1] for d in dropped)
    ref = set(cands)                    # 參考名單：所有候選（切段後）。(b)：迷因詞搶走參考名單裡別的字串的第一名
    rank = B.collision_rank(B.read_tsv(collisions_tsv) if os.path.exists(collisions_tsv) else [])
    existing = frozenset(have) | packwords
    ldrop = []
    for _ in range(B.LEXICON_RULE_ROUNDS):
        order = B.ordered(words, lambda w: admitted[w][0], rank, reading)
        rows = B.pack_rows(order, reading, sc, tag=TAG)
        # (c)：迷因列把任何既有詞（詞庫任何一層或動漫、資訊詞包）擠下第一名就丟（變調列、書面也算；先出貨的勝）。例外：聊天、主要讀音上被擠下的正是 X，
        # 而且 X 不是別的詞包的詞——這一條讀音已經由 judge 的 X 規則判給迷因了。
        found = B.detect_collisions(words, reading, rows, ref, decode, existing=existing)
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
    keep_now = B.first_named(rank, set(words))
    col = B.open_collisions(found, decided, keep_now, set(), frozenset(), {(r.split("\t")[0].replace("-", " "), r.split("\t")[1]) for r in rows})
    srcs = ["word\tsources\tptt_articles\tptt_boards\tnote\n"]
    for w in order:
        srcs.append(f"{w}\t{','.join(sorted(cands[w]))}\t{admitted[w][0]}\t{admitted[w][1]}\t{'CHECK' if check[w] else ''}\n")
    manifest = {
        "words": len(words), "rows": len(rows), "bonus": BONUS, "thresholds": {"min_boards": MIN_BOARDS, "min_articles": MIN_ARTICLES},
        "sources": dict(sorted(collections.Counter(s for w in words for s in cands[w]).items())),
        "stages": dict(st, **{"dropped_" + k: reasons.get(k, 0) for k in ("same_reading", "already_right", "x_other_pack", "x_not_fewer")},
                       dropped_by_c_rule=len(ldrop)),
        "unresolved_collision_readings": len(col),
    }
    if info is not None:
        info.update(order=order, reading=reading, check={w: check[w] for w in reading})
    return {"meme-add.tsv": "".join(rows), "meme-sources.tsv": "".join(srcs)}, manifest, col, dropped, unread, ldrop


def check_words(info, seed=SEED, n=CHECK_N):
    """契約 §3.5 的檢查集：收進詞包的詞（讀音是 CHECK 的不抽，沒人確認過的讀音沒對不能算詞包的錯）用固定種子抽 n 個，不到 n 個就全收。列的格式：前文|句子|讀音。"""
    pool = [w for w in bsorted(info["order"]) if not info["check"][w]]
    return [f"|{w}|{' '.join(info['reading'][w])}\n" for w in random.Random(seed).sample(pool, min(n, len(pool)))]


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cand", nargs="+", required=True)
    ap.add_argument("--counts", nargs="+", default=[])
    ap.add_argument("--emit-candidates", action="store_true")
    ap.add_argument("--emit-x", action="store_true")
    ap.add_argument("--work", default=os.path.join(ROOT, "build", "meme-pack"))
    ap.add_argument("--out", default=PACKS)
    ap.add_argument("--collisions", default=os.path.join(PACKS, "meme-collisions.tsv"))
    ap.add_argument("--readings", default=os.path.join(PACKS, "meme-readings.tsv"))
    a = ap.parse_args()
    cands, cst, cfiles = read_candidates(a.cand)
    cand_text = lines_text(cands)
    os.makedirs(a.work, exist_ok=True)
    if a.emit_candidates:
        with open(os.path.join(a.work, "meme-candidates.txt"), "w", encoding="utf-8", newline="\n") as f:
            f.write(cand_text)
        print(f"candidates {len(cands)} ({cst}); wrote {a.work}/meme-candidates.txt sha256 {IT.sha256_text(cand_text)}")
        return
    need = 1 if a.emit_x else 2
    if len(a.counts) != need:
        sys.exit(f"{'--emit-x' if a.emit_x else 'the final stage'} takes {need} counts file(s) (candidates{'' if a.emit_x else ', then X'})")
    h1, counts1 = read_counts(a.counts[0])
    if h1["input_sha256"] != IT.sha256_text(cand_text):
        sys.exit(f"{a.counts[0]}: input_sha256 is not that of the candidates (--emit-candidates) these --cand files give")
    IT.check_shipping_lm()
    have, packwords = have_words(), pack_words()
    dec = decoder()
    if a.emit_x:
        _, reading, _, st, unread = prepare(cands, counts1, have, packwords, B.make_readings, a.readings)
        meta = write_x(a.work, x_rows(reading, dec))
        print(f"{st}\nwrote {a.work}/meme-x.tsv, meme-x-strings.txt, meme-x.json: {meta}")
        return
    x_table, xmeta = read_x(a.work)
    h2, counts2 = read_counts(a.counts[1])
    if h2["input_sha256"] != xmeta["meme-x-strings.txt"]:
        sys.exit(f"{a.counts[1]}: input_sha256 is not that of meme-x-strings.txt")
    if h2["ptt_tree_sha256"] != h1["ptt_tree_sha256"]:
        sys.exit("the two counts files were made from different PTT page directories (ptt_tree_sha256)")
    info = {}
    files, manifest, col, dropped, unread, ldrop = build(cands, counts1, x_table, counts2, dec, have=have, packwords=packwords, collisions_tsv=a.collisions,
                                                           readings_tsv=a.readings, info=info)
    manifest.update(version=IT.sha256_text(files["meme-add.tsv"])[:8], candidate_files=cfiles, candidate_stats=cst,
                    counts_candidates=dict(h1, sha256=B.sha256_file(a.counts[0])), counts_x=dict(h2, sha256=B.sha256_file(a.counts[1])),
                    meme_x=xmeta, dropped_unreadable=len(unread))
    checks = sorted((w, " ".join(info["reading"][w])) for w, c in info["check"].items() if c)
    report = {"dropped.tsv": "candidate\treason\tother\tcandidate_articles\tother_articles\n" + "".join("\t".join(map(str, d)) + "\n" for d in dropped),
              "collisions.txt": B.collisions_text(col, {"unclassified_groups": []}, ldrop), "unreadable.txt": "".join(w + "\n" for w in unread),
              "check-readings.txt": "".join(f"{w}\t{r}\n" for w, r in checks), "words.txt": "".join(check_words(info)),
              "report.txt": json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True) + "\n"}
    for n, t in report.items():
        with open(os.path.join(a.work, n), "w", encoding="utf-8", newline="\n") as f:
            f.write(t)
    print(report["report.txt"], end="")
    if manifest["words"] < MIN_WORDS:      # 停止條件：先回報各來源的數量再問；data/packs 一個檔都不寫
        sys.exit(f"STOP (contract section 5): {manifest['words']} words, fewer than {MIN_WORDS}; nothing written to {a.out}; report the per-source counts before going on")
    IT.write_all(files, manifest, a.out, "meme.json")


if __name__ == "__main__":
    main()
