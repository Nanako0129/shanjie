"""IT+AI 詞包的來源層（契約 docs/contracts/it-pack.md §1、§2、§2a）：抓取、快取、候選字串的切法。
詞頻計數工具（tools/it_wiki_counts.py）與之後的建置工具共用這一份。

用法：python3 tools/it_sources.py fetch --cache DIR                       連網抓取（由 main 做一次）
      python3 tools/it_sources.py candidates --cache DIR --offline --out FILE   候選字串，排序去重，一行一個

只連契約 §2a 的主機（data.gov.tw、opendata.naer.edu.tw、zh.wikipedia.org/w/api.php、query.wikidata.org），依序送出、
每秒 ≤ 1 個請求、帶 User-Agent、維基加 maxlag=5；5xx 與 maxlag 退避重試。每個請求一個快取檔（檔名是請求網址的 SHA-256），
manifest.json 記每個下載的網址、大小、SHA-256、抓取日期（給 it.json）。--offline 只讀快取，缺檔就中止。
"""
import argparse
import csv
import datetime
import hashlib
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

CACHE = os.path.expanduser("~/.cache/shanjie/sources/it")
UA = "shanjie-it-pack/1.0 (https://github.com/Nanako0129/shanjie; build tool for an optional word pack; contact via the repository)"
HOSTS = {"data.gov.tw", "opendata.naer.edu.tw", "zh.wikipedia.org", "query.wikidata.org"}
WIKI = "https://zh.wikipedia.org/w/api.php"
DATASETS = {"15416": "https://data.gov.tw/api/v2/rest/dataset/15416", "15275": "https://data.gov.tw/api/v2/rest/dataset/15275"}
# 契約 §1：8 個根分類，往下兩層（根、子、孫），每個分類的 ns0 成員都算。
AI_ROOTS = ["人工智能", "机器学习", "大型语言模型", "自然語言處理", "深度学习", "人工神经网络", "生成式人工智能", "计算机视觉"]
AI_DEPTH = 2
MODULES = ["Module:CGroup/IT", "Module:CGroup/Electronics"]
HAN = re.compile(r"[一-鿿]{2,10}")
PAREN = re.compile(r"（[^（）]*）|\([^()]*\)")


# ---------------------------------------------------------------- 候選字串（契約 §2：計數工具與建置工具共用）

def split_names(cell):
    """15416 一列的「中文名稱」→ 2–10 個漢字的字串。先去掉全形與半形括號裡的部分（括號裡可以有「、」），再依「；」「，」「、」切開。"""
    while PAREN.search(cell):
        cell = PAREN.sub("", cell)
    return [p for p in (x.strip() for x in re.split(r"[；，、]", cell)) if HAN.fullmatch(p)]


def ai_title(title):
    """維基 AI 分類條目的 zh-tw 標題 → [標題]（2–10 個漢字）或 []。"""
    t = title.strip()
    return [t] if HAN.fullmatch(t) else []


# ---------------------------------------------------------------- 抓取與快取

class Fetcher:
    """get(url) → bytes。快取命中就用；--offline 缺檔中止；否則限速、重試、寫快取與 manifest。opener 給測試換掉網路。"""

    def __init__(self, cache=CACHE, offline=False, opener=None, sleep=time.sleep):
        self.cache, self.offline, self.sleep, self.last = cache, offline, sleep, 0.0
        self.opener = opener or self._urlopen
        self.mpath = os.path.join(cache, "manifest.json")
        os.makedirs(cache, exist_ok=True)
        self.manifest = json.load(open(self.mpath, encoding="utf-8")) if os.path.exists(self.mpath) else {}

    @staticmethod
    def _urlopen(url):
        return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=300).read()

    def get(self, url):
        key = hashlib.sha256(url.encode()).hexdigest()
        path = os.path.join(self.cache, key + ".bin")
        if os.path.exists(path):
            return open(path, "rb").read()
        if self.offline:
            raise SystemExit(f"--offline: not cached: {url[:160]}")
        if urllib.parse.urlsplit(url).hostname not in HOSTS:
            raise SystemExit(f"host not allowed by contract 2a: {url[:160]}")
        for attempt in range(6):
            wait = 1.1 - (time.time() - self.last)
            if wait > 0:
                self.sleep(wait)
            self.last = time.time()
            try:
                data = self.opener(url)
            except urllib.error.HTTPError as e:
                if e.code < 500 and e.code != 429:
                    raise
                self.sleep(5 * (attempt + 1))
                continue
            except urllib.error.URLError:
                self.sleep(5 * (attempt + 1))
                continue
            if b'"code":"maxlag"' in data[:300].replace(b" ", b""):
                self.sleep(5 * (attempt + 1))
                continue
            with open(path + ".part", "wb") as f:
                f.write(data)
            os.replace(path + ".part", path)
            self.manifest[key] = {"url": url, "file": key + ".bin", "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                                  "fetched": datetime.date.today().isoformat()}
            with open(self.mpath + ".part", "w", encoding="utf-8") as f:
                json.dump(self.manifest, f, ensure_ascii=False, indent=1, sort_keys=True)
            os.replace(self.mpath + ".part", self.mpath)
            return data
        raise RuntimeError(f"gave up after retries: {url[:160]}")

    def wiki(self, **p):
        p.update(format="json", formatversion="2", maxlag="5")
        return json.loads(self.get(WIKI + "?" + urllib.parse.urlencode(sorted(p.items()))))

    def sparql(self, query):
        return json.loads(self.get("https://query.wikidata.org/sparql?" + urllib.parse.urlencode({"query": query, "format": "json"})))


def csv_rows(f, dataset, urls=None):
    """政府資料開放平臺的資料集 → CSV 的列（dict）。下載網址取自 data.gov.tw 的資料集 API（resourceFormat 是 CSV 的那筆）；urls={id: 網址} 可直接指定。"""
    url = (urls or {}).get(dataset)
    if not url:
        meta = json.loads(f.get(DATASETS[dataset]))
        dist = meta["result"]["distribution"]
        url = next(d.get("resourceDownloadUrl") or d.get("downloadURL") for d in dist if "csv" in str(d.get("resourceFormat", "")).lower())
    raw = f.get(url)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("big5hkscs")
    return list(csv.DictReader(io.StringIO(text)))


def category_titles(f):
    """8 個根分類往下兩層的 ns0 成員標題（原樣，不轉換），排序去重。"""
    seen, level, titles = set(), ["Category:" + r for r in AI_ROOTS], []
    for _ in range(AI_DEPTH + 1):
        nxt = []
        for cat in level:
            if cat in seen:
                continue
            seen.add(cat)
            for kind, ns, out in (("page", "0", titles), ("subcat", "14", nxt)):
                cont = {}
                while True:
                    r = f.wiki(action="query", list="categorymembers", cmtitle=cat, cmtype=kind, cmnamespace=ns, cmlimit="max", **cont)
                    out.extend(m["title"] for m in r["query"]["categorymembers"])
                    if "continue" not in r:
                        break
                    cont = r["continue"]
        level = nxt
    return sorted(set(titles))


def convert_lines(f, lines, variant):
    """一批字串用維基的 action=parse 轉成 variant（zh-tw 或 zh-hant），每 50 個一個請求，回傳同順序的列表。"""
    out = []
    for i in range(0, len(lines), 50):
        b = lines[i:i + 50]
        r = f.wiki(action="parse", text="\n\n".join(b), contentmodel="wikitext", prop="text", disablelimitreport="1", variant=variant)
        got = [re.sub(r"<[^>]+>", "", p).strip() for p in re.findall(r"<p>(.*?)</p>", r["parse"]["text"], re.S)]
        if len(got) != len(b):
            raise RuntimeError(f"parse conversion returned {len(got)} lines for {len(b)}")
        out += got
    return out


def candidates(f, urls=None):
    names = {c for row in csv_rows(f, "15416", urls) for c in split_names(row["中文名稱"])}
    titles = {c for t in convert_lines(f, category_titles(f), "zh-tw") for c in ai_title(t)}
    return sorted(names | titles)


def fetch_all(f, urls=None):
    """§2a 的全部請求（候選字串、15275 與它的大陸譯名轉 zh-hant、兩個 CGroup 模組原始碼）。Wikidata 的 P31 查詢要等過濾後的名單，由建置工具的第一次（線上）執行送出。"""
    n = len(candidates(f, urls))
    mainland = sorted({v.strip() for row in csv_rows(f, "15275", urls) for v in re.split(r"[；，、]", row.get("中國大陸譯名", "")) if v.strip()})
    convert_lines(f, mainland, "zh-hant")
    for m in MODULES:
        f.wiki(action="query", prop="revisions", rvprop="content", rvslots="main", titles=m)
    return n


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["fetch", "candidates"])
    ap.add_argument("--cache", default=CACHE)
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--csv-url", action="append", default=[], metavar="ID=URL", help="直接指定 CSV 下載網址（15416、15275）")
    a = ap.parse_args()
    f = Fetcher(a.cache, a.offline)
    urls = dict(x.split("=", 1) for x in a.csv_url)
    if a.cmd == "fetch":
        print(f"{fetch_all(f, urls)} candidates; manifest {f.mpath}")
    else:
        c = candidates(f, urls)
        with open(a.out, "w", encoding="utf-8") as o:
            o.write("".join(x + "\n" for x in c))
        print(f"{len(c)} candidates -> {a.out}")


if __name__ == "__main__":
    main()
