"""ACG 詞包建置工具（契約 docs/contracts/acg-pack.md A.1）：由中文維基公共轉換組、作品條目標題與條目人物，
產生 data/packs/acg-add.tsv（疊加層格式）、acg-sources.tsv、acg.json，並把同音衝突列進 collisions.txt。
輸出以 CC BY-SA 4.0 釋出（LICENSES/data.md）。同一份快取跑兩次，輸出逐位元相同；版號是最新來源頁的日期加 acg-add.tsv 的雜湊前 8 碼。

用法：python3 tools/build_acg_pack.py                  抓取（有快取就用快取）並寫入 data/packs/
      python3 tools/build_acg_pack.py --offline        只用快取，缺了就中止
      --cache DIR  --out DIR  --report DIR            快取（預設 ~/.cache/shanjie/sources/acg）、輸出、報告（預設 build/acg-pack）

只連 zh.wikipedia.org/w/api.php 與 query.wikidata.org，依序送出、每秒不超過 1 個、帶 User-Agent 與 maxlag=5。
讀音靠 tools/readings.py（需要萌典與小麥的破音字表，路徑見該檔），同音衝突靠評測 CLI（target/release/shanjie-eval，
沒有就用 cargo 建）與 data/lm/bigram.sjlm。
"""
import argparse
import collections
import functools
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from html import unescape
from html.parser import HTMLParser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import build_overlay as bo  # noqa: E402  分數規則、變調列、基底與疊加層的路徑
import ime  # noqa: E402

PACKS = os.path.join(ROOT, "data", "packs")
LEX = os.path.join(ROOT, "data", "lexicon")
LM = os.path.join(ROOT, "data", "lm", "bigram.sjlm")
CACHE = os.path.expanduser("~/.cache/shanjie/sources/acg")
UA = "shanjie-acg-pack/1.0 (https://github.com/Nanako0129/shanjie; build tool for an optional word pack; contact via the repository)"
WORKS = 400                       # 契約 A.1：sitelink 數最多的作品數
LAST_YEAR, YEARS = 2026, 7        # 契約 A2.1：年度動畫清單「N年日本動畫列表」的最後一年與年數（2020–2026）。不看系統日期，同一份快取任何一天重建都相同；CI 要換年份時明確改 LAST_YEAR
MAX_REQUESTS, MAX_CACHE = 5000, 1_500_000_000   # 契約 A2.7 的停止條件：一次建置的網路請求數、快取位元組數
HAN = re.compile(r"^[一-鿿]{2,10}$")
TAG = "acg"
SPARQL = """SELECT ?w ?sl ?art ?twl WHERE {
  VALUES ?cls { wd:Q63952888 wd:Q20650540 }
  ?w wdt:P31 ?cls ; wikibase:sitelinks ?sl .
  ?art schema:about ?w ; schema:isPartOf <https://zh.wikipedia.org/> .
  OPTIONAL { ?w rdfs:label ?twl FILTER(lang(?twl)="zh-tw") }
} ORDER BY DESC(?sl) LIMIT 450"""


# ---------------------------------------------------------------- 抓取與快取

class Api:
    """快取 + 限速（每秒 ≤ 1 個請求）+ maxlag=5。快取檔名是請求參數的 sha1，內容是回應 JSON。"""

    def __init__(self, cache=CACHE, offline=False):
        self.cache, self.offline, self.last, self.requests = cache, offline, 0.0, 0
        os.makedirs(cache, exist_ok=True)
        self.size = sum(e.stat().st_size for e in os.scandir(cache))

    @staticmethod
    def key(params):
        return hashlib.sha1(urllib.parse.urlencode(sorted(params.items())).encode()).hexdigest()

    def _get(self, key, url):
        """快取命中就用；回應含 error 時丟 RuntimeError(error)。"""
        path = os.path.join(self.cache, key + ".json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "error" in data:     # 快取的 missingtitle 回應：兩種模式都照樣丟出
                raise RuntimeError(data["error"])
            return data
        if self.offline:
            raise SystemExit(f"--offline: not cached: {url[:120]}")
        for attempt in range(6):
            wait = 1.1 - (time.time() - self.last)
            if wait > 0:
                time.sleep(wait)
            if self.requests >= MAX_REQUESTS or self.size > MAX_CACHE:
                raise SystemExit(f"stop (contract A2.7): {self.requests} requests this run, cache {self.size} bytes")
            self.requests += 1
            self.last = time.time()
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            data = json.loads(urllib.request.urlopen(req, timeout=120).read())
            if isinstance(data, dict) and data.get("error", {}).get("code") == "maxlag":
                time.sleep(5 * (attempt + 1))
                continue
            if isinstance(data, dict) and "error" in data and data["error"].get("code") != "missingtitle":
                raise RuntimeError(data["error"])               # 其他錯誤可能是暫時的，不快取；missingtitle 是確定的，快取起來，--offline 才跑得完
            with open(path + ".part", "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(path + ".part", path)
            self.size += os.path.getsize(path)
            if "error" in data:
                raise RuntimeError(data["error"])
            return data
        raise RuntimeError("maxlag did not clear")

    def wiki(self, **p):
        p.update(format="json", formatversion="2", maxlag="5")
        return self._get(self.key(p), "https://zh.wikipedia.org/w/api.php?" + urllib.parse.urlencode(sorted(p.items())))

    def sparql(self, query):
        p = {"query": query, "format": "json"}
        return self._get("sparql-" + hashlib.sha1(query.encode()).hexdigest(), "https://query.wikidata.org/sparql?" + urllib.parse.urlencode(p))


def pages(api, titles):
    """標題 → {title, revid, ts, text}（跟著重新導向與正規化），不存在就是 None。每批 10 個；
    回應超過內容上限會被截斷（後面的頁沒有 revisions、帶 continue），所以照 continue 一直追到沒有為止。"""
    out = {}
    for i in range(0, len(titles), 10):
        b = titles[i:i + 10]
        norm, red, pg, cont = {}, {}, {}, {}
        while True:
            r = api.wiki(action="query", prop="revisions", rvprop="ids|timestamp|content", rvslots="main", redirects="1", titles="|".join(b), **cont)
            q = r["query"]
            norm.update({x["from"]: x["to"] for x in q.get("normalized", [])})
            red.update({x["from"]: x["to"] for x in q.get("redirects", [])})
            for p in q["pages"]:
                if p["title"] not in pg or not pg[p["title"]].get("revisions"):
                    pg[p["title"]] = p
            cont = r.get("continue")
            if not cont:
                break
        for t in b:
            p = pg.get(red.get(norm.get(t, t), norm.get(t, t)))
            rev = p["revisions"][0] if p and p.get("revisions") else None
            out[t] = {"title": p["title"], "revid": rev["revid"], "ts": rev["timestamp"], "text": rev["slots"]["main"]["content"]} if rev else None
    return out


# ---------------------------------------------------------------- 年度動畫清單（契約 A2.1）

def year_titles(years):
    return [f"{y}年日本動畫列表" for y in years]


TOKEN = re.compile(r"(\{\{|\}\}|\[\[|\]\]|\|\||!!|\|)")
CELL_ATTR = re.compile(r"^((?:\s*[A-Za-z-]+\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s|\"']*))+)\s*$")
NOT_ARTICLE = re.compile(r"^(?::|(?:File|Image|檔案|文件|Category|分類|Template|模板|Help|Special|Wikipedia|WP|ja|en|zh|ko|w|wikt|d):)", re.I)


def table_cells(line, header=False):
    """表格列裡的一行（`|a||b` 或 `!a!!b`）→ 儲存格文字清單，只在最外層（不在 {{ }}、[[ ]] 裡）拆 `||`；標題行（header）另外拆 `!!`，資料行裡的 `!!` 是內容。"""
    seps = ("||", "!!") if header else ("||",)
    out, cur, depth = [], [], 0
    for tok in TOKEN.split(line):
        if tok in ("{{", "[["):
            depth += 1
        elif tok in ("}}", "]]"):
            depth -= 1
        elif tok in seps and depth <= 0:
            out.append("".join(cur))
            cur = []
            continue
        cur.append(tok)
    out.append("".join(cur))
    return out


def cell_attrs(cell):
    """`rowspan=2|內容`、`style="…"|內容` → (屬性字串, 內容)；[[a|b]] 裡的 | 不是屬性分隔。"""
    depth = 0
    for m in TOKEN.finditer(cell):
        t = m.group(1)
        depth += (t in ("{{", "[[")) - (t in ("}}", "]]"))
        if t == "|" and depth <= 0:
            return (cell[:m.start()], cell[m.end():]) if CELL_ATTR.match(cell[:m.start()]) else ("", cell)
    return "", cell


def table_rows(block):
    """一個 wikitable 的文字 → (標題列的儲存格, [資料列的儲存格清單])。資料列的 rowspan 往下列延伸（被佔用的欄位補空字串），所以欄位索引與標題對齊。"""
    head, rows, cur, depth = [], [], [], 0
    for line in block.split("\n")[1:]:
        if line.startswith("|}"):
            break
        if depth <= 0 and line.startswith("|-"):
            if cur:
                rows.append(cur)
            cur = []
        elif depth <= 0 and line[:1] in ("|", "!") and not line.startswith("|+"):
            parts = table_cells(line[1:], header=line[0] == "!")
            if line[0] == "!" and not rows and not cur:
                head += parts                                   # 第一列之前的 ! 行是標題列
            else:
                cur += parts
        elif cur:
            cur[-1] += "\n" + line
        elif head:
            head[-1] += "\n" + line
        depth += len(re.findall(r"\{\{|\[\[", line)) - len(re.findall(r"\}\}|\]\]", line))
    if cur:
        rows.append(cur)
    placed, busy = [], {}                                       # busy：欄 → 還要被上面的 rowspan 佔幾列
    for cells in rows:
        row, col = [], 0
        for c in cells:
            while busy.get(col, 0) > 0:
                busy[col] -= 1
                row.append("")
                col += 1
            attrs, text = cell_attrs(c)
            m = re.search(r"rowspan\s*=\s*\"?(\d+)", attrs)
            n = re.search(r"colspan\s*=\s*\"?(\d+)", attrs)
            span = int(n.group(1)) if n else 1
            if span < 1:
                raise SystemExit(f"year list: colspan={span} in a cell: {text[:30]!r}")
            for k in range(span):                                # colspan：佔好幾欄，文字放第一欄
                row.append(text if k == 0 else "")
                if m and int(m.group(1)) > 1:
                    busy[col] = int(m.group(1)) - 1
                col += 1
        for c in sorted(k for k, v in busy.items() if v > 0 and k >= col):   # 這一列比較早結束：後面所有被 rowspan 佔著的欄位（不只是緊接的）都補上並扣掉一列
            row.extend([""] * (c - len(row) + 1))
            busy[c] -= 1
        placed.append(row)
    heads = []
    for c in head:                                              # 標題列的 colspan 也展開，作品名欄的索引才對得上資料列
        attrs, text = cell_attrs(c)
        n = re.search(r"colspan\s*=\s*\"?(\d+)", attrs)
        span = int(n.group(1)) if n else 1
        if span < 1:
            raise SystemExit(f"year list: header colspan={span}")
        heads += [text] + [""] * (span - 1)
    return heads, placed


MIN_YEAR_WORKS = 100      # 一頁年度清單至少要取到這麼多個作品連結（2020–2026 實測最少 200）；少於這個就是頁面結構變了，建置中止（契約 A2.7）


ZH_VARIANTS = {"zh", "zh-hans", "zh-hant", "zh-cn", "zh-tw", "zh-hk", "zh-mo", "zh-sg", "zh-my"}


def zh_tw_branch(text):
    """`-{zh-tw:甲;zh-cn:乙}-` → 甲（沒有 zh-tw 才用 zh-hant、zh，再沒有就取第一個語言的值）；沒有任何語言變體的 `-{甲:乙;丙}-` 整段原樣保留
    （「Re:從零開始…」的冒號不是語言代碼）。其他語言的分支不取。"""
    def pick(m):
        body = re.sub(r"^\s*[A-Za-z]+\s*\|", "", m.group(1))
        langs = {}
        for part in body.split(";"):
            k, sep, v = part.partition(":")
            if sep and k.strip().lower() in ZH_VARIANTS:
                langs[k.strip().lower()] = v.strip()
        if not langs:
            return body.strip()
        for k in ("zh-tw", "zh-hant", "zh"):
            if k in langs:
                return langs[k]
        return next(iter(langs.values()))
    return re.sub(r"-\{(.*?)\}-", pick, text, flags=re.S)


def year_works(text, minimum=0):
    """年度清單頁（wikitext）→ 作品名欄裡的條目連結（出現順序、去重）。每個 wikitable 的標題列必須有「作品名」欄，只取那一欄的 [[連結]]
    （語言轉換 `-{ }-` 只取 zh-tw 的分支）；沒有這一欄、有一列的欄數和標題列對不上（整列橫跨的註腳列除外），或取到的連結少於 minimum，
    建置中止（頁面結構變了，契約 A2.7 的停止條件）。紅連結模板（{{link-ja}}、{{tsl}}）沒有中文條目，不取。"""
    text = re.sub(r"<ref\b[^>]*/>|<ref\b[^>]*>.*?</ref>", "", strip_comments(text, False), flags=re.S)
    out, tables = [], 0
    for block in (b for b in re.split(r"(?m)^(?=\{\|)", text) if b.startswith("{|")):
        if "wikitable" not in block.split("\n", 1)[0]:
            continue
        head, rows = table_rows(block)
        names = [re.sub(r"\{\{.*?\}\}|<[^>]+>|\s", "", h) for h in head]
        if "作品名" not in names:
            raise SystemExit(f"year list: a wikitable without a 作品名 column: {head[:6]}")
        i = names.index("作品名")
        tables += 1
        for row in rows:
            if not (row[i] if i < len(row) else "").strip() and all(not c.strip() for c in row[1:]):     # 橫跨整列的註腳列（作品名欄也是空的）
                continue
            if len(row) > len(head) or len(row) <= i:               # 比標題列多，或短到沒有作品名欄：欄位對不上
                raise SystemExit(f"year list: a row with {len(row)} cells under {len(head)} headers: {[c[:20] for c in row]}")
            cell = zh_tw_branch(re.sub(r"\{\{[^{}]*\}\}", "", row[i]))
            for m in re.finditer(r"\[\[([^\[\]|]+)(?:\|[^\[\]]*)?\]\]", cell):
                t = unescape(m.group(1)).split("#")[0].replace("_", " ").strip()
                if t and not NOT_ARTICLE.match(t) and t not in out:
                    out.append(t)
    if not tables:
        raise SystemExit("year list: no wikitable found")
    if len(out) < minimum:
        raise SystemExit(f"year list: only {len(out)} work links, expected at least {minimum}")
    return out


def resolve_titles(api, titles):
    """年度清單的連結 → {連結: 實際存在的條目標題或 None}。維基百科上不少條目的標題是簡體或另一種寫法（[[淡島百景]] 實際是「淡岛百景」），
    wikitext 的 [[ ]] 靠語言轉換找得到、`action=parse` 不行，所以先用 `converttitles=1&redirects=1` 每批 50 個查；連結本身就存在的照原樣（快取命中），
    查不到的（紅連結）是 None。"""
    out = {}
    for i in range(0, len(titles), 50):
        b = titles[i:i + 50]
        q = api.wiki(action="query", titles="|".join(b), converttitles="1", redirects="1")["query"]
        step = {x["from"]: x["to"] for k in ("normalized", "converted", "redirects") for x in q.get(k, [])}
        exists = {p["title"] for p in q["pages"] if not p.get("missing") and not p.get("invalid")}
        for t in b:
            c = t
            for _ in range(5):
                if c not in step:
                    break
                c = step[c]
            out[t] = None if c not in exists else (t if t in exists or c == t else c)
    return out


# ---------------------------------------------------------------- 轉換組

def read_tsv(path):
    return [l.rstrip("\n").split("\t") for l in open(path, encoding="utf-8") if l.strip() and not l.startswith("#")]


def read_groups(path):
    """acg-groups.tsv：區段、組名、模組、收錄、類別或理由。第四欄只接受 include／exclude（契約 A2.5），拼錯不能靜靜地當成排除。"""
    rows = read_tsv(path)
    for r in rows:
        if len(r) != 5 or r[3] not in ("include", "exclude"):
            raise SystemExit(f"{path}: bad group row (column 4 must be include or exclude): {r}")
    return rows


LIST_ITEM = re.compile(r"\{\{CGroup/list/item\|([^|}]*)\|([^|}]*)\|([^|}]*)\|")
ACG_SECTION = re.compile(r"colspan=4[^|]*\|\s*(.*)")


def list_items(text):
    """Template:CGroup/list → [(段名, 中文全稱, 原文全稱, 簡稱)]；只留標題含 ACG 的段。"""
    sec, out = "", []
    for line in text.split("\n"):
        m = ACG_SECTION.search(line)
        if m:
            sec = m.group(1).strip()
        m = LIST_ITEM.match(line)
        if m and "ACG" in sec:
            out.append((sec, m.group(1).strip(), m.group(2).strip(), m.group(3).strip()))
    return out


def lua_items(x):
    """Lua 模組裡每個 Item( 呼叫的引數清單。"""
    i, n = 0, len(x)
    while True:
        j = x.find("Item(", i)
        if j < 0:
            return
        k, args, depth = j + 5, [], 1
        while k < n and depth > 0:
            c = x[k]
            if c in "\"'":
                e, s = k + 1, []
                while e < n and x[e] != c:
                    if x[e] == "\\" and e + 1 < n:
                        s.append(x[e + 1])
                        e += 2
                        continue
                    s.append(x[e])
                    e += 1
                args.append("".join(s))
                k = e + 1
                continue
            if c == "[" and re.match(r"\[=*\[", x[k:k + 6]):
                m = re.match(r"\[(=*)\[", x[k:])
                close = "]" + m.group(1) + "]"
                e = x.find(close, k + len(m.group(0)))
                if e < 0:                                    # 沒有結尾的 [[：模組壞了，到此為止
                    return
                args.append(x[k + len(m.group(0)):e])
                k = e + len(close)
                continue
            if c == "-" and x[k:k + 2] == "--":
                e = x.find("\n", k)
                k = e if e >= 0 else n
                continue
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
            k += 1
        i = k
        yield args


def wiki_title(url):
    """SPARQL 回的條目網址 → 條目標題（只拆掉 /wiki/ 前綴，標題裡的 / 如 .hack//SIGN 要留著）。"""
    return urllib.parse.unquote(url.split("/wiki/", 1)[1])


def wiki_items(x):
    for m in re.finditer(r"\{\{\s*(?:CItem|CItemHidden|CItemLan|CI|CNoteA)\s*\|(.*)", x):
        line = m.group(1)
        e = line.rfind("}}")
        yield line[:e] if e >= 0 else line


LANG = re.compile(r"^\s*(?:(.*?)=>)?\s*(zh-[a-z]+)\s*:(.*)$", re.S)


NAMED = re.compile(r"^\s*[A-Za-z_]\w*\s*=(?!>)")
POS = re.compile(r"^\s*\d+\s*=(?!>)")      # {{CItem|1=zh-tw:X;zh-cn:Y}}：數字開頭的是位置參數，只是明寫編號


def params(rule):
    """模板內容照最外層的 | 拆成參數（{{ }}、[[ ]] 裡的 | 不拆）；具名參數（original=、desc= 等）丟掉，只留語言轉換那幾個。"""
    out, depth, cur = [], 0, []
    for tok in re.split(r"(\{\{|\}\}|\[\[|\]\]|\|)", rule):
        if tok in ("{{", "[["):
            depth += 1
        elif tok in ("}}", "]]"):
            depth -= 1
        elif tok == "|" and depth <= 0:
            out.append("".join(cur))
            cur = []
            continue
        cur.append(tok)
    out.append("".join(cur))
    return [POS.sub("", p, count=1) for p in out if not NAMED.match(p)]


def tw_values(rule):
    """一條規則的 zh-tw 值（含 X=>zh-tw:Y 單向規則）；沒有 zh-tw 才退回 zh-hant。回傳 [(詞, 'zh-tw'|'zh-hant')]，已拆 / 、去《》、只留 2–10 個漢字。"""
    rule = re.sub(r"-\{|\}-", "", rule)
    out = []
    for prm in params(rule):
        cl = [(m.group(2), m.group(3).strip()) for m in (LANG.match(c) for c in prm.split(";")) if m]
        tw = [c for c in cl if c[0] == "zh-tw"]
        use, src = (tw, "zh-tw") if tw else ([c for c in cl if c[0] == "zh-hant"], "zh-hant")
        for _, v in use:
            v = re.sub(r"^《(.*)》$", r"\1", v.strip())
            out += [(q.strip(), src) for q in v.split("/") if HAN.match(q.strip())]
    return out


def strip_comments(text, lua):
    """去掉註解再解析：維基文字的 <!-- … -->；Lua 模組另外去掉 -- … 與 --[[ … ]]（字串裡的 -- 不算）。"""
    text = re.sub(r"<!--.*?(?:-->|\Z)", "", text, flags=re.S)
    if not lua:
        return text
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c in "\"'":
            e = i + 1
            while e < n and text[e] != c:
                e += 2 if text[e] == "\\" else 1
            out.append(text[i:e + 1])
            i = e + 1
        elif c == "[" and (m := re.match(r"\[(=*)\[", text[i:i + 40])):
            e = text.find("]" + m.group(1) + "]", i + len(m.group(0)))
            e = n if e < 0 else e + len(m.group(1)) + 2
            out.append(text[i:e])
            i = e
        elif text.startswith("--", i):
            m = re.match(r"--\[(=*)\[", text[i:i + 40])
            if m:
                e = text.find("]" + m.group(1) + "]", i + len(m.group(0)))
                i = n if e < 0 else e + len(m.group(1)) + 2
            else:
                e = text.find("\n", i)
                i = n if e < 0 else e
        else:
            out.append(c)
            i += 1
    return "".join(out)


def group_rules(text, lua=False):
    """頁面裡的規則：{{CItem…}} 模板、Lua 的 Item( 呼叫與 rule = 欄位，同一頁兩種都解析（例如 Module:CGroup/閃電十一人）；註解先去掉。lua＝這頁是 Lua 模組。"""
    text = strip_comments(text, lua)
    return list(wiki_items(text)) + [a[-1] for a in lua_items(text) if a] + [m.group(2) for m in re.finditer(r"\brule\s*=\s*(['\"])(.*?)\1", text)]


def resolve_groups(api, rows, items):
    """每個組 → 頁面記錄 {title, revid, ts, text}（Lua 模組只有 require 時，跟到被引用的模組）。回傳 (組 → 記錄或 None, 所有抓到的頁)。"""
    by_key = {(i[1], i[2]): i for i in items}
    by_zh = {i[1]: i for i in items}
    cands = {}
    for r in rows:
        it = by_key.get((r[1], r[2])) or by_zh.get(r[1])
        c = [r[2], it[3] if it else "", r[1], it[2] if it else ""]
        cands[(r[1], r[2])] = [x for x in dict.fromkeys(c) if x and x != "--"] or [r[1]]
    got, chosen = {}, {}
    for rank in range(4):
        todo = [g for g, c in cands.items() if g not in chosen and rank < len(c)]
        titles = [f"{ns}:CGroup/{cands[g][rank]}" for g in todo for ns in ("Module", "Template")]
        titles = [t for t in dict.fromkeys(titles) if t not in got]
        got.update(pages(api, titles))
        for g in todo:
            for ns in ("Module", "Template"):
                if got.get(f"{ns}:CGroup/{cands[g][rank]}"):
                    chosen[g] = got[f"{ns}:CGroup/{cands[g][rank]}"]
                    break
    for _ in range(5):                                   # 短模組只 require 另一個：換成被引用的
        need = {}
        for g, p in chosen.items():
            m = re.search(r"require\(\s*['\"]([^'\"]+)['\"]", p["text"])
            if len(p["text"]) < 400 and m and not m.group(1).replace("模块:", "Module:").startswith("Module:CGroup/core"):
                need[g] = m.group(1).replace("模块:", "Module:")
        if not need:
            break
        got.update(pages(api, [t for t in dict.fromkeys(need.values()) if t not in got]))
        for g, t in need.items():
            if got.get(t) and got[t]["title"] != chosen[g]["title"]:
                chosen[g] = got[t]
    return {g: chosen.get(g) for g in cands}


# ---------------------------------------------------------------- 條目人物（原型 extract.py／dom.py／clean.py）

VOID = {"br", "img", "hr", "meta", "link", "input", "wbr", "col"}


class N:
    __slots__ = ("tag", "attrs", "kids", "parent")

    def __init__(s, tag, attrs=None, parent=None):
        s.tag, s.attrs, s.kids, s.parent = tag, dict(attrs or []), [], parent

    def cls(s):
        return s.attrs.get("class", "").split()

    def text(s):
        if s.tag == "#t":
            return s.attrs["t"]
        if s.tag in ("sup", "style", "script"):
            return ""
        if "reference" in s.cls() or "mw-editsection" in s.cls() or ("noprint" in s.cls() and s.tag != "td"):
            return ""
        if s.tag == "br":
            return "\n"
        return "".join(k.text() for k in s.kids)

    def walk(s):
        yield s
        for k in s.kids:
            if k.tag != "#t":
                yield from k.walk()


class _P(HTMLParser):
    def __init__(s):
        super().__init__(convert_charrefs=True)
        s.root = s.cur = N("root")

    def handle_starttag(s, tag, attrs):
        n = N(tag, attrs, s.cur)
        s.cur.kids.append(n)
        if tag not in VOID:
            s.cur = n

    def handle_startendtag(s, tag, attrs):
        s.cur.kids.append(N(tag, attrs, s.cur))

    def handle_endtag(s, tag):
        c = s.cur
        while c is not None and c.tag != tag:
            c = c.parent
        if c is not None and c.parent is not None:
            s.cur = c.parent

    def handle_data(s, d):
        s.cur.kids.append(N("#t", {"t": d}, s.cur))


def dom(html):
    p = _P()
    p.feed(html)
    return p.root


CHAR_RE = re.compile(r"(登場|登场)?(人物|角色)|主角|配角|角色介紹|人物介紹|角色簡介|主要登場|登場者|^演員|角色設定")
CHAR_NEG = re.compile(r"列表$|一覽$|相關|周邊|商品|遊戲|玩具|聲優|配音|演員|製作|聲音|影響|評價|設計|創作|名詞|專有|用語")
LIST_RE = re.compile(r"(角色|人物)(列表|一覽|一览)$|登場(人物|角色)(列表|一覽|一览)$")   # 契約：連到的角色列表條目（不含機體、用語列表）
HANX = re.compile(r"^[㐀-䶿一-鿿]+$")
SEP = re.compile(r"[・·•･ 　/／]")
CUT = re.compile(r"[（(：:、,，；;〔\[「『【《—–\-~～…→＝=|｜\n]|\s聲|CV|配音|演員|飾演|聲優")
STOP = set("日本 角色 其他 旁白 香港 台灣 中國大陸 國語 醫生 主角 老師 首領 怪物 播音員 記者 警官 校長 警備員 長老 老婆婆 角色名 角色名稱 登場人物 登場角色 客串角色 次要角色 主要人物 主要角色 原作角色 本作角色 其他人物 其他角色 其他登場人物 必殺技 合體必殺技 主角的家人 傳說神奇寶貝 台灣 中國 美國 韓國 英語 粵語 日語".split())
STOP_RE = re.compile(r"(角色|人物|登場|列表|一覽|必殺技|聲優|配音|名稱|演員)$|^(主要|次要|其他|原創|登場)")
KANA = re.compile(r"[぀-ヿ]")
REL = re.compile(r"的|之(母|父|女|子|妻|夫|弟|兄|姊|姐|妹|友|師|徒|主|王|僕)")
ROLE_END = re.compile(r"(母親|父親|哥哥|姊姊|姐姐|弟弟|妹妹|爺爺|奶奶|老師|同學|學生|校長|社長|部長|隊長|會長|隊員|成員|一夥|一伙|學園|學院|小隊|軍團|集團|社團|俱樂部|協會|公司|組織|王國|帝國|村落|號|家|們)$")
ROLE_ANY = re.compile(r"社團|俱樂部|同好會|研究會|委員會|學生會|粉絲|觀眾|路人|店員|店長|護士|警察|刑警|警官|醫生|老闆|客人|居民|村民|士兵|聲優|配音|旁白|廣播")


def raw_candidates(root):
    """條目裡登場人物段落下的 (種類, 文字)。"""
    stack = []
    for n in root.walk():
        if n.tag in ("h2", "h3", "h4", "h5"):
            lv = int(n.tag[1])
            while stack and stack[-1][0] >= lv:
                stack.pop()
            stack.append((lv, n.text().strip(), n))
        if not any(CHAR_RE.search(t) and not CHAR_NEG.search(t) for _, t, _ in stack):
            continue
        if n.tag == "dt":
            yield "dt", "".join(k.text() for k in n.kids if k.tag not in ("ul", "ol", "dl", "table", "div"))
        elif n.tag == "li":
            if "toclevel-1" not in str(n.attrs):
                yield "li", "".join(k.text() for k in n.kids if k.tag not in ("ul", "ol", "dl", "table", "div"))
        elif n.tag == "tr":
            cells = [k for k in n.kids if k.tag in ("th", "td")]
            if len(cells) >= 2:
                for c in cells:
                    t = c.text().strip()
                    if t:
                        yield "tr", t
                        break
        elif n.tag == "p":
            b = [k for k in n.kids if k.tag != "#t"][:1]
            if b and b[0].tag in ("b", "strong"):
                yield "p-bold", b[0].text()
        elif n.tag in ("h3", "h4", "h5") and stack and stack[-1][2] is n:
            yield "heading", n.text()


def names_of(html):
    """條目 HTML → [(名字, 種類, 原文前 120 字)]，只有單一名字（含 ・／空白的複合寫法不收）。"""
    out = []
    for kind, raw in raw_candidates(dom(html)):
        s = re.sub(r"\[[^\]]*\]", "", raw).strip()
        m = CUT.search(s)
        head = (s[:m.start()] if m else s).strip()
        if head and not SEP.search(head) and HANX.match(head) and 2 <= len(head) <= 10 and head not in STOP and not STOP_RE.search(head):
            out.append((head, kind, s[:120]))
    return out


# 契約 A2.2：括號裡的原名是假名，或不在這份清單裡的拉丁字母詞；聲優、播出形式這類標註（「（CV：…）」「（OVA）」）不是原名。
# 括號裡第一個聲優標記之後的文字不算證據（「（勅使河原 鏡花，聲：長月アキ）」只有「勅使河原 鏡花」算）；ver、XY、PT、IT、TVB 這類標籤與縮寫不算原名（第N話 沒有拉丁字母詞，本來就不算）。
NOT_ORIGINAL = {"CV", "OVA", "OAD", "ONA", "TV", "TVA", "SP", "PV", "MV", "DVD", "BD", "CD", "ED", "OP", "OST", "NHK", "TBS", "MBS", "TVB", "IT", "XY", "PT", "VER"}
LATIN = re.compile(r"[A-Za-z]{2,}")
CREDIT_AT = re.compile(r"(?<![A-Za-z])CV(?![A-Za-z])|聲優|声优|配音|日本配音|演員|飾演|[聲声]\s*[:：]|由[^，,；;）)（(]*(?:配音|飾演|演出)")
ORIGINAL = re.compile(r"^\s*[（(]([^）)]*)[）)]")


def has_original(content):
    """括號裡（第一個聲優標記之前）有假名，或有不在 NOT_ORIGINAL 的拉丁字母詞。"""
    m = CREDIT_AT.search(content)
    head = content[:m.start()] if m else content
    return bool(KANA.search(head) or any(t.upper() not in NOT_ORIGINAL for t in LATIN.findall(head)))


def strict_ok(name, kind, snip, base):
    """嚴格過濾（契約 A.1）：不在基底、不是小標題、不是關係詞組、不是泛稱，而且名字緊接的括號裡有原名（契約 A2.2，`has_original`）。"""
    if not HAN.match(name) or name in base or kind == "heading" or REL.search(name) or ROLE_END.search(name) or ROLE_ANY.search(name):
        return False
    sn = snip.replace("\n", " ").strip()
    m = ORIGINAL.match(sn[len(name):]) if sn.startswith(name) else None
    return bool(m and has_original(m.group(1)))


# ---------------------------------------------------------------- 去重、讀音、分數、排序

@functools.lru_cache(maxsize=1)
def base_lexicon():
    return ime.Lexicon(bo.BASE)


@functools.lru_cache(maxsize=1)
def lexicon_words():
    """基底、overlay-add.tsv、sandhi-add.tsv 的詞（任何讀音下）。"""
    have = set(base_lexicon().by_word)
    for f in ("overlay-add.tsv", "sandhi-add.tsv"):
        have |= {l.split("\t")[1] for l in open(os.path.join(LEX, f), encoding="utf-8")}
    return frozenset(have)


def dedupe(words, have):
    """任何來源的詞只要字串已在基底、overlay-add.tsv 或 sandhi-add.tsv（任何讀音下）就丟掉。"""
    return sorted(set(words) - have)


def scores(base):
    """長度 → 基底同字數詞條分數的第 25 百分位；某長度的基底詞條少於 1,000 條就沿用上一個長度（5 字以上才會發生）。"""
    sc = {}
    for n in range(2, 11):
        got = sorted(s for syls, ents in base.by_reading.items() if len(syls) == n for _, s in ents)
        sc[n] = got[len(got) // 4] if len(got) >= 1000 else sc[n - 1]
        if n in bo.SCORE:
            assert sc[n] == bo.SCORE[n], (n, sc[n])
    return sc


def make_readings(words):
    """tools/readings.py：詞 → (讀音, 是否 CHECK)；UNREADABLE 的不在結果裡。"""
    with tempfile.TemporaryDirectory() as d:
        src, dst, err = (os.path.join(d, x) for x in ("in.txt", "out.txt", "err.txt"))
        with open(src, "w", encoding="utf-8") as f:
            f.writelines(f"|{w}\n" for w in words)
        with open(err, "w", encoding="utf-8") as e:
            subprocess.run([sys.executable, "-B", os.path.join(ROOT, "tools", "readings.py"), src, dst], stderr=e, check=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        check, cur = set(), None
        for l in open(err, encoding="utf-8"):
            if l.startswith("CHECK\t"):
                check.add(l.rstrip("\n").split("\t")[1].lstrip("|"))
        out = {}
        for l in open(dst, encoding="utf-8"):
            _, w, rd = l.rstrip("\n").split("|")
            assert len(rd.split()) == len(w), (w, rd)
            out[w] = (rd.split(), w in check)
        return out


def collision_rank(rows):
    """處置列的名次：{(讀音, 詞): 名次}，依列出現的先後、每一列保留的詞在前。`ordered`（排序）與 `first_named`（開詞包後的第一名）共用這一份。"""
    rank = {}
    for r in rows:
        for w in (r[1], r[2].lstrip("+")):
            rank.setdefault((r[0], w), len(rank))
    return rank


def first_named(rank, inpack):
    """每個有處置列的讀音，名次最前、而且還在詞包的詞：`ordered` 排在最前面的那個，也是開詞包後必須是第一名的詞。"""
    out = {}
    for (r, w), _ in sorted(rank.items(), key=lambda kv: kv[1]):
        if w in inpack:
            out.setdefault(r, w)
    return out


def ordered(words, nsrc, rank=None, reading=None, extra=None):
    """同音詞的順序，由 `pack_rows` 轉成分數的差（解碼器同分時的先後與檔案順序無關，2026-10-10 實測）：處置列點名的先照 `collision_rank`
    的名次（詞有好幾個讀音時取最前面的），其餘來源數多的在前，同數依字串排序，所以可以重現。"""
    rank = rank or {}
    extra = extra or {}
    def rk(w):
        if not reading:
            return 0
        rs = [reading[w]] + list(extra.get(w, []))
        return min((rank.get((" ".join(r), w), len(rank)) for r in rs))
    return sorted(words, key=lambda w: (rk(w), -nsrc(w), w))


TIE = 1e-6      # 同讀音的詞包詞，排在第 k 位的分數減 k × TIE：解碼器遇到同分時的先後與檔案順序無關（2026-10-10 實測），要讓「來源數多的在前」成立只能靠分數


def pack_rows(order, reading, sc, extra=None):
    """每個詞的列，格式和 build_overlay 共用同一個函式；同讀音的列依 order 的先後各減 k × TIE，讓同分的詞有固定的第一名。
    extra：{詞: [另外的讀音]}（手動詞第四欄），這些讀音也各有一列（含變調列），和主要讀音同分數。"""
    rows, seen = [], collections.Counter()
    extra = extra or {}
    for w in order:
        for syls in [reading[w]] + list(extra.get(w, [])):
            for r in bo.overlay_rows(w, syls, sc[len(w)], TAG):
                key, word, score, tag = r.rstrip("\n").split("\t")
                k = seen[key]
                seen[key] += 1
                rows.append(r if not k else f"{key}\t{word}\t{round(float(score) - k * TIE, 8)!r}\t{tag}\n")
    return rows


# ---------------------------------------------------------------- 同音衝突（契約 A.1）

@functools.lru_cache(maxsize=1)
def eval_bin():
    """每次建置前先 cargo build（已是最新就很快），不用過期的執行檔；SHANJIE_EVAL 指定的照用。"""
    if os.environ.get("SHANJIE_EVAL"):
        return os.environ["SHANJIE_EVAL"]
    subprocess.run(["cargo", "build", "--release", "--locked", "-q", "-p", "cli"], cwd=ROOT, check=True)
    return os.path.join(ROOT, "target", "release", "shanjie-eval")


def top1(pairs, profile, packs_dir=None):
    """評測 CLI 解碼每個 (詞, 讀音音節串)，回傳第一名。packs_dir 給了就開詞包（`--packs acg`）。"""
    with tempfile.TemporaryDirectory() as d:
        rows, dump = os.path.join(d, "rows.txt"), os.path.join(d, "dump.txt")
        with open(rows, "w", encoding="utf-8") as f:
            f.writelines(f"|{w}|{' '.join(syls)}\n" for w, syls in pairs)
        cmd = [eval_bin(), "--lm", LM, "--profile", profile, "--rows", rows, "--dump", dump]
        if packs_dir:
            cmd += ["--packs", "acg", "--packs-dir", packs_dir]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
        first = {}
        for l in open(dump, encoding="utf-8"):
            r, k, s, _ = l.rstrip("\n").split("\t")
            if k == "1":
                first[int(r)] = s
        assert len(first) == len(pairs), "the evaluation CLI skipped rows"
        return [first[i + 1] for i in range(len(pairs))]


LEXICON_RULE_ROUNDS = 5      # 類型 c 規則重跑的上限；丟一個詞只會讓少數鄰居改變，實測幾輪就穩


def detect_collisions(words, reading, rows, ref, decode=top1, existing=None, extra=None):
    """對每個詞包詞 w 的讀音（含「一」「不」變調列的讀音），各解碼兩次（不開／開詞包；聊天與書面）。符合其一就列出：
    (a) 開了之後第一名是另一個詞包詞；(b) 不開時第一名不是 w，而且在參考名單 ref 裡；
    (c) 不開時第一名 o 不是 w、是既有詞庫的詞（existing，預設 lexicon_words()），開了之後第一名變成 w（w 把既有詞擠下第一名）。
    都只在開了之後第一名有改變時才算：
    第一名沒變的，詞包的寫法只多一個候選，不改變打出來的結果（使用者 2026-10-09 決定這類留做候選、不必人工處置）。
    回傳 {讀音: [(w, 條件, 設定, 不開第一名, 開第一名)]}。"""
    existing = lexicon_words() if existing is None else existing
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "acg-add.tsv"), "w", encoding="utf-8") as f:
            f.writelines(rows)
        inpack, found = set(words), collections.defaultdict(list)
        pairs, extra = [], extra or {}
        for w in words:
            for syls in [reading[w]] + list(extra.get(w, [])):
                pairs.append((w, syls))
                var = bo.sandhi_variant(w, syls)
                if var:
                    pairs.append((w, var))
        for prof in ("chat", "formal"):
            off, on = decode(pairs, prof), decode(pairs, prof, d)
            for (w, syls), o, n in zip(pairs, off, on):
                if n == o:
                    continue
                for cond, hit in (("a", n != w and n in inpack), ("b", o != w and o in ref), ("c", n == w and o != w and o in existing)):
                    if hit:
                        found[" ".join(syls)].append((w, cond, prof, o, n))
    return dict(found)


def read_collisions(path):
    """acg-collisions.tsv：讀音、保留的詞、排除的詞、理由。排除欄寫 `+詞` 表示兩個都留，`+` 後面是另一個留下的詞
    （使用者決定的處置）。回傳 ({排除的詞: 讀音}, {(讀音, 詞)})：處置列裡點名的兩個詞才算已處置，同讀音的新詞不算。
    同一個讀音的排序與開詞包後的第一名見 `collision_rank`、`first_named`（列出現的先後，每列保留的詞在前；名次最前、還在詞包的點名的詞必須是第一名）。"""
    if not os.path.exists(path):
        return {}, set()
    out, decided = {}, set()
    for r in read_tsv(path):
        if not (len(r) == 4 and r[2].lstrip("+")):
            raise SystemExit(f"{path}: bad collision row: {r}")
        other = r[2].lstrip("+")
        if r[2].startswith("+") and other == r[1]:
            raise SystemExit(f"{path}: a row keeps {r[1]!r} and also names it as the other word: {r}")
        decided |= {(r[0], r[1]), (r[0], other)}
        if not r[2].startswith("+"):
            out[other] = r[0]
    return out, decided


def read_exclude(path):
    """acg-exclude.tsv：詞、理由。抽取時誤當成名字的字串（句子片段、轉換錯誤），不收進詞包，也不算參考名單。"""
    if not os.path.exists(path):
        return set()
    out = set()
    for r in read_tsv(path):
        if not (len(r) == 2 and HANX.match(r[0]) and r[1]):
            raise SystemExit(f"{path}: bad exclude row: {r}")
        out.add(r[0])
    return out


# ---------------------------------------------------------------- 主流程

def build(api, groups_tsv=os.path.join(PACKS, "acg-groups.tsv"), collisions_tsv=os.path.join(PACKS, "acg-collisions.tsv"), manual_tsv=os.path.join(PACKS, "acg-manual.tsv"), decode=top1, readings=make_readings, exclude_tsv=os.path.join(PACKS, "acg-exclude.tsv"), years=range(LAST_YEAR - YEARS + 1, LAST_YEAR + 1), min_year_works=MIN_YEAR_WORKS):
    log = lambda *a: print(*a, file=sys.stderr)
    gr = read_groups(groups_tsv)
    listing = pages(api, ["Template:CGroup/list"])["Template:CGroup/list"]
    items = list_items(listing["text"])
    known = {(r[1], r[2]) for r in gr}
    unclassified = sorted(f"{i[1]} ({i[2]})" for i in items if (i[1], i[2]) not in known)
    log("groups", len(gr), "unclassified", len(unclassified))
    resolved = resolve_groups(api, gr, items)
    ts, revs = [listing["ts"]], [listing["revid"]]
    ref, cg_src, cg_hant, unresolved = set(), collections.defaultdict(set), set(), []
    for r in gr:
        p = resolved[(r[1], r[2])]
        if not p:
            unresolved.append(r[1])
            continue
        ts.append(p["ts"])
        revs.append(p["revid"])
        for rule in group_rules(p["text"], p["title"].startswith("Module:")):
            for v, src in tw_values(rule):
                ref.add(v)
                if r[3] == "include":
                    cg_src[v].add(f"{p['title']}@{p['revid']}")
                    if src == "zh-hant":
                        cg_hant.add(v)
    log("group pages missing:", unresolved)

    # 作品與條目
    b = api.sparql(SPARQL)["results"]["bindings"]
    ws = sorted({x["w"]["value"].rsplit("/", 1)[1]: (x["w"]["value"].rsplit("/", 1)[1], wiki_title(x["art"]["value"]), int(x["sl"]["value"])) for x in b}.values(), key=lambda t: (-t[2], t[0]))[:WORKS]
    arts, art_rev, seen, queued = [], {}, set(), set()
    title_src, name_src, all_names = {}, collections.defaultdict(set), set()
    base, have = base_lexicon(), lexicon_words()
    ylists = pages(api, year_titles(years)) if years else {}      # 契約 A2.1：年度動畫清單，只取作品名欄的連結
    yearly, ywork = {}, []
    for y, t in zip(years, year_titles(years)):
        if not ylists[t]:
            raise SystemExit(f"year list not found: {t}")
        ts.append(ylists[t]["ts"])
        revs.append(ylists[t]["revid"])
        yw = year_works(ylists[t]["text"], min_year_works)
        ywork += yw
        yearly[str(y)] = {"title": ylists[t]["title"], "revid": ylists[t]["revid"], "ts": ylists[t]["ts"], "links": len(yw)}
        log("year", y, "works linked", len(yw))
    ywork = list(dict.fromkeys(ywork))
    real = resolve_titles(api, ywork) if ywork else {}
    missing = [t for t in ywork if real[t] is None]                  # 紅連結：沒有這個條目，不去抓
    log("year lists: links", len(ywork), "no article", len(missing))
    todo = [(w[1], True) for w in ws] + [(real[t], True) for t in ywork if real[t]]      # (條目, 是不是作品)；作品條目連到的角色列表接在後面；與前 400 部重複的，下面依標題去重
    while todo:
        t, is_work = todo.pop(0)
        try:
            d = api.wiki(action="parse", page=t, variant="zh-tw", redirects="1", prop="text|revid|displaytitle|links|sections")["parse"]
        except RuntimeError as e:
            if e.args[0].get("code") != "missingtitle":      # Wikidata 的 sitelink 指向已刪除或移走的條目：略過，記在報告裡
                raise
            log("missing article:", t)
            missing.append(t)
            continue
        if d["title"] in seen:
            continue
        seen.add(d["title"])
        arts.append(d["title"])
        art_rev[d["title"]] = d["revid"]
        if is_work:
            disp = re.sub(r"\s*[\(（][^)）]*[\)）]\s*$", "", re.sub("<[^>]+>", "", d["displaytitle"]).strip())
            if HAN.match(disp):
                title_src.setdefault(disp, set()).add(d["title"])
            for l in d["links"]:
                if l.get("ns") == 0 and l.get("exists") and LIST_RE.search(l["title"]) and l["title"] not in queued:
                    queued.add(l["title"])
                    todo.append((l["title"], False))
        for n, kind, snip in names_of(d["text"]):             # 契約 A2.2：每一次出現都判斷，有一次通過就收；出處只記通過的條目
            all_names.add(n)
            if strict_ok(n, kind, snip, have):
                name_src[n].add(d["title"])
    revs += art_rev.values()
    log("works", len(ws), "articles", len(arts), "titles", len(title_src), "names", len(all_names), "passing", len(name_src))

    chars = set(name_src)
    ref |= set(title_src) | all_names               # 契約：參考名單含所有抽出來的人名，在嚴格過濾與去重之前
    src = collections.defaultdict(lambda: collections.defaultdict(set))   # 詞 → 種類 → 出處
    for v, s in cg_src.items():
        src[v]["cgroup"] |= s
    for v, s in title_src.items():
        src[v]["title"] |= {f"{t}@{art_rev[t]}" for t in s}
    for n in chars:
        src[n]["char"] |= {f"{t}@{art_rev[t]}" for t in name_src[n]}
    manual_extra = {}
    for w, work, *rest in read_tsv(manual_tsv):         # 維護者手動加的詞（欄位：詞、作品、備註、另外的讀音（選填））：同樣去重、定讀音、算分數、偵測衝突
        if not HAN.match(w):
            raise SystemExit(f"{manual_tsv}: bad manual word: {w!r}")
        if len(rest) > 1 and rest[1].strip():           # 第四欄：另外的讀音（音節以空白分隔，要和字數一致）。詞同時有讀音工具選的讀音和這一個
            if len(rest[1].split()) != len(w):
                raise SystemExit(f"{manual_tsv}: reading of {w!r} has {len(rest[1].split())} syllables")
            manual_extra[w] = rest[1].split()
        src[w]["manual"].add(work)
        ref.add(w)
    exclude = read_exclude(exclude_tsv)
    ref -= exclude
    excluded, decided = read_collisions(collisions_tsv)
    rank = collision_rank(read_tsv(collisions_tsv) if os.path.exists(collisions_tsv) else [])      # 排序與開詞包後的第一名共用同一份名次
    deduped = set(dedupe(src, have))
    dropped = exclude & deduped                          # 實際從候選拿掉的才算
    cand = [w for w in sorted(deduped) if w not in excluded and w not in dropped]
    log("candidates", len(src), "after dedupe and exclusions", len(cand), "excluded by acg-collisions.tsv", len(set(excluded) & deduped), "by acg-exclude.tsv", len(dropped))

    rd = readings(cand)
    unread = sorted(set(cand) - set(rd))
    words = [w for w in cand if w in rd]
    reading = {w: rd[w][0] for w in words}
    extra = {w: [r] for w, r in manual_extra.items() if w in reading and r != reading[w]}      # 手動詞多出來的讀音
    sc = scores(base)
    # 使用者 2026-10-10 的規則：詞包詞若把既有詞庫的詞擠下第一名（類型 c），既有詞勝、詞包詞丟掉；丟掉之後別的詞的結果可能變，重跑到沒有 c 為止。
    lexicon_dropped = []
    for _ in range(LEXICON_RULE_ROUNDS):
        order = ordered(words, lambda w: sum(len(s) for s in src[w].values()), rank, reading, extra)
        rows = pack_rows(order, reading, sc, extra)
        found = detect_collisions(words, reading, rows, ref, decode, extra=extra)
        hit = {e[0]: (r, e) for r, v in sorted(found.items()) for e in v if e[1] == "c"}
        if not hit:
            break
        lexicon_dropped += [(r, w, o, prof) for w, (r, (_, _, prof, o, _)) in sorted(hit.items())]
        words = [w for w in words if w not in hit]
    else:
        raise SystemExit(f"type (c) collisions not settled after {LEXICON_RULE_ROUNDS} rounds")
    col = {}
    inpack = set(words)
    keep_now = first_named(rank, inpack)       # 每個讀音開詞包後必須是第一名的詞：名次最前、還在詞包的那個（和排序同一個定義）
    for r, v in found.items():
        # 詞 e[0] 在這個讀音的處置列裡被點名，而且開了之後的第一名 e[4] 就是處置列的保留的詞，才算已處置
        # （2026-10-10：被舊處置蓋住的新詞搶走第一名，或點名的詞沒排在保留的詞前面，都要列出）
        left = [e for e in v if (r, e[0]) not in decided or e[4] != keep_now.get(r)]
        if left:
            col[r] = left
    ts_max = max(ts)
    manifest = {
        "version": ts_max[:10].replace("-", "") + "-" + hashlib.sha256("".join(rows).encode()).hexdigest()[:8],   # 最新的有時間戳的來源頁日期＋詞包內容雜湊：內容變了版號一定變
        "latest_source_revision": ts_max,
        "words": len(words),
        "rows": len(rows),
        "sources": {k: sum(1 for w in words if k in src[w]) for k in ("cgroup", "title", "char", "manual")},
        "yearly_lists": yearly,
        "revision_ids": {"min": min(revs), "max": max(revs), "pages": len(set(revs))},
        "groups": {"listed": len(gr), "included": sum(1 for r in gr if r[3] == "include"), "page_missing": unresolved},
        "unclassified_groups": unclassified,
        "articles_missing": sorted(missing),
        "unreadable_dropped": len(unread),
        "excluded_by_collisions_tsv": len(set(excluded) & deduped),
        "dropped_by_lexicon_rule": len(lexicon_dropped),
        "excluded_by_exclude_tsv": len(dropped),
        "unresolved_collision_readings": len(col),
    }
    sources = ["word\tsource_kind\tsources (page@revision)\tnote\n"]
    for w in order:
        for k in ("cgroup", "title", "char", "manual"):
            if k in src[w]:
                note = ",".join(x for x, on in (("CHECK", rd[w][1]), ("zh-hant", k == "cgroup" and w in cg_hant)) if on)
                sources.append(f"{w}\t{k}\t{'; '.join(sorted(src[w][k]))}\t{note}\n")
    return {"acg-add.tsv": "".join(rows), "acg-sources.tsv": "".join(sources)}, manifest, col, unread, ref, lexicon_dropped


def collisions_text(col, manifest, ldrop=()):
    out = ["# 未處置的同音衝突（契約 A.1）。每列：讀音、詞、條件（a 詞包內同音／b 搶走參考名單的名字）、設定、不開詞包的第一名、開了之後的第一名。\n",
           "# 處置寫進 data/packs/acg-collisions.tsv（讀音、保留的詞、排除的詞、理由）。\n"]
    for r in sorted(col):
        out += [f"{r}\t{w}\t{c}\t{p}\t{o}\t{n}\n" for w, c, p, o, n in sorted(col[r])]
    out.append("# 依使用者 2026-10-10 的規則丟掉的詞包詞（類型 c：會把既有詞庫的詞擠下第一名）。每列：讀音、丟掉的詞、既有詞庫的詞、設定。\n")
    out += [f"{r}\t{w}\t{o}\t{prof}\n" for r, w, o, prof in sorted(ldrop)]
    out.append("# 未分類的轉換組（不收）：" + "、".join(manifest["unclassified_groups"]) + "\n")
    return "".join(out)


def write_all(files, manifest, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    manifest = dict(manifest, files={n: {"sha256": hashlib.sha256(t.encode()).hexdigest(), "bytes": len(t.encode()), "lines": t.count("\n")} for n, t in sorted(files.items())})
    files = dict(files, **{"acg.json": json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True) + "\n"})
    for n, t in files.items():
        with open(os.path.join(out_dir, n) + ".tmp", "w", encoding="utf-8", newline="\n") as f:
            f.write(t)
        os.replace(os.path.join(out_dir, n) + ".tmp", os.path.join(out_dir, n))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=CACHE)
    ap.add_argument("--out", default=PACKS)
    ap.add_argument("--report", default=os.path.join(ROOT, "build", "acg-pack"))
    ap.add_argument("--offline", action="store_true")
    a = ap.parse_args()
    files, manifest, col, unread, ref, ldrop = build(Api(a.cache, a.offline))
    write_all(files, manifest, a.out)
    os.makedirs(a.report, exist_ok=True)
    with open(os.path.join(a.report, "collisions.txt"), "w", encoding="utf-8") as f:
        f.write(collisions_text(col, manifest, ldrop))
    for name, lines in (("unreadable.txt", unread), ("reference.txt", sorted(ref))):
        with open(os.path.join(a.report, name), "w", encoding="utf-8") as f:
            f.write("".join(w + "\n" for w in lines))
    print(json.dumps({k: v for k, v in manifest.items() if k != "unclassified_groups"}, ensure_ascii=False, sort_keys=True))
    print(f"unresolved collision readings: {len(col)} (see {os.path.join(a.report, 'collisions.txt')})")


if __name__ == "__main__":
    main()
