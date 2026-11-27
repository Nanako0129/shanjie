"""常見專有名詞（公司、品牌、學校、車站、醫療院所、場館、地名）建置工具（規格 docs/spec/lexicon-and-packs.md §3.2–§3.4, §3.7）：由 name-suggestion-index（NSI）、
Wikidata、中文維基、教育部學校名錄與本地預解之政府開放資料，產生一般詞庫層 data/lexicon/names-add.tsv（疊加層格式）、names-sources.tsv、names.json，
並把沒有處置的同音衝突列進 build/names/collisions.txt。輸出以 CC BY-SA 4.0 釋出（LICENSES/data.md）。
同一份快取跑兩次，輸出逐位元相同。這一片不載入名單；評測用 CLI 的 `--extra-overlay`。

用法：python3 tools/build_names.py --nsi-commit SHA     抓取（有快取就用快取）並寫入 data/lexicon/
      python3 tools/build_names.py --nsi-commit SHA --offline   只用快取，缺了就中止
      --cache DIR  --open-data-dir DIR  --out DIR  --report DIR   快取資料夾（預設 ~/.cache/shanjie，環境變數 SHANJIE_CACHE）下的 sources/names、預解開放資料（預設 names-src）、輸出、報告（預設 build/names）
      python3 tools/build_names.py --check    只檢查已提交的 names-add.tsv 和其餘 8 份詞庫／詞包詞檔沒有交集（make test 也跑）
      python3 tools/build_names.py --append-creators   讀 names-creators.tsv（標籤 creator）與 names-stores.tsv（標籤 store），在 names-add.tsv／names-sources.tsv
                                                       檔尾追加（或換掉）這兩種標籤的列（順序：names → creator → store）、更新 names.json
                                                       （docs/spec/lexicon-and-packs.md §3.5；不連網、不讀快取、不動既有列）
      來源檔欄位：詞、讀音（音節以空白分隔）、備註，可選的第四欄是加分（有限、≥ 0，只能放在同讀音的第一列；docs/spec/lexicon-and-packs.md §3.5）。同讀音的先後就是檔案裡的先後：建置資料的人照 論壇 A 文章數由多到少排好（要人工調整就改檔案順序，例如 峰哥 排在 風哥 前面）。

網路只連 zh.wikipedia.org／www.wikidata.org 的 /w/api.php、raw.githubusercontent.com（只限 NSI 那個 commit）、
data.gov.tw／stats.moe.gov.tw 的名錄 CSV 與 OPEN_DATA_HOSTS；限速、User-Agent、maxlag 沿用 build_acg_pack.Api。請求 3,500 個、快取 500 MB 為上限（不計唯讀開放資料）。
讀音靠 tools/readings.py（再套 names-readings.tsv），同音衝突靠評測 CLI（target/release/shanjie-eval）與 data/lm/bigram.sjlm。
"""
import argparse
import collections
import csv
import hashlib
import html
import io
import json
import math
import os
import re
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import build_acg_pack as bap  # noqa: E402  Api（限速、快取）、分數、讀音、同音衝突
import ime  # noqa: E402

bo, bc = bap.bo, bap.bo.bc
LEX = os.path.join(ROOT, "data", "lexicon")
ACG_ADD = os.path.join(bap.PACKS, "acg-add.tsv")
IT_ADD = os.path.join(bap.PACKS, "it-add.tsv")
MEME_ADD = os.path.join(bap.PACKS, "meme-add.tsv")
BEAUTY_ADD = os.path.join(bap.PACKS, "beauty-add.tsv")
QUOTES_ADD = os.path.join(bap.PACKS, "quotes-add.tsv")
CACHE_ROOT = os.path.expanduser(os.environ.get("SHANJIE_CACHE", "~/.cache/shanjie"))   # SHANJIE_CACHE: the cache folder (default ~/.cache/shanjie)
CACHE = os.path.join(CACHE_ROOT, "sources", "names")
OPEN_DATA_DIR = os.path.join(CACHE_ROOT, "names-src")
MAX_REQUESTS, MAX_CACHE = 3500, 500_000_000   # docs/spec/lexicon-and-packs.md §3.5：一次建置的網路請求數、快取位元組數（不計唯讀開放資料）
NHI_AS_OF = "20261127"   # 固定常數截止日（不讀系統時鐘）：健保署醫院名單依此判斷是否仍有效
# docs/spec/lexicon-and-packs.md §3.3（固定值，不在評測集上選）：名單詞的分數 ＝ scores()[字數] ＋ 這個值（log10），在建置時寫進 names-add.tsv，
# 載入端不另外加；載入端照疊加層規則封頂（語料出現過）或扣 UNSEEN_OVERLAY_PENALTY（沒出現過）。依據：探索（名單層的分數往上加，加 2 時「酷澎」兩種設定第一、守門集合改壞 0）。同音衝突偵測讀的是加分後的列。
NAMES_BONUS = 2
TAG = "names"
NSI_COMMIT = "ac22bfd445b729b567070c1b0900be6d0c6ef924"   # 釘住的 NSI commit（和 data/lexicon/names.json 同一個）；--nsi-commit 可覆寫
NSI_CATEGORIES = (
    [("amenity", v) for v in "cafe restaurant fast_food bank fuel pharmacy ice_cream parcel_locker cinema".split()]
    + [("shop", v) for v in "convenience supermarket department_store mall electronics mobile_phone clothes cosmetics bakery tea beverages optician furniture sports variety_store books pet car motorcycle telecommunication".split()]
    + [("office", v) for v in "insurance estate_agent".split()]
    + [("tourism", "hotel")]
)
NAME_KEYS = ["name:zh-Hant", "name:zh-TW", "name:zh", "brand:zh-Hant", "brand:zh", "name", "brand"]   # docs/spec/lexicon-and-packs.md §3.2：取名字的順序
WIKI_CATEGORIES = [
    ("Category:台灣手搖茶飲品牌", 1),
    ("Category:台灣餐飲公司", 1),
    ("Category:台灣公司", 1),
    ("Category:臺灣電子商務", 0),
    ("Category:臺灣網路書店", 0),
    ("Category:台灣新聞網站", 0),
    ("Category:台灣零售商", 0),
    ("Category:臺灣電信業公司", 0),
    ("Category:台灣航空公司", 0),
    ("Category:台灣房屋仲介業", 0),
    ("Category:臺灣物流公司", 0),
    ("Category:台灣購物中心", 0),
    ("Category:台灣百貨公司", 0),
    ("Category:台灣暢貨中心", 0),
    ("Category:台灣連鎖電影院", 0),
    ("Category:台灣電影院", 0),
    ("Category:台灣夜市", 0),
    ("Category:台灣表演藝術場地", 0),
    ("Category:台灣音樂廳", 0),
    ("Category:台灣文化藝術中心", 0),
    ("Category:中華民國文化部指定文化園區", 0),
    ("Category:台灣當代藝術館", 0),
    ("Category:台灣百岳", 0),
    ("Category:台灣小百岳", 0),
    ("Category:台灣古道", 0),
    ("Category:臺灣國家步道", 0),
    ("Category:臺灣區域步道", 0),
    ("Category:台灣重劃區", 0),
    ("Category:市地重劃區", 0),
    ("Category:臺灣老街", 0),
]
EXCLUDED_SUBCATS = frozenset({"Category:臺灣醫院院長"})
BIO_SUBCAT_RE = re.compile(r"(?:院長|人物|創辦人|董事長|總裁|執行長|館長|校長|校友|員工|企業家|政治人物|藝人|歌手|演員|導演|作家)")
MOE_FILES = {"u1_new.csv": "https://stats.moe.gov.tw/files/opendata/u1_new.csv", "high.csv": "https://stats.moe.gov.tw/files/school/115/high.csv"}
COMPANY_SUFFIXES = ("股份有限公司", "有限公司", "公司", "集團")      # 長的先試；只用在分類來源
POLY = set("樂行長重藏都會種朝")                                      # docs/spec/lexicon-and-packs.md §3.5：多音字，讀音由 main 人工核對
HANX = bap.HANX
MANUAL = os.path.join(LEX, "names-manual.tsv")
READINGS_TSV = os.path.join(LEX, "names-readings.tsv")
COLLISIONS_TSV = os.path.join(LEX, "names-collisions.tsv")
KEPT_C_TSV = os.path.join(LEX, "names-kept-c.tsv")

OPEN_DATA_HOSTS = frozenset({
    "apiservice.mol.gov.tw",
    "cloud.culture.tw",
    "data.cip.gov.tw",
    "data.kcg.gov.tw",
    "data.moa.gov.tw",
    "data.ntpc.gov.tw",
    "data.tainan.gov.tw",
    "data.taipei",
    "file.moc.gov.tw",
    "info.nhi.gov.tw",
    "ins-info.ib.gov.tw",
    "media.taiwan.net.tw",
    "mopsfin.twse.com.tw",
    "newdatacenter.taichung.gov.tw",
    "ods.railway.gov.tw",
    "opdadm.moi.gov.tw",
    "opendata.tycg.gov.tw",
    "stat.fsc.gov.tw",
    "www.banking.gov.tw",
    "www.ida.gov.tw",
    "www.mnd.gov.tw",
    "www.tgos.tw",
    "www.twse.com.tw",
})


# ---------------------------------------------------------------- 抓取：允許清單、上限、快取

def allowed(url, commit):
    """docs/spec/lexicon-and-packs.md §3.5 的允許清單；其他網址一律拒絕。"""
    u = urllib.parse.urlsplit(url)
    if u.scheme != "https" or u.port is not None or u.username or u.password:
        return False
    if u.netloc in ("zh.wikipedia.org", "www.wikidata.org"):
        return u.path == "/w/api.php"
    if u.netloc == "raw.githubusercontent.com":
        return bool(re.fullmatch(r"[0-9a-f]{40}", commit or "")) and u.path.startswith(f"/osmlab/name-suggestion-index/{commit}/data/brands/")
    if u.netloc in ("data.gov.tw", "stats.moe.gov.tw"):
        return u.path.endswith(".csv")
    if u.netloc in OPEN_DATA_HOSTS:
        return True
    return False


class _Redirect(urllib.request.HTTPRedirectHandler):
    """轉址也要在允許清單裡。"""

    def __init__(self, commit):
        self.commit = commit

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not allowed(newurl, self.commit):
            raise SystemExit(f"refused: redirect outside the allow-list: {newurl[:120]}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _retry_after(headers):
    """Parse Retry-After header seconds, fallback 15, clamp to [5, 120]."""
    raw = headers.get("Retry-After") if headers else None
    try:
        secs = int(str(raw).strip())
    except (TypeError, ValueError):
        secs = 15
    return max(5, min(120, secs))


class NamesApi(bap.Api):
    """build_acg_pack.Api（快取、每秒 ≤ 1 個請求、User-Agent、maxlag=5）加上允許清單、429/503 Retry-After 重試與docs/spec/lexicon-and-packs.md §3.7 的上限。"""

    def __init__(self, cache=CACHE, offline=False, commit=""):
        super().__init__(cache, offline)
        self.commit = commit

    def _check(self, url, cached):
        if not allowed(url, self.commit):
            raise SystemExit(f"refused: not on the allow-list: {url[:120]}")
        if not cached and not self.offline and (self.requests >= MAX_REQUESTS or self.size > MAX_CACHE):
            raise SystemExit(f"stop (docs/spec/lexicon-and-packs.md §3.5): {self.requests} requests this run, cache {self.size} bytes")

    def _fetch_bytes(self, url):
        """Fetch raw response bytes with polite rate limit, request cap check, and 429/503 Retry-After handling."""
        for attempt in range(6):
            wait = 1.1 - (time.time() - self.last)
            if wait > 0:
                time.sleep(wait)
            if self.requests >= MAX_REQUESTS or self.size > MAX_CACHE:
                raise SystemExit(f"stop (docs/spec/lexicon-and-packs.md §3.5): {self.requests} requests this run, cache {self.size} bytes")
            self.requests += 1
            self.last = time.time()
            req = urllib.request.Request(url, headers={"User-Agent": bap.UA})
            try:
                return urllib.request.urlopen(req, timeout=120).read()
            except urllib.error.HTTPError as e:
                headers, code = e.headers, e.code
                e.close()
                if code in (429, 503) and attempt < 5:
                    delay = _retry_after(headers) + 1
                    print(f"HTTP {code} for {url[:80]}; sleeping {delay}s (attempt {attempt + 1}/6)", file=sys.stderr)
                    time.sleep(delay)
                    self.last = time.time()
                    continue
                raise
        raise RuntimeError("rate limit did not clear")

    def _get(self, key, url):
        path = os.path.join(self.cache, key + ".json")
        cached = os.path.exists(path)
        self._check(url, cached)
        if cached:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "error" in data:
                raise RuntimeError(data["error"])
            return data
        if self.offline:
            raise SystemExit(f"--offline: not cached: {url[:120]}")
        for attempt in range(6):
            data = json.loads(self._fetch_bytes(url))
            if isinstance(data, dict) and data.get("error", {}).get("code") == "maxlag":
                if attempt < 5:
                    time.sleep(5 * (attempt + 1))
                    self.last = time.time()
                    continue
                raise RuntimeError("maxlag did not clear")
            if isinstance(data, dict) and "error" in data and data["error"].get("code") != "missingtitle":
                raise RuntimeError(data["error"])
            with open(path + ".part", "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(path + ".part", path)
            self.size += os.path.getsize(path)
            if isinstance(data, dict) and "error" in data:
                raise RuntimeError(data["error"])
            return data
        raise RuntimeError("maxlag or rate limit did not clear")

    def wikidata(self, **p):
        p.update(format="json", formatversion="2", maxlag="5")
        return self._get(self.key(dict(p, _host="wikidata")), "https://www.wikidata.org/w/api.php?" + urllib.parse.urlencode(sorted(p.items())))

    def json_url(self, url):
        return self._get("url-" + hashlib.sha1(url.encode()).hexdigest(), url)

    def text(self, url):
        """名錄 CSV 的原始位元組（快取；--offline 缺檔就中止）。"""
        path = os.path.join(self.cache, "csv-" + hashlib.sha1(url.encode()).hexdigest() + ".csv")
        cached = os.path.exists(path)
        self._check(url, cached)
        if not cached:
            if self.offline:
                raise SystemExit(f"--offline: not cached: {url[:120]}")
            data = self._fetch_bytes(url)
            with open(path + ".part", "wb") as f:
                f.write(data)
            os.replace(path + ".part", path)
            self.size += len(data)
        with open(path, "rb") as f:
            return f.read()


# ---------------------------------------------------------------- 來源規則

def is_taiwan(location_set):
    """docs/spec/lexicon-and-packs.md §3.2：include 明列 tw（001 全球不算），exclude 含 tw 的不收。"""
    return "tw" in location_set.get("include", []) and "tw" not in location_set.get("exclude", [])


def _conv():
    if not bc.MARKERS:
        bc.load_conv()      # 填 MARKERS、TRAD_ONLY、TRAD_SIMP_ONLY（讀快取資料夾的 sources/opencc，核對 SHA-256）


def simplified(name):
    """docs/spec/lexicon-and-packs.md §3.2：含簡體專用字。`is_simplified` 之外再要求至少一個字在 `TRAD_SIMP_ONLY`（簡體專用、台灣不用的字）：
    is_simplified 對短字串過敏（台糖、台塑、台北、台南、后里：「台」「后」是標記字、又沒有繁體專用字可抵銷），會丟掉正常的台灣名字（2026-10-10 實測）。"""
    _conv()
    return bc.is_simplified(name) and any(c in bc.TRAD_SIMP_ONLY for c in name)


def nsi_name(tags):
    """docs/spec/lexicon-and-packs.md §3.2 的順序，取第一個全部是漢字、沒有簡體專用字的值；都沒有就是 None。"""
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

def _clean_disp_title(disp):
    s = html.unescape(re.sub(r"<[^>]+>", "", disp)).strip()
    return re.sub(r"\s*[\(（][^)）]*[\)）]\s*$", "", s)


def _parse_cache_params(title, follow):
    p = dict(action="parse", page=title, variant="zh-tw", prop="displaytitle|revid", format="json", formatversion="2", maxlag="5")
    if follow:
        p["redirects"] = "1"
    return p


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
        memo[k] = (_clean_disp_title(d["displaytitle"]), d["title"], d["revid"])
    return memo[k]


def tw_titles_batch(api, titles, memo, follow=True):
    """50 筆一批查 zh-tw 顯示標題（批次鍵只依標題排序決定，不看單頁磁碟快取是否存在，確保即時抓完後重跑 --offline 100% 命中快取；含 NoteTA 時才退回單頁 action=parse）。"""
    todo = [t for t in sorted(set(titles)) if (t, follow) not in memo]
    for i in range(0, len(todo), 50):
        batch = todo[i:i + 50]
        norm, red, by_title, cont = {}, {}, {}, {}
        while True:
            qparams = dict(
                action="query",
                titles="|".join(batch),
                prop="info|revisions|templates" if follow else "info|revisions",
                inprop="displaytitle|varianttitles",
                rvprop="ids",
            )
            if follow:
                qparams["redirects"] = "1"
                qparams["tltemplates"] = "Template:NoteTA"
                qparams["tllimit"] = "500"
            qparams.update(cont)
            r = api.wiki(**qparams)
            q = r["query"]
            norm.update({x["from"]: x["to"] for x in q.get("normalized", [])})
            red.update({x["from"]: x["to"] for x in q.get("redirects", [])})
            for p in q.get("pages", []):
                pt = p["title"]
                if pt not in by_title:
                    by_title[pt] = dict(p)
                else:
                    cur = by_title[pt]
                    if "revisions" in p and "revisions" not in cur:
                        cur["revisions"] = p["revisions"]
                    if "templates" in p:
                        cur["templates"] = cur.get("templates", []) + p["templates"]
                    for k in ("varianttitles", "displaytitle", "missing", "invalid", "pageid"):
                        if k in p and k not in cur:
                            cur[k] = p[k]
            cont = r.get("continue")
            if not cont:
                break
        for orig in batch:
            cur = norm.get(orig, orig)
            seen = set()
            while cur in red and cur not in seen:
                seen.add(cur)
                cur = red[cur]
            p = by_title.get(cur)
            if not p or p.get("missing") or p.get("invalid") or not p.get("revisions"):
                memo[(orig, follow)] = None
            elif (follow and bool(p.get("templates"))) or not p.get("varianttitles", {}).get("zh-tw"):
                tw_title(api, orig, memo, follow=follow)
            else:
                vt = p["varianttitles"]["zh-tw"]
                memo[(orig, follow)] = (_clean_disp_title(vt), p["title"], p["revisions"][0]["revid"])
    return {t: memo[(t, follow)] for t in titles}


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


MEME_ADD = os.path.join(bap.PACKS, "meme-add.tsv")


def known_words(lex_dir=LEX, acg_add=ACG_ADD, meme_add=MEME_ADD, it_add=None, beauty_add=None, quotes_add=None):
    """基底詞庫與全部 8 份疊加／詞包詞檔的詞（任何讀音下；不含 names-add.tsv 本身）。"""
    base = bap.base_lexicon() if lex_dir == LEX else ime.Lexicon(os.path.join(lex_dir, "mcbpmf-data.txt"))
    pack = lambda p, name: (os.path.join(bap.PACKS, name) if lex_dir == LEX else os.path.join(lex_dir, name)) if p is None else p
    out = set(base.by_word) | file_words(os.path.join(lex_dir, "overlay-add.tsv")) | file_words(os.path.join(lex_dir, "sandhi-add.tsv"))
    for p in (acg_add, pack(it_add, "it-add.tsv"), meme_add, pack(beauty_add, "beauty-add.tsv"), pack(quotes_add, "quotes-add.tsv")):
        out |= file_words(p)
    return out


def check_disjoint(words, have, what="names-add.tsv"):
    """docs/spec/lexicon-and-packs.md §3.5：輸出和 known_words 涵蓋的 8 份檔（共 9 份詞檔）詞集合交集必須為空（核心依字串封頂所有讀音，同一個（讀音, 詞）重複還會讓載入失敗）。"""
    bad = sorted(set(words) & set(have))
    if bad:
        raise SystemExit(f"{what} shares {len(bad)} words with the base, overlay-add.tsv, sandhi-add.tsv, or pack files: {bad[:5]}")


def check_committed(lex_dir=LEX, acg_add=ACG_ADD, meme_add=MEME_ADD, it_add=None, beauty_add=None, quotes_add=None):
    """已提交的 names-add.tsv 對其餘 8 份檔（沒有檔就是空，照樣通過）；make test 與 --check 用。"""
    check_disjoint(file_words(os.path.join(lex_dir, "names-add.tsv")), known_words(lex_dir, acg_add, meme_add, it_add, beauty_add, quotes_add))


def read_name_readings(path):
    """names-readings.tsv：名字、讀音（音節以空白分隔）；音節數要等於字數，不管這個名字有沒有進名單。"""
    out = {}
    for r in bap.read_tsv(path) if os.path.exists(path) else []:
        if len(r) != 2 or len(r[1].split()) != len(r[0]):
            raise SystemExit(f"{path}: reading of {r[0]!r} must have one syllable per character: {r}")
        out[r[0]] = r[1].split()
    return out


def read_kept_c(path):
    """names-kept-c.tsv：三欄（空白分隔讀音、詞、被擠下第一名的既有詞）；# 與空行略過，任一欄不合規或重複 (讀音, 詞) 就中止。"""
    if not path or not os.path.exists(path):
        return set()
    out = set()
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.rstrip("\r\n")
            if not line.strip() or line.startswith("#"):
                continue
            fields = line.split("\t")
            if len(fields) != 3:
                raise SystemExit(f"{path}:{n}: expected 3 tab-separated columns (reading, word, displaced): {line!r}")
            reading, word, displaced = fields
            syls = reading.split(" ")
            if "-" in reading or not bap.HAN.fullmatch(word) or len(syls) != len(word) or not all(syls) or not displaced.strip():
                raise SystemExit(f"{path}:{n}: bad kept-c row: {line!r}")
            key = (reading, word)
            if key in out:
                raise SystemExit(f"{path}:{n}: duplicate kept-c row {key!r}")
            out.add(key)
    return out


def decode(pairs, profile, packs_dir=None, acg=False):
    """評測 CLI 解碼每個 (詞, 讀音音節串)，回傳第一名。packs_dir 給了就加 `--extra-overlay`（detect_collisions 把名單寫成那個目錄的
    acg-add.tsv，檔名是 build_acg_pack 寫死的）。acg=True 另開 ACG 詞包（`--packs acg`，用這份 checkout 的 data/packs/acg-add.tsv）。
    一律加 `--no-names`：名單層 model-v6 起常開（data/lexicon/names-add.tsv），不關的話「沒有名單」的解碼其實有已提交的名單（低報），
    候選名單用 --extra-overlay 再給一次也會重複載入而失敗（docs/spec/models.md §3.5, §8.1）。"""
    with tempfile.TemporaryDirectory() as d:
        rows, dump = os.path.join(d, "rows.txt"), os.path.join(d, "dump.txt")
        with open(rows, "w", encoding="utf-8") as f:
            f.writelines(f"|{w}|{' '.join(syls)}\n" for w, syls in pairs)
        cmd = [bap.eval_bin(), "--lm", bap.LM, "--profile", profile, "--no-names", "--rows", rows, "--dump", dump]
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
    """docs/spec/lexicon-and-packs.md §3.5 的排序規則（只這一條）：處置列把詞包詞排在最前面時，同讀音、同一列點名的名字詞，分數至多是詞包詞的分數減 TIE
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


# ---------------------------------------------------------------- 創作者與店家名字：只在檔尾追加（docs/spec/lexicon-and-packs.md §3.5）

CREATORS = os.path.join(LEX, "names-creators.tsv")
STORES = os.path.join(LEX, "names-stores.tsv")
CREATOR, STORE = "creator", "store"
TAGS = (TAG, CREATOR, STORE)            # 檔尾順序：既有列 → creator → store
# 同一個讀音有好幾個新名字時，論壇 A 文章數多的留第一，後面的扣 k × SLANG_BEHIND 只當候選；和網路用語同一個值（匯入，不重複定義）。
SLANG_BEHIND = bo.SLANG_BEHIND


def _lines(text):
    """保留每一行的原始位元組（含行尾）；不用 splitlines（它也在 U+2028 等字元上斷行）。"""
    return re.findall(r"[^\n]*\n|[^\n]+", text)


def creator_lines(path):
    """來源檔（names-creators.tsv／names-stores.tsv）的資料行（略過 # 開頭與空行；CRLF 的行尾 \r 去掉）→ [(行號, 行)]；檔案不存在時是空的。"""
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig") as f:
        lines = [l.rstrip("\r") for l in f.read().split("\n")]
    return [(n, l) for n, l in enumerate(lines, 1) if not l.startswith("#") and l.strip()]


def base_char_readings(base):
    """{(單字, 音節)}：基底裡單字詞的讀音。"""
    return {(w, syls[0]) for syls, ws in base.by_reading.items() if len(syls) == 1 for w, _ in ws}


def _toneless(syl):
    return re.sub("[ˊˇˋ˙]", "", syl)


def creator_rows(sources, taken, scores, chars, names_readings=frozenset(), skip=frozenset()):
    """sources：[(來源檔路徑, 標籤)]，依序 creator、store。每個檔：詞、讀音（音節以空白分隔）、備註，可選第四欄「加分」（一個數字，加到這一列的分數；
    店名輸給沒人這樣講的組合時提高名單加分，見 docs/spec/lexicon-and-packs.md §3.5）；# 開頭與空行略過 → (names-add.tsv 的列, names-sources.tsv 的列, 被擋的列)，
    列的順序是檔案順序（先 creator 檔再 store 檔）。每列：三欄；詞是 2–10 個漢字、字數在 scores 裡、不在 taken（基底、疊加層、名單其他列、詞包、前面的列）；
    讀音的音節數等於字數、每個音節都是基底裡那個字（chars）的讀音。不符就中止並指出行號；(詞, 讀音) 重複也中止；同一個詞可以有不同讀音的好幾列。
    被擋（印出、不中止）：詞在 skip（迷因詞包）；讀音等於名字層既有列（names_readings，讀音以 - 連接）的讀音；store 的讀音等於 creator 列的讀音（creator 已出貨）。
    同標籤內、同讀音的名字照檔案順序排（檔案已照 論壇 A 文章數排好；names-creators.tsv 的峰哥／風哥是人工調過的，不能改成依數字重排），分數＝scores[字數]，第 k 個（k 從 0 起）再扣 k × SLANG_BEHIND。
    被擋列是 (字串, 讀音, 對手, 原因)。"""
    rows, taken, pairs = [], set(taken), set()
    toneless = {(c, _toneless(s)) for c, s in chars}
    blocked = []
    creator_readings = {}                                   # 讀音鍵 → 第一個留下的 creator 詞
    words_by_tag = {}
    other_tag = {STORE: words_by_tag.setdefault(CREATOR, set()), CREATOR: words_by_tag.setdefault(STORE, set())}   # 同一個詞不跨標籤（只有同標籤可換讀音）
    for path, tag in sources:
        cand = []
        for n, line in creator_lines(path):
            fields = line.split("\t")
            ok = len(fields) in (3, 4)
            boost = 0.0
            if ok and len(fields) == 4:
                try:
                    boost = float(fields[3])
                except ValueError:
                    ok = False
                ok = ok and math.isfinite(boost) and boost >= 0
            if ok:
                w, reading, note = fields[:3]
                syls = reading.split(" ")
                ok = bool(bap.HAN.fullmatch(w)) and len(w) in scores and len(syls) == len(w) and (w, reading) not in pairs and (w not in taken or any(p[0] == w for p in pairs) and w not in other_tag.get(tag, ())) \
                    and all((c, s) in chars or (s.endswith("˙") and (c, _toneless(s)) in toneless) for c, s in zip(w, syls))     # 輕聲：基底有這個字的同音節（任何聲調）就算
            if not ok:
                raise SystemExit(f"{path}:{n}: bad {tag} row (need word, reading, note[, finite boost >= 0]; Han word not already in a lexicon layer, same word only with a different reading; one syllable that is a reading of its character): {line!r}")
            pairs.add((w, reading))
            words_by_tag.setdefault(tag, set()).add(w)
            key = "-".join(syls)
            if w in skip:
                blocked.append((w, reading, "meme-add.tsv", "already in the meme pack"))
            elif key in names_readings:
                blocked.append((w, reading, names_readings[key], "same reading as a names-layer row"))
            elif tag == STORE and key in creator_readings:
                blocked.append((w, reading, creator_readings[key], "same reading as a creator row"))
            else:
                cand.append((w, key, syls, note, boost))
        count = {}
        for w, key, syls, note, boost in cand:
            rank = count[key] = count.get(key, -1) + 1
            if boost and rank:
                raise SystemExit(f"{path}: {w!r} has a boost but is behind another row of the same reading (rank {rank}); a boost only goes on the first")
            if tag == CREATOR and rank == 0:
                creator_readings[key] = w
            rows.append((tag, w, syls, note, round(scores[len(w)] + boost - rank * SLANG_BEHIND, 8), w not in taken))
            taken.add(w)
    add, srcs = [], []
    for tag, w, syls, note, score, first in rows:
        add += bo.overlay_rows(w, syls, score, tag)
        if first:
            srcs.append(f"{w}\t{tag}\t{note}\t\t\n")
    return add, srcs, blocked


def append_creators(lex_dir=LEX, creators=CREATORS, acg_add=ACG_ADD, it_add=bo.IT_PACK, base=None, scores=None, stores=STORES, meme_add=MEME_ADD, beauty_add=None, quotes_add=None):
    """docs/spec/lexicon-and-packs.md §3.5 的追加模式：lex_dir 裡的 names-add.tsv／names-sources.tsv 去掉 creator 與 store 列後逐位元組保留，檔尾接 creators（標籤 creator）再接 stores（標籤 store）的列，
    names.json 跟著改（sources 分別計兩種標籤）；重跑是換掉舊的這兩種列。某一個來源檔不存在或沒有列，就不產生那種標籤的列；兩個都沒有時什麼都不碰（舊的列也留著）。
    被擋的列（見 creator_rows）印到 stderr。"""
    if not (creator_lines(creators) or creator_lines(stores)):
        return 0
    path = lambda n: os.path.join(lex_dir, n)

    def read(n):
        with open(path(n), encoding="utf-8", newline="") as f:
            return f.read()
    mine = (CREATOR, STORE)
    add, srcs = _lines(read("names-add.tsv")), _lines(read("names-sources.tsv"))
    add = [l for l in add if l.split("\t")[3].rstrip("\n") not in mine]
    srcs = [l for l in srcs if l.split("\t")[1] not in mine]
    base = base or (bap.base_lexicon() if lex_dir == LEX else ime.Lexicon(path("mcbpmf-data.txt")))
    if scores is None:
        scores = {n: round(v + NAMES_BONUS, 8) for n, v in bap.scores(base).items()}
    meme = file_words(meme_add)
    taken = (known_words(lex_dir, acg_add, meme_add, it_add, beauty_add, quotes_add) - meme) | file_words(it_add) | {l.split("\t")[1] for l in add}
    names_readings = {}
    for l in add:
        names_readings.setdefault(l.split("\t")[0], l.split("\t")[1])
    new_add, new_srcs, blocked = creator_rows([(creators, CREATOR), (stores, STORE)], taken, scores, base_char_readings(base), names_readings, meme)
    for b in blocked:
        print("blocked (string, reading, rival, reason): " + "\t".join(b), file=sys.stderr)
    out = {"names-add.tsv": "".join(add + new_add), "names-sources.tsv": "".join(srcs + new_srcs)}
    got = {t: len({r.split("\t")[1] for r in new_add if r.split("\t")[3].rstrip("\n") == t}) for t in mine}
    manifest = json.loads(read("names.json"))
    old = {t: manifest["sources"].pop(t, 0) for t in mine}
    if sum(old.values()) or sum(got.values()):      # 追加前後都沒有這兩種列時 names.json 原樣不動
        manifest["sources"].update({t: n for t, n in got.items() if n})
        manifest["words"] += sum(got.values()) - sum(old.values())
        manifest["rows"] = out["names-add.tsv"].count("\n")
        manifest["files"] = dict(manifest["files"], **{k: {"sha256": hashlib.sha256(t.encode()).hexdigest(), "bytes": len(t.encode()), "lines": t.count("\n")} for k, t in out.items()})
        manifest["version"] = manifest["version"].rsplit("-", 1)[0] + "-" + manifest["files"]["names-add.tsv"]["sha256"][:8]
        out["names.json"] = json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    for name, text in out.items():
        if text != read(name):          # 沒有變的檔不碰
            with open(path(name) + ".tmp", "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            os.replace(path(name) + ".tmp", path(name))
    return sum(got.values())


# ---------------------------------------------------------------- 政府開放資料離線解析器

class OpenDataSpec(collections.namedtuple("OpenDataSpec", "dataset_id relpath url agency fmt encoding parser")):
    pass


ATTRACTIONS_SUFFIX_RE = re.compile(r"(?:商場|夜市|樂園|博物館|美術館|園區|故事館|紀念館|影城|戲院|古道|步道|吊橋|百貨|廣場|劇場|演藝廳|文化中心|藝術中心|音樂廳)$")
_PARENS_RE = re.compile(r"[（(][^)）]*[)）]")
_TRAIL_PARENS_RE = re.compile(r"[（(][^)）]*[)）]$")
_STATION_CODE_RE = re.compile(r"_[A-Za-z0-9,]+(?:_[A-Za-z0-9,]+)*$")
_TRA_PREFIX_RE = re.compile(r"^(?:阿里山林業鐵路|阿里山林鐵|臺鐵|台鐵)")
_THSR_PREFIX_RE = re.compile(r"^(?:台灣高鐵|臺灣高鐵|高鐵)")
_MRT_PREFIX_RE = re.compile(r"^(?:臺北捷運|北捷|新北捷運|新北捷|桃園機場捷運|機捷|臺中捷運|中捷|高雄捷運|高捷)")
_LRT_PREFIX_RE = re.compile(r"^(?:新北捷運安坑輕軌|新北安坑輕軌|新北捷運淡海輕軌|新北淡海輕軌|新北捷運|新北輕軌|高雄捷運環狀輕軌|高雄輕軌)")
_BARE_AMP_RE = re.compile(r"&(?!amp;|lt;|gt;|quot;|apos;|#\d+;|#x[0-9a-fA-F]+;)")


def _read_csv_rows(raw, encoding):
    txt = raw.decode(encoding).replace("\ufeff", "")
    return list(csv.DictReader(io.StringIO(txt)))


def _read_zip_dbf(raw, encoding="utf-8"):
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        dbfs = [n for n in z.namelist() if n.lower().endswith(".dbf")]
        if not dbfs:
            raise SystemExit("zip_dbf: no .dbf entry in archive")
        dbf = z.read(dbfs[0])
    num_rec, hdr_len, rec_len = struct.unpack("<IHH", dbf[4:12])
    fields = []
    pos = 32
    while pos < hdr_len - 1 and dbf[pos] != 0x0D:
        fname = dbf[pos:pos + 11].split(b"\x00", 1)[0].decode("ascii")
        flen = dbf[pos + 16]
        fields.append((fname, flen))
        pos += 32
    recs = []
    pos = hdr_len
    for _ in range(num_rec):
        rec = dbf[pos:pos + rec_len]
        pos += rec_len
        if len(rec) < rec_len or rec[0] == 0x2A:
            continue
        row, off = {}, 1
        for fname, flen in fields:
            val = rec[off:off + flen].split(b"\x00", 1)[0]
            off += flen
            try:
                s = val.decode(encoding)
            except UnicodeDecodeError:
                s = val.decode("cp950", "replace")
            row[fname] = s.replace("\ufeff", "").strip()
        recs.append(row)
    return recs


def _clean_text(s):
    return (s or "").replace("\ufeff", "").strip()


def _tra_variants(raw):
    s = _TRA_PREFIX_RE.sub("", _clean_text(raw)).strip()
    s = _PARENS_RE.sub("", s).strip()
    stem = re.sub(r"(?:車站|站)$", "", s)
    if stem and HANX.fullmatch(stem):
        return [stem + "站", stem + "車站"]
    return []


def _thsr_variants(raw):
    s = _THSR_PREFIX_RE.sub("", _clean_text(raw)).strip()
    s = _PARENS_RE.sub("", s).strip()
    if s.endswith(("車站", "站")):
        stem = re.sub(r"(?:車站|站)$", "", s)
        if stem and HANX.fullmatch(stem):
            return [stem + "站", stem + "車站"] if not s.endswith("高鐵站") else [s]
    return [s] if s and HANX.fullmatch(s) else []


def _mrt_variants(raw):
    s = _PARENS_RE.sub("", _clean_text(raw)).strip()
    if not s:
        return []
    if s.endswith(("車站", "總站")):
        return [s] if HANX.fullmatch(s) else []
    stem = s[:-1] if s.endswith("站") else s
    if stem and HANX.fullmatch(stem):
        return [stem + "站", "捷運" + stem + "站"]
    return []


def _lrt_variants(raw):
    s = _PARENS_RE.sub("", _clean_text(raw)).strip()
    return [s] if s and HANX.fullmatch(s) else []


def _parse_tra_json(raw, spec):
    items = json.loads(raw.decode(spec.encoding))
    out = []
    for it in items:
        out.extend(_tra_variants(it.get("stationName", "")))
    return len(items), out


def _parse_tra_dbf(raw, spec):
    recs = _read_zip_dbf(raw, spec.encoding)
    out = []
    for r in recs:
        for col in ("MARKNAME1", "MARKNAME2"):
            out.extend(_tra_variants(r.get(col, "")))
    return len(recs), out


def _parse_thsr_dbf(raw, spec):
    recs = _read_zip_dbf(raw, spec.encoding)
    out = []
    for r in recs:
        for col in ("MARKNAME1", "MARKNAME2"):
            out.extend(_thsr_variants(r.get(col, "")))
    return len(recs), out


def _parse_trtc_csv(raw, spec):
    recs = _read_csv_rows(raw, spec.encoding)
    out = []
    for r in recs:
        m = re.match(r"^\{([^,}]+)", _clean_text(r.get("StationName", "")).strip("'"))
        if m:
            out.extend(_mrt_variants(m.group(1)))
    return len(recs), out


def _parse_taipei_dorts_json(raw, spec):
    feats = json.loads(raw.decode(spec.encoding)).get("features", [])
    out = []
    for f in feats:
        out.extend(_mrt_variants(f.get("properties", {}).get("NAME", "")))
    return len(feats), out


def _parse_mrt_dbf(raw, spec):
    recs = _read_zip_dbf(raw, spec.encoding)
    out = []
    for r in recs:
        m1, m2 = r.get("MARKNAME1", ""), r.get("MARKNAME2", "")
        if "出口" in m1 or "出入口" in m1 or "出口" in m2 or "出入口" in m2:
            continue
        for val in (m1, m2):
            s = _STATION_CODE_RE.sub("", _clean_text(val)).strip()
            if s.startswith("貓空纜車"):
                s = _PARENS_RE.sub("", s[len("貓空纜車"):]).strip()
                if s and HANX.fullmatch(s):
                    out.append(s)
            else:
                out.extend(_mrt_variants(_MRT_PREFIX_RE.sub("", s)))
    return len(recs), out


def _parse_lrt_dbf(raw, spec):
    recs = _read_zip_dbf(raw, spec.encoding)
    out = []
    for r in recs:
        m1, m2 = r.get("MARKNAME1", ""), r.get("MARKNAME2", "")
        if "出口" in m1 or "出入口" in m1 or "出口" in m2 or "出入口" in m2:
            continue
        for val in (m1, m2):
            s = _STATION_CODE_RE.sub("", _clean_text(val)).strip()
            s = _LRT_PREFIX_RE.sub("", s).strip()
            out.extend(_lrt_variants(s))
    return len(recs), out


def _parse_tymetro_xml(raw, spec):
    et = ET.fromstring(raw.decode(spec.encoding).replace("\ufeff", ""))
    els = [el for el in et.iter() if el.tag.rsplit("}", 1)[-1] == "Zh_tw"]
    out = []
    for el in els:
        out.extend(_mrt_variants(el.text or ""))
    return len(els), out


def _parse_csv_col(col, fn):
    def parse(raw, spec):
        recs = _read_csv_rows(raw, spec.encoding)
        out = []
        for r in recs:
            out.extend(fn(r.get(col, "")))
        return len(recs), out
    return parse


def _normalize_hospital(raw):
    s = _clean_text(raw)
    s = re.sub(r"[（(〈\-－]委託.*$", "", s)
    s = re.sub(r"及其.+$", "", s)
    s = re.sub(r"附設民眾診療服務處.*$", "", s)
    s = re.sub(r"^.*?(?:醫療財團法人|醫療社團法人|財團法人私立|財團法人|社團法人)", "", s)
    s = re.sub(r"^(?:天主教|臺灣基督教|基督教|佛教)", "", s).strip()
    if not s or "?" in s or "？" in s or not HANX.fullmatch(s):
        return []
    out = [s]
    m = re.match(r"^衛生福利部(.+醫院)$", s)
    if m:
        out.append("部立" + m.group(1))
    return out


def _parse_nhi_hospitals(raw, spec):
    recs = _read_csv_rows(raw, spec.encoding)
    out = []
    for r in recs:
        end = _clean_text(r.get("終止合約或歇業日期", ""))
        if not end or end >= NHI_AS_OF:
            out.extend(_normalize_hospital(r.get("醫事機構名稱", "")))
    return len(recs), out


def _clean_company_name(raw):
    s = _clean_text(raw)
    if not s or re.search(r"[?？+]", s):
        return ""
    s = re.sub(r"[（(]股[)）](?:公司)?$", "股份有限公司", s)
    if re.search(r"[()（）〈〉]", s) or s.endswith("分公司"):
        return ""
    s = strip_company(s)
    return s if bap.HAN.fullmatch(s) else ""


_BIG5_FALLBACK_RE = re.compile(r"(?:牛牛牛|方方土|\+)")


def _parse_mops_companies(raw, spec):
    recs = _read_csv_rows(raw, spec.encoding)
    out = []
    for r in recs:
        raw_cn = _clean_text(r.get("公司名稱", ""))
        cn = _clean_company_name(raw_cn)
        if cn:
            out.append(cn)
        cs = re.sub(r"(?:\*|[-－](?:KY|創)|股份)+$", "", _clean_text(r.get("公司簡稱", ""))).strip()
        if len(cs) >= 3 and bap.HAN.fullmatch(cs) and not _BIG5_FALLBACK_RE.search(cs):
            if not any(m in cs for m in re.findall(r"[（(]([^()（）]+)[)）]", raw_cn) if _BIG5_FALLBACK_RE.search(m)):
                out.append(cs)
    return len(recs), out


def _parse_fsc_hq(raw, spec):
    recs = _read_csv_rows(raw, spec.encoding)
    out = []
    for r in recs:
        if _clean_text(r.get("機構代號", "")) == "":
            cn = _clean_company_name(r.get("機構名稱", ""))
            if cn:
                out.append(cn)
    return len(recs), out


def _parse_fhc(raw, spec):
    recs = _read_csv_rows(raw, spec.encoding)
    out = []
    for r in recs:
        fhc = _clean_company_name(r.get("公司名稱", ""))
        if fhc:
            out.append(fhc)
            if fhc.endswith("金融控股"):
                short = fhc[:-4] + "金控"
                if bap.HAN.fullmatch(short):
                    out.append(short)
        sub = _clean_company_name(r.get("直接投資之子公司名稱", ""))
        if sub:
            out.append(sub)
    return len(recs), out


def _parse_insurers_json(raw, spec):
    items = json.loads(raw.decode(spec.encoding))
    out = []
    for it in items:
        s = _TRAIL_PARENS_RE.sub("", _clean_text(it.get("INSURER_Name", ""))).strip()
        if not s or s.endswith("分公司") or "?" in s or "？" in s:
            continue
        s = strip_company(s)
        if bap.HAN.fullmatch(s):
            out.append(s)
        short = s.replace("人壽保險事業", "人壽").replace("產物保險", "產險").replace("人壽保險", "人壽")
        if short != s and bap.HAN.fullmatch(short):
            out.append(short)
    return len(items), out


def _parse_securities_col(col):
    def parse(raw, spec):
        recs = _read_csv_rows(raw, spec.encoding)
        out = []
        for r in recs:
            cn = _clean_company_name(html.unescape(_clean_text(r.get(col, ""))))
            if cn:
                out.append(cn)
        return len(recs), out
    return parse


def _clean_han_item(raw):
    s = _clean_text(raw)
    if not s or "?" in s or "？" in s:
        return []
    return [s] if bap.HAN.fullmatch(s) else []


def _parse_json_key(key):
    def parse(raw, spec):
        items = json.loads(raw.decode(spec.encoding))
        out = []
        for it in items:
            out.extend(_clean_han_item(it.get(key, "")))
        return len(items), out
    return parse


def _parse_cip_json(raw, spec):
    data = json.loads(raw.decode(spec.encoding))
    recs = data[0]["result"]["records"] if isinstance(data, list) and data and "result" in data[0] else data.get("result", {}).get("records", [])
    out = []
    for r in recs:
        out.extend(_clean_han_item(r.get("館名", "")))
    return len(recs), out


def _parse_attractions_7777(raw, spec):
    atts = json.loads(raw.decode(spec.encoding)).get("Attractions", [])
    out = []
    for a in atts:
        s = _clean_text(a.get("AttractionName", ""))
        if s and "?" not in s and "？" not in s and bap.HAN.fullmatch(s) and ATTRACTIONS_SUFFIX_RE.search(s):
            out.append(s)
    return len(atts), out


def _parse_night_market_col(col):
    def parse(raw, spec):
        recs = _read_csv_rows(raw, spec.encoding)
        out = []
        for r in recs:
            s = _PARENS_RE.sub("", _clean_text(r.get(col, ""))).strip()
            s = re.sub(r"(?:攤販臨時集中場|攤販集中區)$", "", s).strip()
            if s and "?" not in s and "？" not in s and s.endswith("夜市") and bap.HAN.fullmatch(s):
                out.append(s)
        return len(recs), out
    return parse


def _parse_geo_xml(tag, escape_amp=False):
    def parse(raw, spec):
        txt = raw.decode(spec.encoding).replace("\ufeff", "")
        if escape_amp:
            txt = _BARE_AMP_RE.sub("&amp;", txt)
        et = ET.fromstring(txt)
        els = [el for el in et.iter() if el.tag.rsplit("}", 1)[-1] == tag]
        out = []
        for el in els:
            out.extend(_clean_han_item(el.text or ""))
        return len(els), out
    return parse


def _parse_tainan_old_places(raw, spec):
    recs = _read_csv_rows(raw, spec.encoding)
    out = []
    for r in recs:
        for part in _clean_text(r.get("地名舊稱/俗稱", "")).split("、"):
            s = _PARENS_RE.sub("", part).strip()
            out.extend(_clean_han_item(s))
    return len(recs), out


def _parse_redev(col, district_col=None):
    def parse(raw, spec):
        recs = _read_csv_rows(raw, spec.encoding)
        out = []
        for r in recs:
            s = _clean_text(r.get(col, "")).replace("\r", "").replace("\n", "").strip()
            if not s or "?" in s or "？" in s:
                continue
            s = _PARENS_RE.sub("", s).strip()
            s = re.sub(r"第[一二三四五六七八九十0-9]+期(?:[一二三四五六七八九十0-9、]+區)?", "", s).strip()
            s = re.sub(r"(?:自辦)?市地重劃區?$", "", s).strip()
            s = re.sub(r"(?:-(?:[一二三四五六七八九十0-9]+|[^-]+)|[一二三四五六七八九十]+(?:、[一二三四五六七八九十]+)+)$", "", s).strip()
            if district_col and r.get(district_col):
                ds = []
                for x in re.split(r"[、,，]", _clean_text(r[district_col])):
                    x = x.strip()
                    if x:
                        ds.append(x)
                        if not x.endswith(("區", "鄉", "鎮", "市")):
                            ds.append(x + "區")
                for d in sorted(ds, key=len, reverse=True):
                    if s.startswith(d) and len(s) - len(d) >= 2:
                        s = s[len(d):].strip()
                        break
            if s and bap.HAN.fullmatch(s):
                out.append(s)
        return len(recs), out
    return parse


OPEN_DATA_SPECS = (
    # Cat 1: 台灣軌道運輸車站 (11)
    OpenDataSpec("33425", "1-transit/tra_stations_33425.json", "https://ods.railway.gov.tw/tra-ods-web/ods/download/dataResource/0518b833e8964d53bfea3f7691aea0ee", "國營臺灣鐵路股份有限公司", "json", "utf-8", _parse_tra_json),
    OpenDataSpec("73226", "1-transit/tra_stations_moi_73226.zip", "https://opdadm.moi.gov.tw/api/v1/no-auth/resource/api/dataset/50508007-F7C7-482B-9959-D178561A0945/resource/2ED44D5D-F4A4-4BC6-A15D-6BB696C60E5F/download", "內政部國土測繪中心", "zip_dbf", "utf-8", _parse_tra_dbf),
    OpenDataSpec("73225", "1-transit/thsr_stations_moi_73225.zip", "https://www.tgos.tw/tgos/VirtualDir/Product/7a95cfc5-4e86-44dc-894d-b3bd6b0c83ae/MARK_%E9%AB%98%E9%90%B5%E8%BB%8A%E7%AB%99_1101208_.zip", "內政部國土測繪中心", "zip_dbf", "utf-8", _parse_thsr_dbf),
    OpenDataSpec("131734", "1-transit/trtc_stations_131734.csv", "https://data.taipei/api/dataset/1eefa68d-7c8d-491b-8e75-66a161947426/resource/c77e91bf-067c-475e-917b-545ff62b7d76/download", "臺北大眾捷運股份有限公司", "csv", "utf-8-sig", _parse_trtc_csv),
    OpenDataSpec("121140", "1-transit/taipei_dorts_stations_121140.json", "https://data.taipei/api/dataset/758e5ae0-e6ee-448b-81f5-316eb68a5ba7/resource/a63e3278-9d10-4916-9f24-e5a4d78afb31/download", "臺北市政府捷運工程局", "json", "utf-8", _parse_taipei_dorts_json),
    OpenDataSpec("73233", "1-transit/mrt_stations_moi_73233.zip", "https://opdadm.moi.gov.tw/api/v1/no-auth/resource/api/dataset/63A7BA62-C429-4F2D-8B73-21AAB9E6EAE0/resource/2396A037-A8FC-450D-BEF3-2D6C69B29287/download", "內政部國土測繪中心", "zip_dbf", "utf-8", _parse_mrt_dbf),
    OpenDataSpec("73230", "1-transit/lrt_stations_moi_73230.zip", "https://www.tgos.tw/tgos/VirtualDir/Product/d1bd5527-f205-44a7-afaf-e7d9b5d89840/MARK_%E8%BC%95%E8%BB%8C%E6%8D%B7%E9%81%8B%E8%BB%8A%E7%AB%99.zip", "內政部國土測繪中心", "zip_dbf", "utf-8", _parse_lrt_dbf),
    OpenDataSpec("128394", "1-transit/tymetro_stations_128394.xml", "https://opendata.tycg.gov.tw/api/dataset/b97daace-ff4d-4c6f-a036-80193992c21c/resource/99e9d1b3-5cfe-4917-8567-d454a39b45e5/download", "桃園市政府桃園捷運公司", "xml", "utf-8-sig", _parse_tymetro_xml),
    OpenDataSpec("144164", "1-transit/tmrt_green_stations_144164.csv", "https://newdatacenter.taichung.gov.tw/api/v1/no-auth/resource.download?rid=f9511cc5-4799-4df9-98b7-0b0f89fc2be9", "臺中捷運股份有限公司", "csv", "utf-8-sig", _parse_csv_col("車站中文", _mrt_variants)),
    OpenDataSpec("88233", "1-transit/krtc_stations_88233.csv", "https://data.kcg.gov.tw/File/DirectDownload/cfda28a1-d62a-4543-afc9-3f7f667b26fc", "高雄市政府捷運工程局", "csv", "utf-8-sig", _parse_csv_col("車站中文名稱", _mrt_variants)),
    OpenDataSpec("102184", "1-transit/klrt_stations_102184.csv", "https://data.kcg.gov.tw/File/DirectDownload/647ddcd5-1d23-4322-acd2-649b517836ba", "高雄市政府捷運工程局", "csv", "utf-8-sig", _parse_csv_col("車站中文站名", _lrt_variants)),
    # Cat 2: 台灣醫院與大型醫療院所 (5)
    OpenDataSpec("39280", "2-hospitals/nhi_39280_medical_centers.csv", "https://info.nhi.gov.tw/api/iode0000s01/Dataset?rId=A21030000I-D21001-003", "衛生福利部中央健康保險署", "csv", "utf-8-sig", _parse_nhi_hospitals),
    OpenDataSpec("39281", "2-hospitals/nhi_39281_regional_hospitals.csv", "https://info.nhi.gov.tw/api/iode0000s01/Dataset?rId=A21030000I-D21002-005", "衛生福利部中央健康保險署", "csv", "utf-8-sig", _parse_nhi_hospitals),
    OpenDataSpec("39282", "2-hospitals/nhi_39282_district_hospitals.csv", "https://info.nhi.gov.tw/api/iode0000s01/Dataset?rId=A21030000I-D21003-003", "衛生福利部中央健康保險署", "csv", "utf-8-sig", _parse_nhi_hospitals),
    OpenDataSpec("6271", "2-hospitals/mol_6271_accredited_hospitals.csv", "https://apiservice.mol.gov.tw/OdService/download/A17000000J-020028-cAw", "勞動部勞工保險局", "csv", "utf-8-sig", _parse_csv_col("醫院名稱", _normalize_hospital)),
    OpenDataSpec("136354", "2-hospitals/mnd_136354_military_hospitals.csv", "https://www.mnd.gov.tw/opendata.aspx?f=%E5%9C%8B%E8%BB%8D%E9%86%AB%E9%99%A2%E5%9F%BA%E6%9C%AC%E8%B3%87%E6%96%99570885", "國防部軍醫局", "csv", "utf-8-sig", _parse_csv_col("Hospital", _normalize_hospital)),
    # Cat 3: 台灣企業、金融保險、電商與連鎖通路 (9)
    OpenDataSpec("18419", "3-business/twse_listed_companies.csv", "https://mopsfin.twse.com.tw/opendata/t187ap03_L.csv", "金融監督管理委員會證券期貨局", "csv", "utf-8-sig", _parse_mops_companies),
    OpenDataSpec("25036", "3-business/tpex_otc_companies.csv", "https://mopsfin.twse.com.tw/opendata/t187ap03_O.csv", "金融監督管理委員會證券期貨局", "csv", "utf-8-sig", _parse_mops_companies),
    OpenDataSpec("28568", "3-business/tpex_emerging_companies.csv", "https://mopsfin.twse.com.tw/opendata/t187ap03_R.csv", "金融監督管理委員會證券期貨局", "csv", "utf-8-sig", _parse_mops_companies),
    OpenDataSpec("28567", "3-business/fsc_public_companies.csv", "https://mopsfin.twse.com.tw/opendata/t187ap03_P.csv", "金融監督管理委員會證券期貨局", "csv", "utf-8-sig", _parse_mops_companies),
    OpenDataSpec("6041", "3-business/fsc_financial_institutions.csv", "https://stat.fsc.gov.tw/api/v1/public/datasets/6041/export", "金融監督管理委員會", "csv", "utf-8-sig", _parse_fsc_hq),
    OpenDataSpec("28775", "3-business/fsc_fhc_headquarters.csv", "https://www.banking.gov.tw/webdowndoc?file=/stat/opendata/banking49.csv", "金融監督管理委員會銀行局", "csv", "utf-8-sig", _parse_fhc),
    OpenDataSpec("7186", "3-business/fsc_insurance_companies.json", "https://ins-info.ib.gov.tw/opendata/json-05010111.aspx", "金融監督管理委員會保險局", "json", "utf-8", _parse_insurers_json),
    OpenDataSpec("20765", "3-business/twse_securities_firms_detail.csv", "https://mopsfin.twse.com.tw/opendata/t187ap18.csv", "金融監督管理委員會證券期貨局", "csv", "utf-8-sig", _parse_securities_col("券商(證券IB、槓桿交易商)簡稱")),
    OpenDataSpec("11545", "3-business/twse_securities_headquarters.csv", "https://www.twse.com.tw/brokerService/brokerList?response=open_data", "金融監督管理委員會證券期貨局", "csv", "utf-8-sig", _parse_securities_col("證券商名稱")),
    # Cat 4: 台灣百貨商場、影城、夜市與藝文場館 (13)
    OpenDataSpec("6242", "4-venues/moc-museums-6242.json", "https://cloud.culture.tw/frontsite/trans/emapOpenDataAction.do?method=exportEmapJson&typeId=H", "文化部", "json", "utf-8", _parse_json_key("name")),
    OpenDataSpec("6244", "4-venues/moc-local-cultural-houses-6244.json", "https://cloud.culture.tw/frontsite/trans/emapOpenDataAction.do?method=exportEmapJson&typeId=C", "文化部", "json", "utf-8", _parse_json_key("name")),
    OpenDataSpec("164242", "4-venues/cip-indigenous-cultural-houses-164242.json", "https://data.cip.gov.tw/API/v1/dump/datastore/A53000000A-112045-001", "原住民族委員會", "json", "utf-8", _parse_cip_json),
    OpenDataSpec("164266", "4-venues/cip-indigenous-museums-164266.json", "https://data.cip.gov.tw/API/v1/dump/datastore/A53000000A-112048-001", "原住民族委員會", "json", "utf-8", _parse_cip_json),
    OpenDataSpec("22213", "4-venues/bamid-cinemas-2025-22213.csv", "https://file.moc.gov.tw/001/Upload/510/relfile/16158/247043/ed396c28-93b3-4a58-9845-25a1e127c095.csv", "文化部影視及流行音樂產業局", "csv", "utf-8-sig", _parse_csv_col("事業名稱", _clean_han_item)),
    OpenDataSpec("7777", "4-venues/motc-attractions-7777.json", "https://media.taiwan.net.tw/XMLReleaseAll_public/v2.0/Zh_tw/Attraction-json.zip", "交通部觀光署", "json", "utf-8-sig", _parse_attractions_7777),
    OpenDataSpec("38257", "4-venues/motc-theme-parks-38257.csv", "https://media.taiwan.net.tw/od/07_DTD/%E6%B0%91%E7%87%9F%E9%81%8A%E6%A8%82%E5%9C%92%E8%B3%87%E6%96%99.csv", "交通部觀光署", "csv", "utf-8-sig", _parse_csv_col("園區名稱", _clean_han_item)),
    OpenDataSpec("6848", "4-venues/moea-tourism-factories-6848.csv", "https://www.ida.gov.tw/opendata/02/SDD6848.csv", "經濟部產業發展署", "csv", "utf-8-sig", _parse_csv_col("觀光工廠名稱", _clean_han_item)),
    OpenDataSpec("121594", "4-venues/taipei-night-markets-121594.csv", "https://data.taipei/api/dataset/c013d9ec-a550-46bd-ac60-45f085930706/resource/4874f750-b972-4c42-ad76-986960670c70/download", "臺北市市場處", "csv", "cp950", _parse_night_market_col("xbody")),
    OpenDataSpec("25965", "4-venues/taoyuan-night-markets-25965.csv", "https://opendata.tycg.gov.tw/api/dataset/0bd2cf36-7540-416b-911c-1ee3216fd79a/resource/65fc0c5c-3307-487e-8136-13358770c776/download", "桃園市政府經濟發展局", "csv", "utf-8-sig", _parse_night_market_col("夜市名稱")),
    OpenDataSpec("85028", "4-venues/taichung-night-markets-85028.csv", "https://newdatacenter.taichung.gov.tw/api/v1/no-auth/resource.download?rid=ee92dc91-2834-4d46-92c4-5033e77cb9df", "臺中市政府經濟發展局", "csv", "utf-8-sig", _parse_night_market_col("名稱")),
    OpenDataSpec("53634", "4-venues/tainan-night-markets-53634.csv", "https://data.tainan.gov.tw/File/ResourceCsvDownload/611e74b4-d09a-4712-b421-aa062fd02854", "臺南市政府經濟發展局", "csv", "utf-8-sig", _parse_night_market_col("夜市名稱")),
    OpenDataSpec("47005", "4-venues/kaohsiung-night-markets-47005.csv", "https://data.kcg.gov.tw/File/DirectDownload/72d7a481-b2d4-45be-b7f7-58984a650dbf", "高雄市政府經濟發展局", "csv", "utf-8-sig", _parse_night_market_col("市集名稱")),
    # Cat 5: 台灣在地地名、重劃區與山岳步道 (9)
    OpenDataSpec("71520", "5-geography/moa_forest_trails_71520.csv", "https://data.moa.gov.tw/Service/OpenData/ForestRtBasic.aspx?FOTT=CSV&IsTransData=1&UnitId=D51", "農業部(林業及自然保育署)", "csv", "utf-8-sig", _parse_csv_col("TR_CNAME", _clean_han_item)),
    OpenDataSpec("37675", "5-geography/moa_forest_recreation_areas_37675.csv", "https://data.moa.gov.tw/Service/OpenData/ForestAmusementParkData.aspx?FOTT=CSV&IsTransData=1&UnitId=757", "農業部(林業及自然保育署)", "csv", "utf-8-sig", _parse_csv_col("RA_name", _clean_han_item)),
    OpenDataSpec("140066", "5-geography/tainan_old_place_names_140066.csv", "https://data.tainan.gov.tw/File/ResourceCsvDownload/2bbdacfe-8b64-426f-862e-0d474e811dd5", "臺南市政府", "csv", "utf-8-sig", _parse_tainan_old_places),
    OpenDataSpec("125432", "5-geography/ntpc_redev_public_125432.csv", "https://data.ntpc.gov.tw/api/datasets/1b16871f-af8c-480d-bfb1-28bb2d876668/csv/file", "新北市政府地政局", "csv", "utf-8-sig", _parse_redev("aa02")),
    OpenDataSpec("123714", "5-geography/ntpc_redev_private_123714.csv", "https://data.ntpc.gov.tw/api/datasets/321940a7-4986-481d-9499-d499a19a0c70/csv/file", "新北市政府地政局", "csv", "utf-8-sig", _parse_redev("aa02", "aa01")),
    OpenDataSpec("46397", "5-geography/taoyuan_redev_public_46397.csv", "https://opendata.tycg.gov.tw/api/dataset/e67eb1f7-7f77-4801-b0b0-7b9e7abdfeae/resource/ab34a574-6cb0-4fc5-b260-00f225d9df4a/download", "桃園市政府地政局", "csv", "utf-8-sig", _parse_redev("重劃區名稱", "區")),
    OpenDataSpec("53421", "5-geography/tainan_redev_public_53421.csv", "https://data.tainan.gov.tw/File/ResourceCsvDownload/b28c67d4-536a-4ec4-bf53-863dfc7ffe74", "臺南市政府地政局", "csv", "utf-8-sig", _parse_redev("重劃區名稱", "區別")),
    OpenDataSpec("9963", "5-geography/moi_np_trails_9963.xml", "https://opdadm.moi.gov.tw/api/v1/no-auth/resource/api/dataset/2CF9EC81-D0E8-41B0-8C52-F790AF839F5C/resource/E35DB0FD-8495-427E-935A-62D81AE8D9F4/download", "內政部國家公園署", "xml", "utf-8-sig", _parse_geo_xml("TRAIL_NAME")),
    OpenDataSpec("9968", "5-geography/moi_np_poi_9968.xml", "https://opdadm.moi.gov.tw/api/v1/no-auth/resource/api/dataset/EDADC819-18AA-4E3F-83D6-1BC72B90C2EE/resource/D82B4C38-7798-497C-A74E-95A324D46551/download", "內政部國家公園署", "xml", "cp950", _parse_geo_xml("NAME", escape_amp=True)),
)


def read_sha256sums(open_data_dir):
    """讀取 open_data_dir/SHA256SUMS → {relpath: sha256}；缺檔或格式錯就中止。"""
    sums_path = os.path.join(open_data_dir, "SHA256SUMS")
    if not os.path.exists(sums_path):
        raise SystemExit(f"open-data: missing SHA256SUMS in {open_data_dir}")
    expected = {}
    with open(sums_path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            parts = line.split(None, 1)
            if len(parts) != 2 or not re.fullmatch(r"[0-9a-f]{64}", parts[0]):
                raise SystemExit(f"open-data: bad SHA256SUMS line {n}: {line!r}")
            expected[parts[1].lstrip("*")] = parts[0]
    return expected


def load_open_data(open_data_dir, specs=OPEN_DATA_SPECS):
    """核對 SHA256SUMS 並離線解析本地開放資料 → (meta_by_id, [(spec, names)], hospitals_full)。"""
    expected = read_sha256sums(open_data_dir)
    meta, records, hospitals = {}, [], set()
    for spec in specs:
        if spec.relpath not in expected:
            raise SystemExit(f"open-data: {spec.relpath} missing from SHA256SUMS")
        fpath = os.path.join(open_data_dir, spec.relpath)
        if not os.path.exists(fpath):
            raise SystemExit(f"open-data: missing file {fpath}")
        with open(fpath, "rb") as f:
            raw = f.read()
        sha = hashlib.sha256(raw).hexdigest()
        if sha != expected[spec.relpath]:
            raise SystemExit(f"open-data: SHA-256 mismatch for {spec.relpath}: got {sha}, expected {expected[spec.relpath]}")
        rows, names = spec.parser(raw, spec)
        unique_valid = set()
        is_hosp = spec.relpath.startswith("2-hospitals/")
        for n in names:
            if is_hosp and HANX.fullmatch(n) and not simplified(n):
                hospitals.add(n)
            if bap.HAN.fullmatch(n) and not simplified(n):
                unique_valid.add(n)
        meta[spec.dataset_id] = {
            "dataset_id": spec.dataset_id,
            "relpath": spec.relpath,
            "agency": spec.agency,
            "sha256": sha,
            "rows": rows,
            "emitted": len(unique_valid),
        }
        records.append((spec, names))
    return meta, records, hospitals


# ---------------------------------------------------------------- 主流程

def build(api, nsi_commit, manual_tsv=MANUAL, readings_tsv=READINGS_TSV, collisions_tsv=COLLISIONS_TSV, have=None, decode=decode, decode_acg=decode_acg, acg_add=ACG_ADD, readings=bap.make_readings,
          base=None, wiki_categories=WIKI_CATEGORIES, nsi_categories=NSI_CATEGORIES, moe_files=MOE_FILES,
          open_data_specs=OPEN_DATA_SPECS, open_data_dir=None, creators_tsv=CREATORS, stores_tsv=STORES,
          it_add=IT_ADD, beauty_add=BEAUTY_ADD, quotes_add=QUOTES_ADD, meme_add=MEME_ADD, hospital_redirects=True,
          kept_c_tsv=KEPT_C_TSV):
    log = lambda *a: print(*a, file=sys.stderr)
    have = known_words(acg_add=acg_add, meme_add=meme_add, it_add=it_add, beauty_add=beauty_add, quotes_add=quotes_add) if have is None else have
    base = base or bap.base_lexicon()
    dropped = collections.Counter()
    cands = collections.defaultdict(lambda: collections.defaultdict(set))    # 名字 → 種類 → 出處
    qids, memo = collections.defaultdict(set), {}
    revs = set()

    def admit(name, kind, source, qid=None, company=False):
        if company and name.endswith(LIST_SUFFIX):    # docs/spec/lexicon-and-packs.md §3.2：分類裡的「…列表」條目不是名字
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
    sitelinks = []
    for i in range(0, len(ids), 50):
        r = api.wikidata(action="wbgetentities", ids="|".join(ids[i:i + 50]), props="info|sitelinks", sitefilter="zhwiki")
        for q, e in sorted(r["entities"].items()):
            link = e.get("sitelinks", {}).get("zhwiki")
            if "missing" in e or not link:
                continue
            sitelinks.append((q, e.get("lastrevid"), link["title"]))
    tw_titles_batch(api, [t for _, _, t in sitelinks], memo, follow=True)
    for q, lastrevid, title in sitelinks:
        got = memo[(title, True)]
        if got:
            revs.add(got[2])
            admit(got[0], "nsi-wikipedia", f"zhwiki:{got[1]}@{got[2]}+wikidata:{q}@{lastrevid}", q)

    # 中文維基分類：批次檢查分類是否存在，支援每類指定深度（depth=1 展開一層子分類並排除人物／傳記子分類；depth=0 只抓直屬頁）
    categories = {}
    skipped_subcategories = set()
    cat_entries = [e if isinstance(e, tuple) else (e, 1) for e in wiki_categories]
    cat_exists = {}
    cat_names = sorted({c for c, _ in cat_entries})
    for i in range(0, len(cat_names), 50):
        batch = cat_names[i:i + 50]
        q = api.wiki(action="query", titles="|".join(batch), prop="info")["query"]
        norm = {x["from"]: x["to"] for x in q.get("normalized", [])}
        pages_by_t = {p["title"]: p for p in q.get("pages", [])}
        for c in batch:
            p = pages_by_t.get(norm.get(c, c))
            cat_exists[c] = bool(p and not p.get("missing"))
    for cat, depth in cat_entries:
        if not cat_exists.get(cat):
            categories[cat] = {"exists": False}
            continue
        pages = members(api, cat, "page")
        subs = []
        if depth > 0:
            for s in members(api, cat, "subcat"):
                if s in EXCLUDED_SUBCATS or BIO_SUBCAT_RE.search(s):
                    skipped_subcategories.add(s)
                    continue
                subs.append(s)
                pages += members(api, s, "page")
        categories[cat] = {"exists": True, "pages": len(pages), "subcategories": len(subs)}
        han_pages = []
        for t in sorted(set(pages)):
            if not han_title(t):
                dropped["title_not_han"] += 1
            else:
                han_pages.append(t)
        tw_titles_batch(api, han_pages, memo, follow=True)
        for t in han_pages:
            got = memo[(t, True)]
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
    school_abbrs = [
        (t, r)
        for t, rds in sorted(redirects_of(api, sorted(target_schools)).items())
        for r in sorted(set(rds))
        if any(is_abbreviation(r, s) for s in target_schools[t])
    ]
    tw_titles_batch(api, [r for _, r in school_abbrs], memo, follow=False)
    for t, r in school_abbrs:
        got = memo[(r, False)]
        if got:
            revs.add(got[2])
            admit(got[0], "school-abbr", f"zhwiki:{got[1]}->{t}@{got[2]}")

    # 本地預解開放資料與醫院簡稱重定向（醫院名在 10 字截斷前先批次查詢重定向）
    opendata_meta, hospitals_full = {}, set()
    if open_data_dir is not None:
        opendata_meta, od_records, hospitals_full = load_open_data(open_data_dir, open_data_specs)
        for spec, names in od_records:
            for n in names:
                admit(n, "opendata", f"opendata:{spec.dataset_id}")
    if hospital_redirects and hospitals_full:
        h_arts = bap.resolve_titles(api, sorted(hospitals_full))
        target_hospitals = collections.defaultdict(list)
        for h_name, t in h_arts.items():
            if t:
                target_hospitals[t].append(h_name)
        hosp_abbrs = [
            (t, r)
            for t, rds in sorted(redirects_of(api, sorted(target_hospitals)).items())
            for r in sorted(set(rds))
            if any(is_abbreviation(r, s) for s in target_hospitals[t])
        ]
        tw_titles_batch(api, [r for _, r in hosp_abbrs], memo, follow=False)
        for t, r in hosp_abbrs:
            got = memo[(r, False)]
            if got:
                revs.add(got[2])
                admit(got[0], "hospital-abbr", f"zhwiki:{got[1]}->{t}@{got[2]}")

    # 手動補
    manual_reading = {}
    for w, note, *rest in bap.read_tsv(manual_tsv) if os.path.exists(manual_tsv) else []:
        if not bap.HAN.fullmatch(w) or (rest and rest[0].strip() and len(rest[0].split()) != len(w)):
            raise SystemExit(f"{manual_tsv}: bad manual row: {w!r}")
        cands[w]["manual"].add(note)
        if rest and rest[0].strip():
            manual_reading[w] = rest[0].split()

    # 保護既有 creator / store 詞串與讀音，避免被 names 段搶走或造成 append_creators 失敗
    reserved_words, reserved_readings = set(), set()
    for path in (creators_tsv, stores_tsv):
        if path:
            for _, line in creator_lines(path):
                fields = line.split("\t")
                if len(fields) >= 2:
                    reserved_words.add(fields[0])
                    reserved_readings.add("-".join(fields[1].split()))

    # 去重：字串在既有 8 份檔的任何讀音下就不收；既有 creator/store 字串也讓位
    in_lexicon = set(cands) & have
    dropped["in_lexicon"] = len(in_lexicon)
    excluded, decided = bap.read_collisions(collisions_tsv)
    rank = bap.collision_rank(bap.read_tsv(collisions_tsv) if os.path.exists(collisions_tsv) else [])
    cand = []
    for w in sorted(cands):
        if w in have:
            continue
        if w in reserved_words:
            dropped["reserved_for_creator_store"] += 1
            continue
        if w in excluded:
            dropped["excluded_by_names_collisions_tsv"] += 1
            continue
        cand.append(w)

    # 讀音：readings.py（手動列的讀音優先），再套 names-readings.tsv 的整名讀音
    rd = dict(readings([w for w in cand if w not in manual_reading]))
    rd.update({w: (s, False) for w, s in manual_reading.items() if w in cand})
    fixed = read_name_readings(readings_tsv)
    rd.update({w: (s, False) for w, s in fixed.items() if w in cand})
    unread = sorted(set(cand) - set(rd))
    words = []
    for w in cand:
        if w not in rd:
            continue
        if "-".join(rd[w][0]) in reserved_readings:
            dropped["reserved_for_creator_store"] += 1
            continue
        words.append(w)
    reading = {w: rd[w][0] for w in words}
    sc = {n: round(v + NAMES_BONUS, 8) for n, v in bap.scores(base).items()}    # 手動列也走這裡（NAMES_BONUS）
    pack = pack_scores(acg_add)
    nsrc = lambda w: sum(len(s) for s in cands[w].values())

    kept_c = read_kept_c(kept_c_tsv)

    # 同音衝突：(c) 名字把既有詞擠下第一名就丟（既有詞勝；names-kept-c.tsv 顯式允許的除外）；(a) 名單內同音要有處置
    lexicon_dropped = []
    for _ in range(bap.LEXICON_RULE_ROUNDS):
        order = bap.ordered(words, nsrc, rank, reading)
        rows = below_pack([r.rsplit("\t", 1)[0] + f"\t{TAG}\n" for r in bap.pack_rows(order, reading, sc)], rank, set(words), pack)
        found = bap.detect_collisions(words, reading, rows, frozenset(), decode, existing=have)
        hit = {e[0]: (r, e) for r, v in sorted(found.items()) for e in v if e[1] == "c" and (r, e[0]) not in kept_c}
        if not hit:
            break
        lexicon_dropped += [(r, w, o, prof) for w, (r, (_, _, prof, o, _)) in sorted(hit.items())]
        words = [w for w in words if w not in hit]
    else:
        raise SystemExit(f"type (c) collisions not settled after {bap.LEXICON_RULE_ROUNDS} rounds")
    kept_c_applied = {(r, e[0]) for r, v in found.items() for e in v if e[1] == "c" and (r, e[0]) in kept_c}
    keep_now = bap.first_named(rank, set(words))
    # 名字與 ACG 詞包詞同音（詞包預設開著）：詞包開著時名字把詞包詞擠下第一名，一律當未處置的 (a)，由人工處置（排除名字，
    # 或在處置列把詞包詞排前面、名字用 `+`，由 below_pack 排到它後面）。名字只和既有詞庫的詞同音另由上面的 (c) 處理。
    pack_words = set().union(*pack.values()) if pack else set()
    with_pack = bap.detect_collisions(words, reading, rows, frozenset(), decode_acg, existing=pack_words)
    keep_any = {}
    for (r, w), _ in sorted(rank.items(), key=lambda kv: kv[1]):
        keep_any.setdefault(r, w)
    col = {}
    for r, v in found.items():
        left = [e for e in v if not (e[1] == "c" and (r, e[0]) in kept_c) and ((r, e[0]) not in decided or e[4] != keep_now.get(r))]
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
        "nhi_as_of": NHI_AS_OF,
        "words": len(words),
        "rows": len(rows),
        "sources": {k: sum(1 for w in words if k in cands[w]) for k in ("nsi", "nsi-wikipedia", "category", "school", "school-abbr", "opendata", "hospital-abbr", "manual")},
        "nsi_taiwan_brands": nsi_counts,
        "categories": categories,
        "skipped_subcategories": sorted(skipped_subcategories),
        "moe": moe,
        "opendata": opendata_meta,
        "wiki_revisions": {"pages": len(revs), "min": min(revs, default=0), "max": max(revs, default=0)},
        "dropped": dict(sorted(dropped.items())),
        "kept_c": len(kept_c_applied),
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
    out = ["# 未處置的同音衝突（docs/spec/lexicon-and-packs.md §3.5 類型 a：名單內同音）。每列：讀音、詞、條件、設定、不開名單的第一名、開了之後的第一名。\n",
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
    ap.add_argument("--open-data-dir", default=OPEN_DATA_DIR)
    ap.add_argument("--out", default=LEX)
    ap.add_argument("--report", default=os.path.join(ROOT, "build", "names"))
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--nsi-commit", default=NSI_COMMIT)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--append-creators", action="store_true")
    a = ap.parse_args()
    if a.append_creators:
        print(f"{append_creators()} creator and store words in names-add.tsv")
        return
    if a.check:
        check_committed()
        print("names-add.tsv shares no word with the other eight lexicon files")
        return
    if not re.fullmatch(r"[0-9a-f]{40}", a.nsi_commit):
        raise SystemExit("--nsi-commit SHA is required (40 hex digits; e.g. gh api repos/osmlab/name-suggestion-index/commits/main -q .sha)")
    urllib.request.install_opener(urllib.request.build_opener(_Redirect(a.nsi_commit)))
    files, manifest, col, unread, ldrop, check = build(NamesApi(a.cache, a.offline, a.nsi_commit), a.nsi_commit, open_data_dir=a.open_data_dir)
    write_all(files, manifest, a.out)
    if creator_lines(CREATORS) or creator_lines(STORES):         # 重建 = 重建 + 追加：不讓 creator、store 列在重建時消失
        if os.path.abspath(a.out) == os.path.abspath(LEX):
            print(f"{append_creators(a.out)} creator and store words appended")
        else:                           # append_creators reads the base and layers from its lex_dir: another --out has none of them
            print(f"NOTE: --out {a.out} is not {LEX}: creator and store rows NOT appended; run --append-creators on the real lexicon directory")
    os.makedirs(a.report, exist_ok=True)
    for name, text in (("collisions.txt", collisions_text(col, ldrop)), ("unreadable.txt", "".join(w + "\n" for w in unread)), ("check-readings.txt", "".join(check))):
        with open(os.path.join(a.report, name), "w", encoding="utf-8") as f:
            f.write(text)
    print(json.dumps({k: v for k, v in manifest.items() if k not in ("categories", "nsi_taiwan_brands", "opendata")}, ensure_ascii=False, sort_keys=True))
    print(f"unresolved collision readings: {len(col)} (see {os.path.join(a.report, 'collisions.txt')})")


if __name__ == "__main__":
    main()
