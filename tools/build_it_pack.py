"""資訊＋AI 詞包建置工具（契約 docs/contracts/it-pack.md §2）：由國教院計算機學術名詞（15416、15275）、中文維基 CGroup/IT、
CGroup/Electronics、AI 分類標題與手寫清單，產生 data/packs/it-add.tsv（疊加層格式，標籤 it）、it-sources.tsv、it.json，
報告放 build/it-pack/。分數、讀音、排序、同音衝突偵測共用 tools/build_acg_pack.py（import），來源與候選字串的切法共用 tools/it_sources.py。
輸出以 CC BY-SA 4.0 釋出（LICENSES/data.md）。同一份快取與計數檔跑兩次，輸出逐位元組相同。

用法：python3 tools/build_it_pack.py fetch --counts build/it-pack/wiki-counts.tsv   連網補齊建置要的快取（由 main 做；快取已有的請求不重送）
      python3 tools/build_it_pack.py build --offline --counts build/it-pack/wiki-counts.tsv   只用快取建置；缺檔就中止並印出缺的請求
      --cache DIR  --out DIR  --report DIR  --manual FILE  --collisions FILE  --acg-pack FILE
      報告資料夾裡的 words.txt 是 §3.5 的檢查集（複製到 eval/dev/it/words.txt）

fetch 與 build 走同一個 gather()：差別只在 Fetcher 是否允許連網。連網的主機只有 it_sources.HOSTS（契約 §2a）。
(c) 規則、「已經打得對」與衝突偵測都用出貨的 data/lm（雜湊見 SHIPPING_LM，開始前核對）解碼，開動漫詞包，資訊詞包關／開。
"""
import argparse
import collections
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import build_acg_pack as B  # noqa: E402
import it_sources as S  # noqa: E402

PACKS = B.PACKS
TAG = "it"
MIN_COUNT = 50                      # 契約 §2「頻率門檻」：至少 50 次（49 次不收）
BONUS = 3.0                         # 契約 §2「分數」：每列的 log10 分數加 3
# 專有名詞查詢（ai_proper）挑標題用的門檻：快取是門檻 20 時抓的，查詢分批的網址跟著名單變。門檻 ≥ 20 時要過濾的標題都在這份名單裡，
# 結果與用 MIN_COUNT 挑相同；門檻要低於 20 就得重抓。
PROPER_LOOKUP_MIN = 20
SEED, CHECK_N = 20261024, 60        # 契約 §3.5：檢查集的固定種子與抽樣數（20261023 那一份在探索門檻與加分時看過了）
# 契約 §2「大陸寫法」：子字串標記（事先固定，只用在 15416 與維基 AI 標題）。比對前先遮掉台灣寫法的「演算法」。
MARKERS = ("網絡", "信息", "算法", "軟件", "硬件", "數據庫", "默認", "視頻", "鼠標", "內存", "緩存", "服務器", "文件夾", "激活", "兼容")
MASKED = "演算法"
# 契約 §2「專有名詞」：Wikidata P31（含子類 P279*）是人、組織、公司、作品就不收。
PROPER_CLASSES = ("Q5", "Q43229", "Q4830453", "Q17537576")
# 來源種類，也是 it-sources.tsv 的順序與檢查集分組時的優先順序（一個詞有好幾個來源時，歸第一個）。
KINDS = ("manual", "cgroup", "naer75", "naer16", "ai")
TAIWAN_KINDS = ("manual", "cgroup", "naer75", "naer16")      # 台灣來源：出現在其中任何一個，大陸寫法清單就放行
FREQ_KINDS = ("naer16", "ai")                                # 頻率門檻與子字串標記只用在這兩種
# 契約 §2：解碼用的出貨 data/lm（照 data/*.sha256），開始前核對。
SHIPPING_LM = {n: open(os.path.join(ROOT, "data", n + ".sha256")).read().split()[0]
               for n in ("bigram.sjlm", "classes.sjc", "kn.sjkn", "trigram.sjt3")}
SHIP_ARGS = []        # 出貨設定在評測 CLI 上的額外選項（見 decoder()）
# 解碼 CLI 的執行緒數：CLI 保證任何 N 的輸出檔都和 N = 1 逐位元組相同（cli/src/main.rs `--threads`），只影響速度。
# Mac 上預設 1（「Mac 不跑重運算」：make test-python 在 Mac 跑），其他機器（GCP、188）預設用滿核心；SHANJIE_THREADS 可改，至少 1。
THREADS = max(1, int(os.environ.get("SHANJIE_THREADS") or (1 if sys.platform == "darwin" else os.cpu_count() or 1)))
ACG_PACK = os.path.join(PACKS, "acg-add.tsv")


def sha256_text(t):
    return hashlib.sha256(t.encode()).hexdigest()


def check_shipping_lm(lm_dir=os.path.join(ROOT, "data", "lm")):
    for name, sha in SHIPPING_LM.items():
        p = os.path.join(lm_dir, name)
        if not os.path.exists(p) or B.sha256_file(p) != sha:
            raise SystemExit(f"shipping model: {p} is missing or its SHA-256 is not {sha}")


# ---------------------------------------------------------------- 來源（fetch 與 build 共用）

class Tracking(S.Fetcher):
    """記下這一趟用到的請求網址，建置完成後從 manifest 找出它們的大小與雜湊，寫進 it.json。"""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.used = []

    def get(self, url):
        self.used.append(url)
        return super().get(url)


def cn_values(rule):
    """一條規則的 zh-cn 值（含 X=>zh-cn:Y 單向規則）；沒有 zh-cn 才退回 zh-hans。只留 2–10 個漢字。同 build_acg_pack.tw_values 的結構。"""
    out = []
    for prm in B.params(re.sub(r"-\{|\}-", "", rule)):
        cl = [(m.group(2), m.group(3).strip()) for m in (B.LANG.match(c) for c in prm.split(";")) if m]
        use = [c for c in cl if c[0] == "zh-cn"] or [c for c in cl if c[0] == "zh-hans"]
        for _, v in use:
            out += [q.strip() for q in re.sub(r"^《(.*)》$", r"\1", v.strip()).split("/") if B.HAN.match(q.strip())]
    return out


def mainland_forms(s):
    """15275「中國大陸譯名」欄一個值（已轉 zh-hant）→ 2–10 個漢字的寫法：去掉【詞性】與（備註），[可省略的字] 有無各算一個。"""
    s = re.sub(r"【[^】]*】", "", s)
    while S.PAREN.search(s):
        s = S.PAREN.sub("", s)
    forms = {""}
    for part in re.split(r"(\[[^\]]*\])", s):
        forms = {f + x for f in forms for x in ((part[1:-1], "") if part.startswith("[") else (part,))}
    return {x.strip() for x in forms if B.HAN.match(x.strip())}


def read_counts(path):
    """計數檔（it_wiki_counts.py 的輸出）→ (檔頭欄位, {字串: 次數})。"""
    with open(path, encoding="utf-8") as f:
        head = dict(kv.split("=", 1) for kv in f.readline().lstrip("# ").split())
        return head, {w: int(n) for w, n in (l.rstrip("\n").split("\t") for l in f if l.strip())}


def read_manual(path):
    """it-manual.tsv：詞、類別、備註，選填第四欄＝另外的讀音（音節以空白分隔、個數要和字數一致，同 acg-manual.tsv）。回傳 {詞: (類別, 另外的讀音或 None)}。"""
    out = {}
    for w, cat, *rest in B.read_tsv(path):
        if not B.HAN.match(w) or not cat:
            raise SystemExit(f"{path}: bad manual row: {w!r} {cat!r}")
        alt = rest[1].split() if len(rest) > 1 and rest[1].strip() else None
        if alt and len(alt) != len(w):
            raise SystemExit(f"{path}: reading of {w!r} has {len(alt)} syllables")
        out[w] = (cat, alt)
    return out


def ai_proper(f, titles, counts):
    """AI 標題（原標題, zh-tw 標題）裡至少 PROPER_LOOKUP_MIN 次的那些，Wikidata P31（含 P279*）屬於 PROPER_CLASSES 的 zh-tw 標題。
    先用維基 API 把標題換成 Wikidata 項目（pageprops），再每 100 個項目一個 SPARQL 查詢。查詢的名單只取決於計數檔，不取決於詞庫。"""
    pick = sorted({(o, t) for o, t in titles if counts.get(t, 0) >= PROPER_LOOKUP_MIN and S.HAN.fullmatch(t)})
    qid = {}
    for i in range(0, len(pick), 50):
        b = [o for o, _ in pick[i:i + 50]]
        cont = {}
        while True:
            r = f.wiki(action="query", prop="pageprops", ppprop="wikibase_item", titles="|".join(b), **cont)
            for p in r["query"]["pages"]:
                if p.get("pageprops", {}).get("wikibase_item"):
                    qid[p["title"]] = p["pageprops"]["wikibase_item"]
            if "continue" not in r:
                break
            cont = r["continue"]
    items = sorted(set(qid.values()), key=lambda q: int(q[1:]))
    proper = set()
    for i in range(0, len(items), 100):
        vals = " ".join("wd:" + q for q in items[i:i + 100])
        classes = " ".join("wd:" + c for c in PROPER_CLASSES)
        q = f"SELECT DISTINCT ?item WHERE {{ VALUES ?item {{ {vals} }} VALUES ?c {{ {classes} }} ?item wdt:P31/wdt:P279* ?c }}"
        proper |= {b["item"]["value"].rsplit("/", 1)[1] for b in f.sparql(q)["results"]["bindings"]}
    return {t for o, t in pick if qid.get(o) in proper}


def gather(f, counts, manual_path, urls=None):
    """§1 的全部來源 → 純資料（建置函式 build() 只看這份，不碰網路）。f 是 Fetcher；--offline 的 f 缺快取就中止。"""
    r16, r75 = S.csv_rows(f, "15416", urls), S.csv_rows(f, "15275", urls)
    names16 = {c for row in r16 for c in S.split_names(row["中文名稱"])}
    names75 = {c for row in r75 for c in S.split_names(row["中文名稱"])}
    originals = S.category_titles(f)
    titles = list(zip(originals, S.convert_lines(f, originals, "zh-tw")))
    ai = {c for _, t in titles for c in S.ai_title(t)}
    mainland_raw = sorted({v.strip() for row in r75 for v in re.split(r"[；，、]", row.get("中國大陸譯名", "")) if v.strip()})
    mainland = {x for s in S.convert_lines(f, mainland_raw, "zh-hant") for x in mainland_forms(s)}
    cg_tw, cg_hant, cg_cn = collections.defaultdict(set), set(), set()
    for m in S.MODULES:
        r = f.wiki(action="query", prop="revisions", rvprop="content", rvslots="main", titles=m)
        for rule in B.group_rules(r["query"]["pages"][0]["revisions"][0]["slots"]["main"]["content"], True):
            for v, src in B.tw_values(rule):
                cg_tw[v].add(m)
                if src == "zh-hant":
                    cg_hant.add(v)
            cg_cn |= set(cn_values(rule))
    mainland |= {x for s in S.convert_lines(f, sorted(cg_cn), "zh-hant") for x in mainland_forms(s)}
    return {"names16": names16, "names75": names75, "ai": ai, "cg_tw": dict(cg_tw), "cg_hant": cg_hant, "mainland": mainland,
            "manual": read_manual(manual_path), "proper": ai_proper(f, titles, counts)}


def source_meta(f):
    """這一趟用到的請求 → it.json 的來源記錄：CSV 與 CGroup 模組各自一筆（網址、大小、SHA-256、抓取日期），維基與 Wikidata 的其餘請求各一筆彙總。"""
    out, agg = {}, collections.defaultdict(list)
    for url in dict.fromkeys(f.used):
        m = f.manifest[hashlib.sha256(url.encode()).hexdigest()]
        host = urllib.parse.urlsplit(url).hostname
        if host in ("data.gov.tw", "opendata.naer.edu.tw") or "revisions" in url:
            out[urllib.parse.unquote(url)] = {k: m[k] for k in ("size", "sha256", "fetched")}
        else:
            agg[host].append(m)
    for host, ms in sorted(agg.items()):
        out[host + " (api requests)"] = {"requests": len(ms), "bytes": sum(x["size"] for x in ms), "fetched": max(x["fetched"] for x in ms),
                                         "sha256_of_sorted_sha256": sha256_text("".join(sorted(x["sha256"] for x in ms)))}
    return out


# ---------------------------------------------------------------- 規則

def marker_hits(w):
    w = w.replace(MASKED, "\0")
    return [m for m in MARKERS if m in w]


def filter_claims(src, counts):
    """契約 §2 的來源規則，依序套在每個 (詞, 來源種類)：子字串標記、大陸寫法、頻率門檻、專有名詞。
    回傳 (活著的來源 {詞: [種類]}, 各條規則的統計, 各標記擋下的詞數)。一個詞要有一個來源活下來才收；統計分「來源」和「詞」兩種單位。"""
    kinds = {"manual": set(src["manual"]), "cgroup": set(src["cg_tw"]), "naer75": src["names75"], "naer16": src["names16"], "ai": src["ai"]}
    taiwan = set().union(*(kinds[k] for k in TAIWAN_KINDS))
    for k in FREQ_KINDS:
        miss = sorted(w for w in kinds[k] if w not in counts)
        if miss:
            raise SystemExit(f"{len(miss)} candidate strings are not in the counts file (candidates changed since it was made?): {miss[:5]}")
    rules = [("marker", FREQ_KINDS, lambda w: not marker_hits(w)),
             ("mainland", KINDS, lambda w: w not in src["mainland"] or w in taiwan),
             ("frequency", FREQ_KINDS, lambda w: counts[w] >= MIN_COUNT),
             ("proper_noun", ("ai",), lambda w: w not in src["proper"])]
    alive = {k: set(v) for k, v in kinds.items()}
    stats = {"claims_in": {k: len(v) for k, v in kinds.items()}, "words_in": len(set().union(*kinds.values()))}
    for name, applies, keep in rules:
        before = set().union(*alive.values())
        dropped = {k: sorted(w for w in alive[k] if not keep(w)) for k in applies}
        for k, ws in dropped.items():
            alive[k] -= set(ws)
        stats[name] = {"claims_dropped": {k: len(ws) for k, ws in dropped.items()}, "words_dropped": len(before - set().union(*alive.values()))}
    markers = collections.Counter(m for k in FREQ_KINDS for w in kinds[k] for m in marker_hits(w))
    left = collections.defaultdict(list)
    for k in KINDS:
        for w in sorted(alive[k]):
            left[w].append(k)
    return dict(left), stats, {m: markers[m] for m in MARKERS}, kinds


def read_acg_words(path):
    return {l.split("\t")[1] for l in open(path, encoding="utf-8") if l.strip()}


# ---------------------------------------------------------------- 解碼（出貨的 data/lm，開動漫詞包；資訊詞包關／開）

def decoder(acg_tsv=ACG_PACK):
    """`detect_collisions` 要的解碼器 decode(pairs, 設定, packs=None)。detect_collisions 把要測的列寫成 packs/acg-add.tsv（它的檔名），
    所以 packs 給了就把那份當資訊詞包（it-add.tsv）、旁邊放出貨的動漫詞包，`--packs acg,it`；沒給就只開動漫詞包。"""
    def decode(pairs, profile, packs=None):
        with tempfile.TemporaryDirectory() as d:
            shutil.copy(acg_tsv, os.path.join(d, "acg-add.tsv"))
            if packs:
                shutil.copy(os.path.join(packs, "acg-add.tsv"), os.path.join(d, "it-add.tsv"))
            rows, dump = os.path.join(d, "rows.txt"), os.path.join(d, "dump.txt")
            with open(rows, "w", encoding="utf-8") as f:
                f.writelines(f"|{w}|{' '.join(syls)}\n" for w, syls in pairs)
            subprocess.run([B.eval_bin(), "--lm", B.LM, "--profile", profile, "--rows", rows, "--dump", dump, "--packs", "acg,it" if packs else "acg",
                            "--packs-dir", d, "--threads", str(THREADS), *SHIP_ARGS], check=True, stdout=subprocess.DEVNULL)
            first = {}
            for l in open(dump, encoding="utf-8"):
                r, k, s, _ = l.rstrip("\n").split("\t")
                if k == "1":
                    first[int(r)] = s
            assert len(first) == len(pairs), "the evaluation CLI skipped rows"
            return [first[i + 1] for i in range(len(pairs))]
    return decode


# ---------------------------------------------------------------- 主流程

def read_fixed(path):
    """data/packs/it-readings.tsv：詞<TAB>讀音（空白分隔），維護者確認過的讀音（契約 §1 讀音）。"""
    return {r[0]: r[1].split() for r in B.read_tsv(path)} if os.path.exists(path) else {}


def build(src, counts, decode, readings=B.make_readings, have=None, acg_words=frozenset(), collisions_tsv=os.path.join(PACKS, "it-collisions.tsv"), sc=None, info=None,
          readings_tsv=os.path.join(PACKS, "it-readings.tsv")):
    """src：gather() 的結果。decode、readings 可換（測試用假的）。回傳 (檔案, manifest, 未處置的 (a)(b) 衝突, 無法讀音的詞, 參考名單, 被 (c) 丟掉的詞)。"""
    have = B.lexicon_words() if have is None else have
    alive, stats, marker_counts, kinds = filter_claims(src, counts)
    ref = set().union(*(kinds[k] for k in KINDS))      # 參考名單：所有來源在任何過濾之前的 2–10 漢字字串（台灣寫法；大陸寫法清單本身不是名字，不算）
    words = sorted(alive)
    stage = {"after_source_rules": len(words)}
    in_lex = [w for w in words if w in have]
    in_acg = [w for w in words if w not in have and w in acg_words]
    words = [w for w in words if w not in have and w not in acg_words]
    stage.update(dedupe_lexicon=len(in_lex), dedupe_acg=len(in_acg))
    excluded, decided = B.read_collisions(collisions_tsv)
    stage["excluded_by_it_collisions_tsv"] = len([w for w in words if w in excluded])
    words = [w for w in words if w not in excluded]
    rd = readings(words)
    for w, r in read_fixed(readings_tsv).items():      # 維護者確認過的讀音：取代讀音工具的結果（工具讀不出來的詞也收），也不再是 CHECK
        if w in words:
            if len(r) != len(w):
                raise SystemExit(f"{readings_tsv}: {w} has {len(r)} syllables")
            rd[w] = (r, False)
    unread = sorted(set(words) - set(rd))
    words = [w for w in words if w in rd]
    reading = {w: rd[w][0] for w in words}
    extra = {w: [src["manual"][w][1]] for w in words if w in src["manual"] and src["manual"][w][1] and src["manual"][w][1] != reading[w]}
    stage["unreadable"] = len(unread)
    right = {}
    for prof in ("chat", "formal"):
        right[prof] = {w for w, g in zip(words, decode([(w, reading[w]) for w in words], prof)) if g == w}
    already = right["chat"] & right["formal"]
    words = [w for w in words if w not in already]
    stage["already_right"] = len(already)
    sc = sc or B.scores(B.base_lexicon())
    rank = B.collision_rank(B.read_tsv(collisions_tsv) if os.path.exists(collisions_tsv) else [])
    nsrc = lambda w: len(alive[w])
    existing = frozenset(have) | frozenset(acg_words)
    ldrop = []                      # (讀音, 詞, 被擠下的既有詞, 設定)：詞庫的詞或動漫詞包的詞被資訊詞擠下第一名，既有的勝（契約 §2）
    for _ in range(B.LEXICON_RULE_ROUNDS):
        order = B.ordered(words, nsrc, rank, reading, extra)
        rows = B.pack_rows(order, reading, sc, extra, tag=TAG)
        found = B.detect_collisions(words, reading, rows, ref, decode, existing=existing, extra=extra)
        hit = {e[0]: (r, e) for r, v in sorted(found.items()) for e in v if e[1] == "c"}
        if not hit:
            break
        ldrop += [(r, w, o, prof) for w, (r, (_, _, prof, o, _)) in sorted(hit.items())]
        words = [w for w in words if w not in hit]
    else:
        raise SystemExit(f"type (c) collisions not settled after {B.LEXICON_RULE_ROUNDS} rounds")
    inpack = set(words)
    keep_now = B.first_named(rank, inpack)
    col = B.open_collisions(found, decided, keep_now, set(), frozenset(), {(r.split("\t")[0].replace("-", " "), r.split("\t")[1]) for r in rows})
    manifest = {
        "words": len(words), "rows": len(rows),
        "sources": {k: sum(1 for w in words if k in alive[w]) for k in KINDS},
        "rules": stats, "markers": marker_counts, "stages": dict(stage, dropped_by_c_rule=len(ldrop),
                                                                  dropped_c_displacing_acg=sum(1 for _, _, o, _ in ldrop if o in acg_words)),
        "reference_strings": len(ref), "unresolved_collision_readings": len(col),
    }
    srcs = ["word\tsource_kind\tsources\tnote\n"]
    cg_note = lambda w: ["zh-hant"] if w in src["cg_hant"] else []
    for w in order:
        for k in alive[w]:
            note = [f"wiki={counts[w]}"] if k in FREQ_KINDS else []
            if k == "cgroup":
                note += cg_note(w)
            if rd[w][1]:
                note.append("CHECK")
            what = {"manual": src["manual"].get(w, ("",))[0], "cgroup": "; ".join(sorted(src["cg_tw"].get(w, ()))),
                    "naer75": "data.gov.tw/15275", "naer16": "data.gov.tw/15416", "ai": "zh.wikipedia.org AI categories"}[k]
            srcs.append(f"{w}\t{k}\t{what}\t{','.join(note)}\n")
    if info is not None:
        info.update(order=order, reading=reading, extra=extra, check={w: rd[w][1] for w in words}, alive=alive)
    return {"it-add.tsv": "".join(rows), "it-sources.tsv": "".join(srcs)}, manifest, col, unread, ref, ldrop


def check_rows(info, seed=SEED, n=CHECK_N):
    """契約 §3.5 的檢查集：從詞包抽 n 個詞，每個來源依比例（一個詞歸 KINDS 裡第一個有它的來源；最大餘數法分配名額），用固定種子。
    讀音被標 CHECK 的詞不抽（讀音沒人確認過，沒對不能算詞包的錯）。列的格式同其他集合：前文|句子|讀音，沒有前文。"""
    rng = random.Random(seed)
    groups = collections.defaultdict(list)
    for w in sorted(info["order"]):
        if not info["check"][w]:
            groups[next(k for k in KINDS if k in info["alive"][w])].append(w)
    total = sum(len(g) for g in groups.values())
    quota = {k: n * len(groups[k]) // total for k in groups}
    for k in sorted(groups, key=lambda k: (-(n * len(groups[k]) % total), KINDS.index(k)))[:n - sum(quota.values())]:
        quota[k] += 1
    picked = [w for k in KINDS if k in groups for w in rng.sample(groups[k], quota[k])]
    return [f"|{w}|{' '.join(info['reading'][w])}\n" for w in picked], quota


def write_all(files, manifest, out_dir):
    """三個檔寫進 out_dir（先寫 .tmp 再改名）；it.json 記每個檔的 SHA-256。不經過 build_acg_pack.write_all：那個會寫 acg.json。"""
    os.makedirs(out_dir, exist_ok=True)
    manifest = dict(manifest, files={n: {"sha256": sha256_text(t), "bytes": len(t.encode()), "lines": t.count("\n")} for n, t in sorted(files.items())})
    files = dict(files, **{"it.json": json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True) + "\n"})
    for n, t in files.items():
        with open(os.path.join(out_dir, n) + ".tmp", "w", encoding="utf-8", newline="\n") as o:
            o.write(t)
        os.replace(os.path.join(out_dir, n) + ".tmp", os.path.join(out_dir, n))


def report_text(manifest, col, unread, ldrop, acg_words, checks):
    s, st = manifest["sources"], manifest["stages"]
    out = [f"words {manifest['words']} (rows {manifest['rows']}); by source (a word can have several): {s}",
           "rules (claims dropped per source kind, words that lost their last claim):"]
    for name in ("marker", "mainland", "frequency", "proper_noun"):
        out.append(f"  {name}: {manifest['rules'][name]['claims_dropped']} words {manifest['rules'][name]['words_dropped']}")
    out += [f"claims in: {manifest['rules']['claims_in']}, distinct strings {manifest['rules']['words_in']}",
            f"marker hits (15416 and AI titles, before the other rules): {manifest['markers']}",
            f"stages: {st}",
            f"unresolved (a)(b) collision readings: {manifest['unresolved_collision_readings']}",
            f"(c) dropped: {len(ldrop)} ({st['dropped_c_displacing_acg']} displace an anime-pack word)",
            f"CHECK readings: {len(checks)}"]
    return "\n".join(out) + "\n"


def main():
    global MIN_COUNT
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", nargs="?", choices=["fetch", "build"], default="build")
    ap.add_argument("--cache", default=S.CACHE)
    ap.add_argument("--counts", default=os.path.join(ROOT, "build", "it-pack", "wiki-counts.tsv"))
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--out", default=PACKS)
    ap.add_argument("--report", default=os.path.join(ROOT, "build", "it-pack"))
    ap.add_argument("--manual", default=os.path.join(PACKS, "it-manual.tsv"))
    ap.add_argument("--collisions", default=os.path.join(PACKS, "it-collisions.tsv"))
    ap.add_argument("--acg-pack", default=ACG_PACK)
    ap.add_argument("--min-count", type=int, default=MIN_COUNT, help="frequency floor (exploration; the contract value is MIN_COUNT)")
    ap.add_argument("--bonus", type=float, default=BONUS, help="added to every pack row's log10 score (exploration; the contract value is BONUS)")
    a = ap.parse_args()
    if a.min_count < PROPER_LOOKUP_MIN:
        sys.exit(f"--min-count below {PROPER_LOOKUP_MIN} needs a new proper-noun fetch (PROPER_LOOKUP_MIN)")
    if a.cmd == "build" and not a.offline:
        sys.exit("build runs --offline only (contract section 2a); run `fetch` online first")
    head, counts = read_counts(a.counts)
    f = Tracking(a.cache, offline=a.cmd == "build")
    src = gather(f, counts, a.manual)
    cand = sorted(src["names16"] | src["ai"])
    if sha256_text("".join(w + "\n" for w in cand)) != head["candidates_sha256"]:
        sys.exit("the candidate strings built from the sources are not the ones the counts file was made for (candidates_sha256)")
    if a.cmd == "fetch":
        print(f"fetched; cache {a.cache}: {len(f.manifest)} requests; proper-noun AI titles {len(src['proper'])}; mainland strings {len(src['mainland'])}")
        return
    check_shipping_lm()
    acg_words = read_acg_words(a.acg_pack)
    info = {}
    MIN_COUNT = a.min_count
    sc = {k: round(v + a.bonus, 8) for k, v in B.scores(B.base_lexicon()).items()}   # 8 decimals like every other overlay file
    files, manifest, col, unread, ref, ldrop = build(src, counts, decoder(a.acg_pack), acg_words=acg_words, collisions_tsv=a.collisions, sc=sc, info=info)
    manifest.update(min_count=a.min_count, bonus=a.bonus)
    meta = source_meta(f)
    manifest.update(
        version=max(m["fetched"] for m in meta.values()).replace("-", "") + "-" + sha256_text(files["it-add.tsv"])[:8],
        counts_file={"sha256": B.sha256_file(a.counts), **head}, source_files=meta,
        manual_file={"sha256": B.sha256_file(a.manual), "words": len(src["manual"])},
        dropped_unreadable=len(unread))
    write_all(files, manifest, a.out)
    os.makedirs(a.report, exist_ok=True)
    checks = sorted((w, " ".join(info["reading"][w])) for w, c in info["check"].items() if c)
    rows, quota = check_rows(info)
    for name, text in (("collisions.txt", B.collisions_text(col, {"unclassified_groups": []}, ldrop)), ("unreadable.txt", "".join(w + "\n" for w in unread)),
                       ("reference.txt", "".join(w + "\n" for w in sorted(ref))), ("check-readings.txt", "".join(f"{w}\t{r}\n" for w, r in checks)),
                       ("words.txt", "".join(rows)), ("report.txt", report_text(manifest, col, unread, ldrop, acg_words, checks))):
        with open(os.path.join(a.report, name), "w", encoding="utf-8") as o:
            o.write(text)
    print(report_text(manifest, col, unread, ldrop, acg_words, checks), end="")
    print("check set quota by source:", quota)


if __name__ == "__main__":
    main()
