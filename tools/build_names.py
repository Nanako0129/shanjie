"""常見專有名詞（公司、品牌、學校）建置工具（契約 docs/contracts/names-lexicon.md §2）：由 name-suggestion-index（NSI）、
Wikidata、中文維基與教育部學校名錄，產生一般詞庫層 data/lexicon/names-add.tsv（疊加層格式）、names-sources.tsv、names.json，
並把沒有處置的同音衝突列進 build/names/collisions.txt。輸出以 CC BY-SA 4.0 釋出（LICENSES/data.md）。
同一份快取跑兩次，輸出逐位元相同。這一片不載入名單；評測用 CLI 的 `--extra-overlay`。

用法：python3 tools/build_names.py --nsi-commit SHA     抓取（有快取就用快取）並寫入 data/lexicon/
      python3 tools/build_names.py --nsi-commit SHA --offline   只用快取，缺了就中止
      --cache DIR  --out DIR  --report DIR   快取（預設 ~/.cache/shanjie/sources/names）、輸出、報告（預設 build/names）
      python3 tools/build_names.py --check    只檢查已提交的 names-add.tsv 和詞庫其他四份檔沒有交集（make test 也跑）

網路只連 zh.wikipedia.org／www.wikidata.org 的 /w/api.php、raw.githubusercontent.com（只限 NSI 那個 commit）、
data.gov.tw 與 stats.moe.gov.tw 的名錄 CSV；限速、User-Agent、maxlag 沿用 build_acg_pack.Api。請求 3,000 個、快取 500 MB 為上限。
讀音靠 tools/readings.py（再套 names-readings.tsv），同音衝突靠評測 CLI（target/release/shanjie-eval）與 data/lm/bigram.sjlm。
"""
import argparse
import collections
import csv
import hashlib
import html
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import build_acg_pack as bap  # noqa: E402  Api（限速、快取）、分數、讀音、同音衝突
import ime  # noqa: E402

bo, bc = bap.bo, bap.bo.bc
LEX = os.path.join(ROOT, "data", "lexicon")
ACG_ADD = os.path.join(bap.PACKS, "acg-add.tsv")
CACHE = os.path.expanduser("~/.cache/shanjie/sources/names")
MAX_REQUESTS, MAX_CACHE = 3000, 500_000_000   # 契約 §2：一次建置的網路請求數、快取位元組數
TAG = "names"
NSI_COMMIT = "ac22bfd445b729b567070c1b0900be6d0c6ef924"   # 釘住的 NSI commit（和 data/lexicon/names.json 同一個）；--nsi-commit 可覆寫
NSI_CATEGORIES = [("amenity", v) for v in "cafe restaurant fast_food bank fuel pharmacy".split()] + \
    [("shop", v) for v in "convenience supermarket department_store mall electronics mobile_phone clothes cosmetics bakery tea beverages".split()]
NAME_KEYS = ["name:zh-Hant", "name:zh-TW", "name:zh", "brand:zh-Hant", "brand:zh", "name", "brand"]   # 契約 §1：取名字的順序
WIKI_CATEGORIES = ["Category:台灣手搖茶飲品牌", "Category:台灣餐飲公司", "Category:台灣公司", "Category:台灣電子商務網站", "Category:臺灣的大學"]
MOE_FILES = {"u1_new.csv": "https://stats.moe.gov.tw/files/opendata/u1_new.csv", "high.csv": "https://stats.moe.gov.tw/files/school/115/high.csv"}
COMPANY_SUFFIXES = ("股份有限公司", "有限公司", "公司", "集團")      # 長的先試；只用在分類來源
POLY = set("樂行長重藏都會種朝")                                      # 契約 §2：多音字，讀音由 main 人工核對
HANX = bap.HANX
MANUAL = os.path.join(LEX, "names-manual.tsv")
READINGS_TSV = os.path.join(LEX, "names-readings.tsv")
COLLISIONS_TSV = os.path.join(LEX, "names-collisions.tsv")


# ---------------------------------------------------------------- 抓取：允許清單、上限、快取

def allowed(url, commit):
    """契約 §2 的允許清單；其他網址一律拒絕。"""
    u = urllib.parse.urlsplit(url)
    if u.scheme != "https" or u.port is not None or u.username:
        return False
    if u.netloc in ("zh.wikipedia.org", "www.wikidata.org"):
        return u.path == "/w/api.php"
    if u.netloc == "raw.githubusercontent.com":
        return bool(re.fullmatch(r"[0-9a-f]{40}", commit or "")) and u.path.startswith(f"/osmlab/name-suggestion-index/{commit}/data/brands/")
    if u.netloc in ("data.gov.tw", "stats.moe.gov.tw"):
        return u.path.endswith(".csv")
    return False


class _Redirect(urllib.request.HTTPRedirectHandler):
    """轉址也要在允許清單裡。"""

    def __init__(self, commit):
        self.commit = commit

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not allowed(newurl, self.commit):
            raise SystemExit(f"refused: redirect outside the allow-list: {newurl[:120]}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class NamesApi(bap.Api):
    """build_acg_pack.Api（快取、每秒 ≤ 1 個請求、User-Agent、maxlag=5）加上允許清單與契約 §2 的上限。"""

    def __init__(self, cache=CACHE, offline=False, commit=""):
        super().__init__(cache, offline)
        self.commit = commit

    def _check(self, url, cached):
        if not allowed(url, self.commit):
            raise SystemExit(f"refused: not on the allow-list: {url[:120]}")
        if not cached and not self.offline and (self.requests >= MAX_REQUESTS or self.size > MAX_CACHE):
            raise SystemExit(f"stop (contract §2): {self.requests} requests this run, cache {self.size} bytes")

    def _get(self, key, url):
        self._check(url, os.path.exists(os.path.join(self.cache, key + ".json")))
        return super()._get(key, url)         # ponytail: 父類的重試（maxlag）可能讓請求數超過上限最多 5 個

    def wikidata(self, **p):
        p.update(format="json", formatversion="2", maxlag="5")
        return self._get(self.key(dict(p, _host="wikidata")), "https://www.wikidata.org/w/api.php?" + urllib.parse.urlencode(sorted(p.items())))

    def json_url(self, url):
        return self._get("url-" + hashlib.sha1(url.encode()).hexdigest(), url)

    def text(self, url):
        """名錄 CSV 的原始位元組（快取；--offline 缺檔就中止）。"""
        path = os.path.join(self.cache, "csv-" + hashlib.sha1(url.encode()).hexdigest() + ".csv")
        self._check(url, os.path.exists(path))
        if not os.path.exists(path):
            if self.offline:
                raise SystemExit(f"--offline: not cached: {url[:120]}")
            time.sleep(max(0.0, 1.1 - (time.time() - self.last)))
            self.requests += 1
            self.last = time.time()
            data = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": bap.UA}), timeout=120).read()
            with open(path + ".part", "wb") as f:
                f.write(data)
            os.replace(path + ".part", path)
            self.size += len(data)
        with open(path, "rb") as f:
            return f.read()


# ---------------------------------------------------------------- 來源規則

def is_taiwan(location_set):
    """契約 §1：include 明列 tw（001 全球不算），exclude 含 tw 的不收。"""
    return "tw" in location_set.get("include", []) and "tw" not in location_set.get("exclude", [])


def _conv():
    if not bc.MARKERS:
        bc.load_conv()      # 填 MARKERS、TRAD_ONLY、SIMP_ONLY、TRAD_SIMP_ONLY（讀 ~/.cache/shanjie/sources/opencc，核對 SHA-256）


def simplified(name):
    """契約 §1：含簡體專用字。`is_simplified` 之外再要求至少一個字在 `TRAD_SIMP_ONLY`（簡體專用、台灣不用的字）：
    is_simplified 對短字串過敏（台糖、台塑、台北、台南、后里：「台」「后」是標記字、又沒有繁體專用字可抵銷），會丟掉正常的台灣名字（2026-10-10 實測）。"""
    _conv()
    return bc.is_simplified(name) and any(c in bc.TRAD_SIMP_ONLY for c in name)


def nsi_name(tags):
    """契約 §1 的順序，取第一個全部是漢字、沒有簡體專用字的值；都沒有就是 None。"""
    for k in NAME_KEYS:
        v = tags.get(k, "").strip()
        if v and HANX.fullmatch(v) and not simplified(v):
            return v
    return None


def nsi_brands(items):
    """一個 NSI 檔的 items → [(名字或 None, QID 或 None, NSI id)]，只含台灣品牌。"""
    out = []
    for it in items:
        if is_taiwan(it.get("locationSet", {})):
            q = it["tags"].get("brand:wikidata", "")
            out.append((nsi_name(it["tags"]), q if re.fullmatch(r"Q[0-9]+", q) else None, it["id"]))
    return out


def moe_names(raw):
    """名錄 CSV 的位元組 → (最大學年度, [學校名稱])：只取「學年度」是數字、而且等於最大學年度的列（高中名錄有一列是「高中部尚未招生」）。"""
    rd = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
    if not {"學年度", "學校名稱"} <= set(rd.fieldnames or ()):
        raise SystemExit(f"moe csv: unexpected header {rd.fieldnames}")
    rows = [(int(r["學年度"]), r["學校名稱"].strip()) for r in rd if re.fullmatch(r"[0-9]+", (r["學年度"] or "").strip())]
    if not rows:
        raise SystemExit("moe csv: no row with a numeric 學年度")
    year = max(y for y, _ in rows)
    return year, [n for y, n in rows if y == year]


LIST_SUFFIX = "列表"   # 分類成員裡的列表條目（臺灣鐵道公司組織列表、台灣機車公司列表），2026-10-11 抽樣後加的過濾


def strip_company(name):
    """去掉公司後綴（只用在分類來源）；剩下不到兩個字就不去。"""
    for s in COMPANY_SUFFIXES:
        if name.endswith(s) and len(name) - len(s) >= 2:
            return name[:-len(s)]
    return name


def is_subsequence(short, full):
    it = iter(full)
    return all(c in it for c in short)


def is_abbreviation(redirect, school):
    """簡稱：重定向標題比全名短，而且是全名的子序列（台、臺視為同字）。"""
    norm = lambda s: s.replace("台", "臺")
    return len(redirect) < len(school) and HANX.fullmatch(redirect) is not None and is_subsequence(norm(redirect), norm(school))


# ---------------------------------------------------------------- 維基查詢

def tw_title(api, title, memo, follow=True):
    """條目在 variant=zh-tw 下的顯示標題（去消歧義括號、標籤）→ (顯示標題, 實際標題, revid)；條目不存在是 None。"""
    k = (title, follow)
    if k not in memo:
        p = dict(action="parse", page=title, variant="zh-tw", prop="displaytitle|revid")
        try:
            d = api.wiki(**(dict(p, redirects="1") if follow else p))["parse"]
        except RuntimeError as e:
            if not (isinstance(e.args[0], dict) and e.args[0].get("code") == "missingtitle"):
                raise      # Api._get 的 "maxlag did not clear" 是字串，原樣再丟
            memo[k] = None
            return None
        disp = html.unescape(re.sub(r"<[^>]+>", "", d["displaytitle"])).strip()
        memo[k] = (re.sub(r"\s*[\(（][^)）]*[\)）]\s*$", "", disp), d["title"], d["revid"])
    return memo[k]


def members(api, cat, types):
    """分類的成員標題（照 continue 追到底）。"""
    out, cont = [], {}
    while True:
        r = api.wiki(action="query", list="categorymembers", cmtitle=cat, cmtype=types, cmlimit="500", **cont)
        out += [m["title"] for m in r["query"]["categorymembers"]]
        cont = r.get("continue")
        if not cont:
            return out


def han_title(title):
    """抓 zh-tw 標題之前先濾掉不可能收的：去括號後含非漢字的標題，轉換不會把它變成全漢字。"""
    return HANX.fullmatch(re.sub(r"\s*[\(（][^)）]*[\)）]\s*$", "", title)) is not None


def redirects_of(api, targets):
    """條目標題 → 重定向到它的標題清單（每批 50 個，照 continue 追到底）。"""
    out = collections.defaultdict(list)
    for i in range(0, len(targets), 50):
        cont = {}
        while True:
            r = api.wiki(action="query", prop="redirects", titles="|".join(targets[i:i + 50]), rdnamespace="0", rdlimit="max", **cont)
            for p in r["query"]["pages"]:
                out[p["title"]] += [x["title"] for x in p.get("redirects", [])]
            cont = r.get("continue")
            if not cont:
                break
    return out


# ---------------------------------------------------------------- 去重、讀音、衝突

def file_words(path):
    """疊加層格式檔（讀音、詞、分數、來源）的詞；沒有檔就是空。"""
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8") as f:
        return {l.split("\t")[1] for l in f}


def known_words(lex_dir=LEX, acg_add=ACG_ADD):
    """基底詞庫、overlay-add.tsv、sandhi-add.tsv、acg-add.tsv 的詞（任何讀音下）。"""
    base = bap.base_lexicon() if lex_dir == LEX else ime.Lexicon(os.path.join(lex_dir, "mcbpmf-data.txt"))
    return set(base.by_word) | file_words(os.path.join(lex_dir, "overlay-add.tsv")) | file_words(os.path.join(lex_dir, "sandhi-add.tsv")) | file_words(acg_add)


def check_disjoint(words, have, what="names-add.tsv"):
    """契約 §2：輸出和那四份檔的詞集合交集必須為空（核心依字串封頂所有讀音，同一個（讀音, 詞）重複還會讓載入失敗）。"""
    bad = sorted(set(words) & set(have))
    if bad:
        raise SystemExit(f"{what} shares {len(bad)} words with the base, overlay-add.tsv, sandhi-add.tsv or acg-add.tsv: {bad[:5]}")


def check_committed(lex_dir=LEX, acg_add=ACG_ADD):
    """已提交的 names-add.tsv 對那四份檔（沒有檔就是空，照樣通過）；make test 與 --check 用。"""
    check_disjoint(file_words(os.path.join(lex_dir, "names-add.tsv")), known_words(lex_dir, acg_add))


def read_name_readings(path):
    """names-readings.tsv：名字、讀音（音節以空白分隔）；音節數要等於字數，不管這個名字有沒有進名單。"""
    out = {}
    for r in bap.read_tsv(path) if os.path.exists(path) else []:
        if len(r) != 2 or len(r[1].split()) != len(r[0]):
            raise SystemExit(f"{path}: reading of {r[0]!r} must have one syllable per character: {r}")
        out[r[0]] = r[1].split()
    return out


def decode(pairs, profile, packs_dir=None, acg=False):
    """評測 CLI 解碼每個 (詞, 讀音音節串)，回傳第一名。packs_dir 給了就加 `--extra-overlay`（detect_collisions 把名單寫成那個目錄的
    acg-add.tsv，檔名是 build_acg_pack 寫死的）。acg=True 另開 ACG 詞包（`--packs acg`，用這份 checkout 的 data/packs/acg-add.tsv）。"""
    with tempfile.TemporaryDirectory() as d:
        rows, dump = os.path.join(d, "rows.txt"), os.path.join(d, "dump.txt")
        with open(rows, "w", encoding="utf-8") as f:
            f.writelines(f"|{w}|{' '.join(syls)}\n" for w, syls in pairs)
        cmd = [bap.eval_bin(), "--lm", bap.LM, "--profile", profile, "--rows", rows, "--dump", dump]
        if acg:
            cmd += ["--packs", "acg"]
        if packs_dir:
            cmd += ["--extra-overlay", os.path.join(packs_dir, "acg-add.tsv")]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
        first = {}
        with open(dump, encoding="utf-8") as f:
            for l in f:
                r, k, s, _ = l.rstrip("\n").split("\t")
                if k == "1":
                    first[int(r)] = s
        assert len(first) == len(pairs), "the evaluation CLI skipped rows"
        return [first[i + 1] for i in range(len(pairs))]


def decode_acg(pairs, profile, packs_dir=None):
    """ACG 詞包開著的 `decode`（給 detect_collisions 查名字與詞包詞的同音衝突）。"""
    return decode(pairs, profile, packs_dir, acg=True)


def pack_scores(path):
    """acg-add.tsv → {讀音鍵: {詞: 分數}}。"""
    out = collections.defaultdict(dict)
    for r in bap.read_tsv(path) if os.path.exists(path) else []:
        out[r[0]][r[1]] = float(r[2])
    return out


def below_pack(rows, rank, inpack, pack):
    """契約 §2 的排序規則（只這一條）：處置列把詞包詞排在最前面時，同讀音、同一列點名的名字詞，分數至多是詞包詞的分數減 TIE
    （分數相同時解碼器的先後不可靠）。其他列原樣。"""
    first = {}
    for (r, w), _ in sorted(rank.items(), key=lambda kv: kv[1]):
        first.setdefault(r, w)
    out = []
    for row in rows:
        key, word, score, tag = row.rstrip("\n").split("\t")
        r = key.replace("-", " ")
        top = first.get(r)
        if top is not None and top not in inpack and top in pack.get(key, {}) and word != top and word in inpack and (r, word) in rank:
            limit = round(pack[key][top] - bap.TIE, 8)
            if float(score) > limit:
                row = f"{key}\t{word}\t{limit!r}\t{tag}\n"
        out.append(row)
    return out


# ---------------------------------------------------------------- 主流程

def build(api, nsi_commit, manual_tsv=MANUAL, readings_tsv=READINGS_TSV, collisions_tsv=COLLISIONS_TSV, have=None, decode=decode, decode_acg=decode_acg, acg_add=ACG_ADD, readings=bap.make_readings,
          base=None, wiki_categories=WIKI_CATEGORIES, nsi_categories=NSI_CATEGORIES, moe_files=MOE_FILES):
    log = lambda *a: print(*a, file=sys.stderr)
    have = known_words() if have is None else have
    base = base or bap.base_lexicon()
    dropped = collections.Counter()
    cands = collections.defaultdict(lambda: collections.defaultdict(set))    # 名字 → 種類 → 出處
    qids, memo = collections.defaultdict(set), {}
    revs = set()

    def admit(name, kind, source, qid=None, company=False):
        if company and name.endswith(LIST_SUFFIX):    # 契約 §3 第 2 項的抽樣（2026-10-11）：分類裡的「…列表」條目不是名字
            dropped["list_article"] += 1
            return
        name = strip_company(name) if company else name
        if not bap.HAN.fullmatch(name):
            dropped["not_han_or_length"] += 1
        elif simplified(name):
            dropped["simplified"] += 1
        else:
            cands[name][kind].add(source)
            if qid:
                qids[name].add(qid)

    # NSI：台灣品牌的名字；有 QID 的再問 Wikidata 有沒有中文維基條目，收它的 zh-tw 顯示標題
    nsi_counts, by_qid = {}, set()
    for key, value in nsi_categories:
        items = api.json_url(f"https://raw.githubusercontent.com/osmlab/name-suggestion-index/{nsi_commit}/data/brands/{key}/{value}.json")["items"]
        brands = nsi_brands(items)
        nsi_counts[f"{key}/{value}"] = len(brands)
        for name, qid, nid in brands:
            if name is None:
                dropped["nsi_no_usable_name"] += 1
            else:
                admit(name, "nsi", f"nsi:{key}/{value}/{nid}@{nsi_commit[:12]}", qid)
            if qid:
                by_qid.add(qid)
    ids = sorted(by_qid)
    for i in range(0, len(ids), 50):
        r = api.wikidata(action="wbgetentities", ids="|".join(ids[i:i + 50]), props="info|sitelinks", sitefilter="zhwiki")
        for q, e in sorted(r["entities"].items()):
            link = e.get("sitelinks", {}).get("zhwiki")
            if "missing" in e or not link:
                continue
            got = tw_title(api, link["title"], memo)
            if got:
                revs.add(got[2])
                admit(got[0], "nsi-wikipedia", f"zhwiki:{got[1]}@{got[2]}+wikidata:{q}@{e.get('lastrevid')}", q)

    # 中文維基分類：條目與一層子分類的條目；標題一律取 zh-tw 顯示標題；公司後綴只在這裡去
    categories = {}
    for cat in wiki_categories:
        q = api.wiki(action="query", titles=cat, prop="info")["query"]["pages"][0]
        if q.get("missing"):
            categories[cat] = {"exists": False}
            continue
        pages = members(api, cat, "page")
        subs = members(api, cat, "subcat")
        for s in subs:
            pages += members(api, s, "page")
        categories[cat] = {"exists": True, "pages": len(pages), "subcategories": len(subs)}
        for t in sorted(set(pages)):
            if not han_title(t):
                dropped["title_not_han"] += 1
                continue
            got = tw_title(api, t, memo)
            if got:
                revs.add(got[2])
                admit(got[0], "category", f"{cat}:{got[1]}@{got[2]}", company=True)

    # 學校名錄（最大學年度）與簡稱（中文維基有重定向到該校條目的）
    moe, schools = {}, {}
    for fname, url in sorted(moe_files.items()):
        raw = api.text(url)
        year, names = moe_names(raw)
        moe[fname] = {"year": year, "sha256": hashlib.sha256(raw).hexdigest(), "rows": len(names)}
        for n in names:
            admit(n, "school", f"moe:{fname}@{year}")
            if bap.HAN.fullmatch(n):
                schools[n] = f"moe:{fname}@{year}"
    arts = bap.resolve_titles(api, sorted(schools))
    target_schools = collections.defaultdict(list)
    for s, t in arts.items():
        if t:
            target_schools[t].append(s)
    for t, rds in sorted(redirects_of(api, sorted(target_schools)).items()):
        for r in sorted(set(rds)):
            if any(is_abbreviation(r, s) for s in target_schools[t]):
                got = tw_title(api, r, memo, follow=False)
                if got:
                    revs.add(got[2])
                    admit(got[0], "school-abbr", f"zhwiki:{got[1]}->{t}@{got[2]}")

    # 手動補
    manual_reading = {}
    for w, note, *rest in bap.read_tsv(manual_tsv) if os.path.exists(manual_tsv) else []:
        if not bap.HAN.fullmatch(w) or (rest and rest[0].strip() and len(rest[0].split()) != len(w)):
            raise SystemExit(f"{manual_tsv}: bad manual row: {w!r}")
        cands[w]["manual"].add(note)
        if rest and rest[0].strip():
            manual_reading[w] = rest[0].split()

    # 去重：字串在那四份檔的任何讀音下就不收
    in_lexicon = set(cands) & have
    dropped["in_lexicon"] = len(in_lexicon)
    excluded, decided = bap.read_collisions(collisions_tsv)
    rank = bap.collision_rank(bap.read_tsv(collisions_tsv) if os.path.exists(collisions_tsv) else [])
    cand = [w for w in sorted(cands) if w not in have and w not in excluded]
    dropped["excluded_by_names_collisions_tsv"] = len((set(cands) - set(have)) & set(excluded))

    # 讀音：readings.py（手動列的讀音優先），再套 names-readings.tsv 的整名讀音
    rd = dict(readings([w for w in cand if w not in manual_reading]))
    rd.update({w: (s, False) for w, s in manual_reading.items() if w in cand})
    fixed = read_name_readings(readings_tsv)
    rd.update({w: (s, False) for w, s in fixed.items() if w in cand})
    unread = sorted(set(cand) - set(rd))
    words = [w for w in cand if w in rd]
    reading = {w: rd[w][0] for w in words}
    sc = bap.scores(base)
    pack = pack_scores(acg_add)
    nsrc = lambda w: sum(len(s) for s in cands[w].values())

    # 同音衝突：(c) 名字把既有詞擠下第一名就丟（既有詞勝）；(a) 名單內同音要有處置
    lexicon_dropped = []
    for _ in range(bap.LEXICON_RULE_ROUNDS):
        order = bap.ordered(words, nsrc, rank, reading)
        rows = below_pack([r.rsplit("\t", 1)[0] + f"\t{TAG}\n" for r in bap.pack_rows(order, reading, sc)], rank, set(words), pack)
        found = bap.detect_collisions(words, reading, rows, frozenset(), decode, existing=have)
        hit = {e[0]: (r, e) for r, v in sorted(found.items()) for e in v if e[1] == "c"}
        if not hit:
            break
        lexicon_dropped += [(r, w, o, prof) for w, (r, (_, _, prof, o, _)) in sorted(hit.items())]
        words = [w for w in words if w not in hit]
    else:
        raise SystemExit(f"type (c) collisions not settled after {bap.LEXICON_RULE_ROUNDS} rounds")
    keep_now = bap.first_named(rank, set(words))
    # 名字與 ACG 詞包詞同音（詞包預設開著）：詞包開著時名字把詞包詞擠下第一名，一律當未處置的 (a)，由維護者決定（排除名字，
    # 或在處置列把詞包詞排前面、名字用 `+`，由 below_pack 排到它後面）。名字只和既有詞庫的詞同音另由上面的 (c) 處理。
    pack_words = set().union(*pack.values()) if pack else set()
    with_pack = bap.detect_collisions(words, reading, rows, frozenset(), decode_acg, existing=pack_words)
    keep_any = {}
    for (r, w), _ in sorted(rank.items(), key=lambda kv: kv[1]):
        keep_any.setdefault(r, w)
    col = {}
    for r, v in found.items():
        left = [e for e in v if (r, e[0]) not in decided or e[4] != keep_now.get(r)]
        if left:
            col[r] = left
    for r, v in with_pack.items():
        left = [(w, "a", prof, o, n) for w, cond, prof, o, n in v if cond == "c" and ((r, w) not in decided or n != keep_any.get(r))]
        if left:
            col.setdefault(r, []).extend(left)
    check_disjoint([r.split("\t")[1] for r in rows], have)
    dropped["type_c_dropped"] = len(lexicon_dropped)
    dropped["unreadable"] = len(unread)

    manifest = {
        "version": f"nsi{nsi_commit[:8]}-moe{'.'.join(str(m['year']) for _, m in sorted(moe.items()))}-" + hashlib.sha256("".join(rows).encode()).hexdigest()[:8],
        "nsi_commit": nsi_commit,
        "words": len(words),
        "rows": len(rows),
        "sources": {k: sum(1 for w in words if k in cands[w]) for k in ("nsi", "nsi-wikipedia", "category", "school", "school-abbr", "manual")},
        "nsi_taiwan_brands": nsi_counts,
        "categories": categories,
        "moe": moe,
        "wiki_revisions": {"pages": len(revs), "min": min(revs, default=0), "max": max(revs, default=0)},
        "dropped": dict(sorted(dropped.items())),
        "unresolved_collision_readings": len(col),
    }
    sources = ["word\tsource_kind\tsources\tqid\tnote\n"]
    check = []
    for w in order:
        note = "POLY" if POLY & set(w) else ""
        if (note or rd[w][1]) and w not in fixed:
            check.append(f"{w}\t{' '.join(reading[w])}\t{'CHECK ' if rd[w][1] else ''}{note}\n")
        for k in sorted(cands[w]):
            sources.append(f"{w}\t{k}\t{'; '.join(sorted(cands[w][k]))}\t{','.join(sorted(qids[w]))}\t{note}\n")
    return {"names-add.tsv": "".join(rows), "names-sources.tsv": "".join(sources)}, manifest, col, unread, lexicon_dropped, check


def collisions_text(col, ldrop=()):
    out = ["# 未處置的同音衝突（契約 §2 類型 a：名單內同音）。每列：讀音、詞、條件、設定、不開名單的第一名、開了之後的第一名。\n",
           "# 處置寫進 data/lexicon/names-collisions.tsv（讀音、保留的詞、排除的詞、理由；排除欄 `+詞` 表示兩個都留）。\n"]
    for r in sorted(col):
        out += [f"{r}\t{w}\t{c}\t{p}\t{o}\t{n}\n" for w, c, p, o, n in sorted(col[r])]
    out.append("# 丟掉的名字（類型 c：會把既有詞庫的詞擠下第一名）。每列：讀音、丟掉的詞、既有詞庫的詞、設定。\n")
    return "".join(out) + "".join(f"{r}\t{w}\t{o}\t{prof}\n" for r, w, o, prof in sorted(ldrop))


def write_all(files, manifest, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    manifest = dict(manifest, files={n: {"sha256": hashlib.sha256(t.encode()).hexdigest(), "bytes": len(t.encode()), "lines": t.count("\n")} for n, t in sorted(files.items())})
    files = dict(files, **{"names.json": json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True) + "\n"})
    for n, t in files.items():
        with open(os.path.join(out_dir, n) + ".tmp", "w", encoding="utf-8", newline="\n") as f:
            f.write(t)
        os.replace(os.path.join(out_dir, n) + ".tmp", os.path.join(out_dir, n))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=CACHE)
    ap.add_argument("--out", default=LEX)
    ap.add_argument("--report", default=os.path.join(ROOT, "build", "names"))
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--nsi-commit", default=NSI_COMMIT)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    if a.check:
        check_committed()
        print("names-add.tsv shares no word with the other four lexicon files")
        return
    if not re.fullmatch(r"[0-9a-f]{40}", a.nsi_commit):
        raise SystemExit("--nsi-commit SHA is required (40 hex digits; e.g. gh api repos/osmlab/name-suggestion-index/commits/main -q .sha)")
    urllib.request.install_opener(urllib.request.build_opener(_Redirect(a.nsi_commit)))
    files, manifest, col, unread, ldrop, check = build(NamesApi(a.cache, a.offline, a.nsi_commit), a.nsi_commit)
    write_all(files, manifest, a.out)
    os.makedirs(a.report, exist_ok=True)
    for name, text in (("collisions.txt", collisions_text(col, ldrop)), ("unreadable.txt", "".join(w + "\n" for w in unread)), ("check-readings.txt", "".join(check))):
        with open(os.path.join(a.report, name), "w", encoding="utf-8") as f:
            f.write(text)
    print(json.dumps({k: v for k, v in manifest.items() if k not in ("categories", "nsi_taiwan_brands")}, ensure_ascii=False, sort_keys=True))
    print(f"unresolved collision readings: {len(col)} (see {os.path.join(a.report, 'collisions.txt')})")


if __name__ == "__main__":
    main()
