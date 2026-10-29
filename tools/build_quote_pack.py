"""經典台詞詞包建置工具（契約 docs/contracts/quote-pack.md §1、§2）：由人工核對過的台詞清單 data/packs/quotes-manual.tsv，
產生 data/packs/quotes-add.tsv（疊加層格式）與 quotes.json，報告寫進 build/quote-pack/report.txt。
核對來源欄空白（或寫「待核對」）的列不收，列在報告裡。同一份清單跑兩次，輸出逐位元組相同。

清單欄位（tab 分隔，# 開頭是註解）：台詞原文、出處、年份、核對來源、收錄人、選填的讀音（各分句的注音以 `｜` 分隔，分句之間可留空白交給讀音工具）。
分句：在標點與空白處切開，每個分句 2–10 個漢字才收；11 字以上、1 字、含非漢字的分句列在報告，不收。
讀音靠 tools/readings.py（第 6 欄有就以它為準，標 CHECK 的列在報告）；分數、變調列、同音順序和 ACG 詞包共用 build_overlay／build_acg_pack 的函式。
同音衝突只有一種處理（契約 §2 衝突）：開詞包後某個讀音的第一名由既有詞庫的詞變成台詞分句，既有詞勝，那個分句不收。

用法：python3 tools/build_quote_pack.py [--list FILE] [--out DIR] [--report DIR] [--acg-dir DIR]
解碼靠評測 CLI（target/release/shanjie-eval）與出貨的 data/lm（雜湊見 SHIPPED_LM_SHA，開始前核對）；「不開」帶 ACG 與資訊詞包（--packs acg,it）、「開」再加台詞（acg,quotes,it），照出貨；
quotes.json 記下解碼用的模型與 ACG、資訊詞包檔的雜湊。
"""
import argparse
import collections
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import build_acg_pack as bap  # noqa: E402  分數、讀音、排序、衝突偵測、評測 CLI 的路徑

bo = bap.bo         # build_overlay：變調列
PACKS = bap.PACKS
LIST = os.path.join(PACKS, "quotes-manual.tsv")
TAG = "quotes"
PACK_FILE = "quotes-add.tsv"
MIN_LEN, MAX_LEN = 2, 10          # 契約 §2：分數規則（length_scores）只涵蓋 2–10 字，引擎每個位置試的詞長也由詞庫最長的詞決定
UNCHECKED = frozenset({"", "待核對"})   # 核對來源欄：空白或起始清單的「待核對」都是還沒核對（契約 §1）
LEXICON_RULE_ROUNDS = 5           # 丟一個分句只會讓少數鄰居改變，實測幾輪就穩（和 ACG 詞包相同）
# 契約 §2 解碼設定：出貨的 data/lm，雜湊照 data/*.sha256（換模型時不必改這裡），開始前核對；
# 聊天讀 bigram、classes、kn 與 trigram-chat.sjt3，書面讀 trigram.sjt3，這五個檔決定出貨設定下的第一名。
SHIPPED_LM_FILES = ("bigram.sjlm", "classes.sjc", "kn.sjkn", "trigram.sjt3", "trigram-chat.sjt3")
DECODE_PACKS = ("acg-add.tsv", "it-add.tsv")     # 「不開」「開」兩次解碼都帶的出貨詞包（預設開的兩個）
REBUILD = "python3 tools/build_quote_pack.py"


def shipped_lm_sha():
    """{檔名: 雜湊}，讀 data/*.sha256；缺檔就中止並說明（不在 import 時開檔）。"""
    out = {}
    for n in SHIPPED_LM_FILES:
        p = os.path.join(bap.ROOT, "data", n + ".sha256")
        if not os.path.exists(p):
            raise SystemExit(f"{p} is missing; the model hashes come from data/*.sha256 (fetch the models with scripts/fetch-models.sh)")
        out[n] = open(p).read().split()[0]
    return out


# 漢字：基本區、擴充 A、相容區、〇，以及擴充 B 以後（讀音工具讀不出的字照樣進「讀音拼不出」的報告，不當成非漢字）。
HAN = re.compile(r"^[〇㐀-䶿一-鿿豈-\uFAFF\U00020000-\U0003FFFF]+$")
# 標點與空白（含全形與半形的引號、刪節號、破折號）。不在這裡也不是漢字的字元（英文、數字、符號）留在分句裡，讓那個分句被擋下。
PUNCT = re.compile(r"[\s，。！？、；：…⋯—─–·・‧．／/～~,.!?;:\"'“”‘’「」『』（）()《》〈〉【】〔〕\[\]<>\-]+")


def sha256_file(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def check_shipped_lm(directory=os.path.join(bap.ROOT, "data", "lm")):
    for name, sha in shipped_lm_sha().items():
        path = os.path.join(directory, name)
        if not os.path.exists(path) or sha256_file(path) != sha:
            raise SystemExit(f"{path} is missing or its SHA-256 is not {sha}; the build decodes with the shipped model")


def split_clauses(text):
    return [c for c in PUNCT.split(text) if c]


def read_list(path):
    """清單 → [(行號, 原文, 出處, 年份, 核對來源, 收錄人, [各分句的讀音或 None])]。少於五欄、原文空白、讀音的分句數超過原文的分句數、
    某個分句的讀音音節數和字數對不上（分句是 2–10 個漢字的才檢查；其他分句不收，讀音也不用），都中止並指出行號。"""
    out = []
    with open(path, encoding="utf-8-sig") as f:
        lines = f.read().splitlines()
    for n, line in enumerate(lines, 1):
        if line.startswith("#") or not line.strip():
            continue
        f = line.split("\t")
        if len(f) < 5 or not f[0].strip():
            raise SystemExit(f"{path}:{n}: bad quote row (need quote, work, year, source, contributor)")
        f = [x.strip() for x in f] + [""] * (6 - len(f))
        clauses = split_clauses(f[0])
        reads = [r.strip() for r in f[5].split("｜")] if f[5] else []
        if len(reads) > len(clauses):
            raise SystemExit(f"{path}:{n}: {len(reads)} readings for {len(clauses)} clauses")
        for c, r in zip(clauses, reads):
            if r and HAN.match(c) and MIN_LEN <= len(c) <= MAX_LEN and len(r.split()) != len(c):
                raise SystemExit(f"{path}:{n}: reading of {c!r} has {len(r.split())} syllables")
        out.append((n, f[0], f[1], f[2], f[3], f[4], [r.split() if r else None for r in reads] + [None] * (len(clauses) - len(reads))))
    return out


def file_words(path):
    with open(path, encoding="utf-8") as f:
        return {l.split("\t")[1] for l in f if l.strip()}


def cli_decode(acg_dir, lm=bap.LM):
    """評測 CLI 的解碼器，簽名同 `bap.top1`：packs 是 None 就開 ACG 與資訊詞包（acg_dir 裡的 acg-add.tsv、it-add.tsv），給了就是放著 quotes-add.tsv 的資料夾，再加台詞詞包（照出貨）。"""
    def decode(pairs, profile, packs=None):
        with tempfile.TemporaryDirectory() as d:
            pdir, names = acg_dir, "acg,it"
            if packs:
                pdir, names = os.path.join(d, "packs"), "acg,quotes,it"
                os.mkdir(pdir)
                shutil.copy(os.path.join(packs, PACK_FILE), pdir)
                for f in DECODE_PACKS:
                    os.symlink(os.path.abspath(os.path.join(acg_dir, f)), os.path.join(pdir, f))
            rows, dump = os.path.join(d, "rows.txt"), os.path.join(d, "dump.txt")
            with open(rows, "w", encoding="utf-8") as f:
                f.writelines(f"|{w}|{' '.join(syls)}\n" for w, syls in pairs)
            subprocess.run([bap.eval_bin(), "--lm", lm, "--profile", profile, "--rows", rows, "--dump", dump, "--packs", names, "--packs-dir", pdir],
                           check=True, stdout=subprocess.DEVNULL)
            first = {}
            with open(dump, encoding="utf-8") as f:
                for l in f:
                    r, k, s, _ = l.rstrip("\n").split("\t")
                    if k == "1":
                        first[int(r)] = s
            assert len(first) == len(pairs), "the evaluation CLI skipped rows"
            return [first[i + 1] for i in range(len(pairs))]
    return decode


def read_pairs(words, reading):
    """每個分句的 (詞, 讀音)，含「一」「不」的變調讀音（契約 §2：衝突檢查與報告的解碼都包含變調讀音）。"""
    pairs = []
    for w in words:
        pairs.append((w, reading[w]))
        var = bo.sandhi_variant(w, reading[w])
        if var:
            pairs.append((w, var))
    return pairs


def first_places(words, reading, rows, decode):
    """{(分句, 讀音): {設定: (不開, 開)}}：開著 ACG 詞包的基準，與再開台詞詞包的第一名（聊天與書面）。"""
    pairs = read_pairs(words, reading)
    out = {(w, " ".join(s)): {} for w, s in pairs}
    if not pairs:
        return out
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, PACK_FILE), "w", encoding="utf-8") as f:
            f.writelines(rows)
        for prof in ("chat", "formal"):
            for (w, s), off, on in zip(pairs, decode(pairs, prof), decode(pairs, prof, d)):
                out[(w, " ".join(s))][prof] = (off, on)
    return out


def build(list_path=LIST, acg_dir=PACKS, decode=None, readings=bap.make_readings, have=None, acg_words=None, it_words=None):
    """回傳 (files, manifest, report)。have：基底、overlay-add.tsv、sandhi-add.tsv、names-add.tsv 的詞（預設照 repo）；acg_words、it_words：ACG、資訊詞包的詞（預設讀 acg_dir 裡的檔）。"""
    decode = decode or cli_decode(acg_dir)
    have = bap.lexicon_words() if have is None else have
    acg_words = file_words(os.path.join(acg_dir, "acg-add.tsv")) if acg_words is None else acg_words
    it_words = file_words(os.path.join(acg_dir, "it-add.tsv")) if it_words is None else it_words
    existing = frozenset(have) | frozenset(acg_words) | frozenset(it_words)     # 契約 §2「既有詞」：基底、overlay、sandhi、names、ACG 詞包、資訊詞包
    quotes = read_list(list_path)
    checked = [q for q in quotes if q[4] not in UNCHECKED]
    src, override = collections.defaultdict(set), {}          # 分句 → 出處（行號）；分句 → 清單給的讀音
    skipped = {"too long": [], "too short": [], "not Han": [], "in the lexicon": []}
    for n, text, work, _, _, _, reads in checked:
        for c, r in zip(split_clauses(text), reads):
            if not HAN.match(c):
                skipped["not Han"].append((n, c))
            elif len(c) < MIN_LEN:
                skipped["too short"].append((n, c))
            elif len(c) > MAX_LEN:
                skipped["too long"].append((n, c))
            elif c in existing:                                # 契約 §2 去重：`CappedLexicon` 會封頂同名詞條，收進來就會改到既有詞的分數
                skipped["in the lexicon"].append((n, c))
            else:
                src[c].add(f"{work}@{n}")
                if r and override.setdefault(c, r) != r:
                    raise SystemExit(f"{list_path}:{n}: reading of {c!r} differs from an earlier line's ({' '.join(override[c])})")
    cand = sorted(src)
    rd = readings([c for c in cand if c not in override])
    unread = [c for c in cand if c not in override and c not in rd]
    words = [c for c in cand if c in override or c in rd]
    reading = {c: override.get(c) or rd[c][0] for c in words}
    sc = bap.scores(bap.base_lexicon())
    dropped = []
    for _ in range(LEXICON_RULE_ROUNDS):
        order = sorted(words, key=lambda w: (-len(src[w]), w))       # 同音的順序：出處多的在前，同數依字串（可重現）
        rows = bap.pack_rows(order, reading, sc, tag=TAG)
        found = bap.detect_collisions(words, reading, rows, frozenset(), decode, existing=existing, pack_file=PACK_FILE) if words else {}
        hit = sorted({(r, e[0], e[3], e[2]) for r, v in found.items() for e in v if e[1] == "c"})     # (讀音, 分句, 既有詞, 設定)，兩種設定都列
        if not hit:
            break
        dropped += hit
        gone = {w for _, w, _, _ in hit}
        words = [w for w in words if w not in gone]
    else:
        raise SystemExit(f"type (c) collisions not settled after {LEXICON_RULE_ROUNDS} rounds")
    check = [c for c in words if c not in override and rd[c][1]]   # 規則丟掉的分句不列（報告這一節是「已收」的）
    same = sorted((r, e[0], e[4]) for r, v in found.items() for e in v if e[1] == "a")      # 台詞分句之間的同音：開詞包後第一名是另一句（只報告）
    firsts = first_places(words, reading, rows, decode)
    base_first = {prof: sum(1 for (w, _), v in firsts.items() if v[prof][0] == w) for prof in ("chat", "formal")}
    missed = sorted((w, r, prof, off, on) for (w, r), v in firsts.items() for prof, (off, on) in v.items() if on != w)
    text = "".join(rows)
    manifest = {
        "version": hashlib.sha256(text.encode()).hexdigest()[:8],
        "quotes_listed": len(quotes),
        "quotes_checked": len(checked),
        "quotes_unchecked": len(quotes) - len(checked),
        "clauses": len(words),
        "rows": len(rows),
        "skipped": {k: len(v) for k, v in skipped.items()},
        "unreadable": len(unread),
        "dropped_by_lexicon_rule": len({w for _, w, _, _ in dropped}),
        "not_first_with_pack": len({w for w, *_ in missed}),
        "first_without_quote_pack": base_first,
        "list_sha256": sha256_file(list_path),
        # 解碼（衝突偵測、第一名報告）用的模型與詞包檔：之後換了模型或詞包，tools/test_build_quote_pack.py 比對這裡，不符就要重建
        "decode_models_sha256": shipped_lm_sha(),
        "decode_packs_sha256": {f: sha256_file(os.path.join(acg_dir, f)) for f in DECODE_PACKS},
    }
    files = {PACK_FILE: text}
    return files, manifest, report_text(manifest, quotes, checked, skipped, unread, check, dropped, same, missed, len(firsts))


def report_text(manifest, quotes, checked, skipped, unread, check, dropped, same, missed, npairs):
    out = [f"# 經典台詞詞包建置報告（契約 docs/contracts/quote-pack.md）；清單 {manifest['list_sha256'][:8]}，詞包 {manifest['version']}\n",
           f"quotes listed {manifest['quotes_listed']}, checked {manifest['quotes_checked']}, unchecked (not collected) {manifest['quotes_unchecked']}\n",
           f"clauses in the pack {manifest['clauses']}, rows {manifest['rows']}\n"]
    out.append("## 核對來源空白的台詞（不收）：行號、原文、出處\n")
    out += [f"{q[0]}\t{q[1]}\t{q[2]}\n" for q in quotes if q[4] in UNCHECKED]
    out.append("## 不收的分句：原因、行號、分句（2–10 個漢字才收；含英文數字的、11 字以上的列在這裡，不是默默丟掉）\n")
    out += [f"{why}\t{n}\t{c}\n" for why, v in skipped.items() for n, c in v]
    out.append("## 讀音拼不出的分句（不收）\n")
    out += [f"{c}\n" for c in unread]
    out.append("## 讀音工具標 CHECK 的分句（已收，交維護者看；清單第 6 欄可改讀音）\n")
    out += [f"{c}\n" for c in check]
    out.append("## 依規則丟掉的分句（開詞包後把既有詞擠下第一名，既有詞勝）：讀音、分句、既有詞、設定\n")
    out += [f"{r}\t{w}\t{o}\t{prof}\n" for r, w, o, prof in sorted(dropped)]
    out.append("## 台詞分句之間的同音（開詞包後第一名是另一句，只報告）：讀音、分句、第一名\n")
    out += [f"{r}\t{w}\t{n}\n" for r, w, n in same]
    out.append(f"## 開詞包後第一名不是分句自己的（{manifest['not_first_with_pack']} 句；含變調讀音共 {npairs} 組讀音）：分句、讀音、設定、不開台詞詞包的第一名、開了之後的第一名\n")
    out += [f"{w}\t{r}\t{prof}\t{off}\t{on}\n" for w, r, prof, off, on in missed]
    out.append(f"## 不開台詞詞包就已經是第一名的讀音組數（共 {npairs} 組）：聊天 {manifest['first_without_quote_pack']['chat']}，書面 {manifest['first_without_quote_pack']['formal']}\n")
    return "".join(out)


def write_all(files, manifest, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    manifest = dict(manifest, files={n: {"sha256": hashlib.sha256(t.encode()).hexdigest(), "bytes": len(t.encode()), "lines": t.count("\n")} for n, t in sorted(files.items())})
    files = dict(files, **{"quotes.json": json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True) + "\n"})
    for n, t in files.items():
        with open(os.path.join(out_dir, n) + ".tmp", "w", encoding="utf-8", newline="\n") as f:
            f.write(t)
        os.replace(os.path.join(out_dir, n) + ".tmp", os.path.join(out_dir, n))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", default=LIST)
    ap.add_argument("--out", default=PACKS)
    ap.add_argument("--report", default=os.path.join(ROOT, "build", "quote-pack"))
    ap.add_argument("--acg-dir", default=PACKS, help="ACG 詞包所在的資料夾（解碼的「不開」「開」都帶它）")
    a = ap.parse_args()
    check_shipped_lm()
    files, manifest, report = build(a.list, a.acg_dir)
    write_all(files, manifest, a.out)
    os.makedirs(a.report, exist_ok=True)
    with open(os.path.join(a.report, "report.txt"), "w", encoding="utf-8") as f:
        f.write(report)
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    print(f"report: {os.path.join(a.report, 'report.txt')}")


if __name__ == "__main__":
    main()
