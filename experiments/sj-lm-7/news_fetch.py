"""docs/contracts/news-corpus.md section 3.6: fetch news text politely and clean it into a `unit<TAB>paragraph` TSV.

  news_fetch.py press [--feed-url URL] [--dataset-id ID ...|--all-datasets] [--dataset-url URL ...] --out FILE
      Official endpoints only, never page scraping: the press-release feed (--feed-url) and data.gov.tw dataset downloads. A dataset id is
      resolved through --dataset-api (a template with {id}) to its download URLs; --dataset-url gives a download URL directly.
  news_fetch.py pages (--urls FILE | --sitemap URL ...) [--lastmod-from D] [--lastmod-to D] [--config FILE] --out FILE
      Generic article pages. robots.txt is read first and disallowed URLs are skipped. No outlet or site name lives in this file:
      exclusion markers and boilerplate patterns come from --exclude-marker / --boilerplate (repeatable) or --config (JSON:
      {"exclude_markers": [...], "boilerplate": [regex, ...]}).

Common to both: one connection, at least 2 s between requests (more if robots.txt asks), a descriptive User-Agent (--user-agent).
Cleaning (clean_pool): field lines dropped; articles with an exclusion marker dropped; a clause that occurs >= 5 times across the pool is
kept at its first occurrence only. Output counts (numbers only) go to stdout.
"""
import argparse
import collections
import csv
import html
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

USER_AGENT = "shanjie-research-fetcher/1.0 (personal non-commercial research; text is used to count words, not redistributed)"
MIN_INTERVAL = 2.0
TIMEOUT = 30

# holdout-refresh section 2: the 23 press-release datasets on data.gov.tw.
DATASET_IDS = [8372, 7404, 44954, 30143, 6040, 7308, 46112, 6420, 162571, 95226, 169967, 94024, 11205, 171979, 25848, 22927, 7505,
               68551, 95057, 48672, 48659, 48504, 6298]
# ponytail: the data.gov.tw metadata API shape is from memory, not exercised (no network in this change); --dataset-url bypasses it.
DATASET_API = "https://data.gov.tw/api/v2/rest/dataset/{id}"

# Lines that are fields of a release, not prose (contact person, release date, attachments).
FIELD_LINE = re.compile(r"^\s*(新聞聯絡人|聯絡人|聯絡電話|連絡電話|聯絡方式|承辦人|發言人|發布日期|發佈日期|發布時間|發稿日期|發稿時間|新聞稿編號|"
                        r"附件|附檔|相關附件|電話|傳真|電子郵件|E-?mail|Email|Tel|Fax)\s*[:：]", re.I)
CLAUSE = re.compile(r"[^。！？；，\n]+[。！？；，]?")
MIN_CLAUSE = 4        # a repeated clause shorter than this is ordinary speech ("好的"), not boilerplate; same floor as clean.MIN_LEN
REPEAT = 5


class Blocked(Exception):
    """robots.txt disallows the URL (or could not be read)."""


class Fetcher:
    """One connection, polite. opener(url, headers, timeout) -> (status, bytes) never raises on an HTTP status; clock/sleep are injectable."""

    def __init__(self, user_agent=USER_AGENT, interval=MIN_INTERVAL, opener=None, clock=time.monotonic, sleep=time.sleep):
        self.ua, self.interval, self.clock, self.sleep = user_agent, interval, clock, sleep
        self.opener = opener or _urlopen
        self.last = None
        self.robots = {}    # origin -> RobotFileParser, or None = everything disallowed
        self.requests = 0

    def _wait(self):
        if self.last is not None:
            gap = self.clock() - self.last
            if gap < self.interval:
                self.sleep(self.interval - gap)

    def raw(self, url):
        self._wait()
        try:
            return self.opener(url, {"User-Agent": self.ua}, TIMEOUT)
        finally:
            self.last = self.clock()   # measured from the end of the previous request
            self.requests += 1

    def allowed(self, url):
        s = urlsplit(url)
        origin = f"{s.scheme}://{s.netloc}"
        if origin not in self.robots:
            rp = RobotFileParser()
            try:
                status, body = self.raw(origin + "/robots.txt")
            except OSError:
                status, body = 599, b""
            if status == 200:
                rp.parse(body.decode("utf-8", errors="replace").splitlines())
                delay = rp.crawl_delay(self.ua.split("/")[0])
                if delay:
                    self.interval = max(self.interval, float(delay))
            elif 400 <= status < 500:        # RFC 9309: no robots.txt means no restriction
                rp.allow_all = True
            else:                            # 5xx or unreachable: do not crawl
                rp = None
            self.robots[origin] = rp
        rp = self.robots[origin]
        return rp is not None and rp.can_fetch(self.ua.split("/")[0], url)

    def get(self, url, check_robots=True):
        """Body bytes. Raises Blocked, or OSError for a non-200."""
        if check_robots and not self.allowed(url):
            raise Blocked(url)
        status, body = self.raw(url)
        if status != 200:
            raise OSError(f"HTTP {status}")
        return body


def _urlopen(url, headers, timeout):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""


# ---------- text extraction ----------

BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "blockquote"}
SKIP = {"script", "style", "noscript", "template"}


class _Blocks(HTMLParser):
    """Text blocks: [(text, in_p, in_article)]. A block ends at any block-level tag."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.cur, self.skip, self.p, self.art = [], [], 0, 0, 0

    def flush(self):
        t = re.sub(r"\s+", " ", "".join(self.cur)).strip()
        if t:
            self.out.append((t, self.p > 0, self.art > 0))
        self.cur = []

    def handle_starttag(self, tag, attrs):
        if tag in SKIP:
            self.skip += 1
        if tag in BLOCK:
            self.flush()
        self.p += tag == "p"
        self.art += tag == "article"

    def handle_endtag(self, tag):
        if tag in SKIP:
            self.skip = max(0, self.skip - 1)
        if tag in BLOCK:
            self.flush()
        self.p = max(0, self.p - (tag == "p"))
        self.art = max(0, self.art - (tag == "article"))

    def handle_data(self, data):
        if not self.skip:
            self.cur.append(data)


def html_paragraphs(text):
    """Paragraphs of an HTML (or plain text) body. Plain text: one per non-empty line. HTML: <p> texts, inside <article> when there is one;
    with no <p> at all, every text block."""
    if "<" not in text:
        return [re.sub(r"\s+", " ", l).strip() for l in text.splitlines() if l.strip()]
    b = _Blocks()
    b.feed(text)
    b.flush()
    for keep in (lambda x: x[1] and x[2], lambda x: x[1], lambda x: True):
        got = [t for t in b.out if keep(t)]
        if got:
            return [t[0] for t in got]
    return []


def sitemap_urls(xml_bytes):
    """[(loc, lastmod or None, is_index_entry)] from a sitemap or sitemap index."""
    root = ET.fromstring(xml_bytes)
    out = []
    for el in root.iter():
        kind = el.tag.rsplit("}", 1)[-1]
        if kind in ("url", "sitemap"):
            ch = {c.tag.rsplit("}", 1)[-1]: (c.text or "").strip() for c in el}
            if ch.get("loc"):
                out.append((ch["loc"], ch.get("lastmod") or None, kind == "sitemap"))
    return out


def in_window(lastmod, lo, hi):
    """lo/hi: 'YYYY-MM-DD' or None. An entry without lastmod cannot be judged and is kept."""
    if not lastmod or (not lo and not hi):
        return True
    d = lastmod[:10]
    return (not lo or d >= lo) and (not hi or d <= hi)


# ---------- records from official downloads ----------

def records_from(body, name=""):
    """list[dict] from a JSON / CSV / XML download (sniffed): JSON list or the first list inside an object; XML: the most repeated element
    that has child elements; otherwise CSV."""
    text = body.decode("utf-8-sig", errors="replace")
    s = text.lstrip()
    if s[:1] in "[{":
        data = json.loads(s)
        while isinstance(data, dict):
            lists = [v for v in data.values() if isinstance(v, list) and v and isinstance(v[0], dict)]
            if not lists:
                return [data]
            data = lists[0]
        return [r for r in data if isinstance(r, dict)]
    if s[:1] == "<":
        root = ET.fromstring(body)
        count = collections.Counter(e.tag for e in root.iter() if len(e))
        if not count:
            return []
        tag = count.most_common(1)[0][0]
        return [{c.tag.rsplit("}", 1)[-1]: "".join(c.itertext()) for c in e} for e in root.iter(tag)]
    return list(csv.DictReader(io.StringIO(text)))


TEXT_FIELDS = ["content", "Content", "body", "description", "內容", "內文", "新聞內容", "新聞稿內容", "全文", "摘要"]
ID_FIELDS = ["id", "ID", "guid", "link", "url", "序號", "編號", "title", "標題", "新聞標題"]


def record_units(records, prefix, text_fields=None, id_fields=None):
    """[(unit, [paragraph])]: the first text field a record has, split into paragraphs; the unit id is the first id field or the position."""
    text_fields, id_fields = text_fields or TEXT_FIELDS, id_fields or ID_FIELDS
    out = []
    for n, r in enumerate(records):
        field = next((f for f in text_fields if (r.get(f) or "").strip()), None)
        if field is None:
            continue
        uid = next((str(r[f]).strip() for f in id_fields if r.get(f) not in (None, "")), str(n))
        body = str(r[field])
        out.append((f"{prefix}:{uid}", html_paragraphs(html.unescape(body) if "&lt;" in body else body)))
    return out


def dataset_downloads(meta_bytes):
    """Download URLs listed in a data.gov.tw dataset metadata JSON (result.distribution[*]); JSON / CSV / XML ones only."""
    dist = (json.loads(meta_bytes).get("result") or {}).get("distribution") or []
    urls = []
    for d in dist:
        u = d.get("resourceDownloadUrl") or d.get("downloadURL") or d.get("downloadUrl")
        if u and (d.get("resourceFormat") or d.get("format") or "").upper() in ("JSON", "CSV", "XML", ""):
            urls.append(u)
    return urls


# ---------- cleaning ----------

def clean_pool(units, exclude_markers=(), boilerplate=()):
    """units: [(unit, [paragraph])] -> (kept units, counts dict).
    1. an article whose text contains an exclusion marker is dropped whole;
    2. a field line (contact person, release date, attachments) or a line matching a boilerplate regex is dropped;
    3. a clause of >= MIN_CLAUSE characters that occurs >= REPEAT times over the pool (after 1 and 2) is kept at its first occurrence only."""
    pats = [re.compile(b) for b in boilerplate]
    stats = collections.Counter()
    kept = []
    for uid, paras in units:
        if any(m in p for m in exclude_markers for p in paras):
            stats["articles_excluded"] += 1
            continue
        good = []
        for p in paras:
            if FIELD_LINE.match(p) or any(r.search(p) for r in pats):
                stats["lines_dropped"] += 1
            else:
                good.append(p)
        kept.append((uid, good))
    count = collections.Counter(_key(c) for _, ps in kept for p in ps for c in CLAUSE.findall(p))
    repeated = {k for k, v in count.items() if len(k) >= MIN_CLAUSE and v >= REPEAT}
    seen, out = set(), []
    for uid, ps in kept:
        new = []
        for p in ps:
            keep, cut = [], False
            for c in CLAUSE.findall(p):
                k = _key(c)
                if k in repeated:
                    if k in seen:
                        stats["clauses_repeat_dropped"] += 1
                        cut = True
                        continue
                    seen.add(k)
                keep.append(c)
            q = "".join(keep).strip() if cut else p     # an untouched paragraph stays byte for byte
            if _key(q):
                new.append(q)
        if new:
            out.append((uid, new))
    stats["articles_kept"] = len(out)
    return out, dict(stats)


def _key(clause):
    return clause.strip().rstrip("。！？；，").strip()


def write_tsv(units, out):
    flat = lambda t: re.sub(r"[\t\r\n]+", " ", t)
    with open(out + ".tmp", "w", encoding="utf-8") as f:
        for uid, paras in units:
            for p in paras:
                f.write(f"{flat(uid)}\t{flat(p)}\n")
    os.replace(out + ".tmp", out)


# ---------- commands ----------

def load_config(a):
    cfg = json.load(open(a.config, encoding="utf-8")) if a.config else {}
    return list(cfg.get("exclude_markers", [])) + a.exclude_marker, list(cfg.get("boilerplate", [])) + a.boilerplate


def cmd_press(a, f):
    units, failed = [], 0
    jobs = []     # (prefix, url, robots check)
    if a.feed_url:
        jobs.append(("feed", a.feed_url))
    ids = DATASET_IDS if a.all_datasets else a.dataset_id
    for i in ids:
        try:
            for n, u in enumerate(dataset_downloads(f.get(a.dataset_api.format(id=i), check_robots=False))):
                jobs.append((f"d{i}" + (f"-{n}" if n else ""), u))
        except (OSError, ValueError):
            failed += 1
    jobs += [(f"u{n}", u) for n, u in enumerate(a.dataset_url)]
    for prefix, url in jobs:
        try:
            units += record_units(records_from(f.get(url, check_robots=False), url), prefix, a.text_field or None, a.id_field or None)
        except (OSError, ValueError, ET.ParseError):
            failed += 1
    return units, {"downloads": len(jobs), "downloads_failed": failed}


def cmd_pages(a, f):
    urls = [l.strip() for l in open(a.urls, encoding="utf-8")] if a.urls else []
    urls = [u for u in urls if u and not u.startswith("#")]
    queue, seen_maps = list(a.sitemap), set()
    stats = collections.Counter()
    while queue:
        sm = queue.pop(0)
        if sm in seen_maps:
            continue
        seen_maps.add(sm)
        try:
            entries = sitemap_urls(f.get(sm))
        except (Blocked, OSError, ET.ParseError):
            stats["sitemaps_failed"] += 1
            continue
        for loc, lastmod, is_index in entries:
            if is_index:
                queue.append(urljoin(sm, loc))
            elif in_window(lastmod, a.lastmod_from, a.lastmod_to):
                urls.append(urljoin(sm, loc))
    units = []
    for u in dict.fromkeys(urls):
        try:
            units.append((u, html_paragraphs(f.get(u).decode("utf-8", errors="replace"))))
        except Blocked:
            stats["blocked_by_robots"] += 1
        except OSError:
            stats["fetch_failed"] += 1
    stats["urls"] = len(units) + stats["blocked_by_robots"] + stats["fetch_failed"]
    return units, dict(stats)


def main(argv=None, opener=None, clock=time.monotonic, sleep=time.sleep):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--user-agent", default=USER_AGENT)
    common.add_argument("--out", required=True)
    common.add_argument("--config")
    common.add_argument("--exclude-marker", action="append", default=[])
    common.add_argument("--boilerplate", action="append", default=[], metavar="REGEX")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("press", parents=[common])
    p.add_argument("--feed-url")
    p.add_argument("--dataset-id", type=int, action="append", default=[])
    p.add_argument("--all-datasets", action="store_true", help="the 23 ids of holdout-refresh section 2")
    p.add_argument("--dataset-url", action="append", default=[])
    p.add_argument("--dataset-api", default=DATASET_API)
    p.add_argument("--text-field", action="append", default=[])
    p.add_argument("--id-field", action="append", default=[])
    g = sub.add_parser("pages", parents=[common])
    g.add_argument("--urls")
    g.add_argument("--sitemap", action="append", default=[])
    g.add_argument("--lastmod-from")
    g.add_argument("--lastmod-to")
    a = ap.parse_args(argv)
    if a.cmd == "pages" and not (a.urls or a.sitemap):
        ap.error("pages needs --urls or --sitemap")
    f = Fetcher(a.user_agent, MIN_INTERVAL, opener, clock, sleep)
    units, stats = (cmd_press if a.cmd == "press" else cmd_pages)(a, f)
    markers, boiler = load_config(a)
    kept, cstats = clean_pool(units, markers, boiler)
    write_tsv(kept, a.out)
    stats.update(cstats)
    stats["requests"] = f.requests
    print("\t".join(f"{k}={v}" for k, v in sorted(stats.items())))   # numbers only


if __name__ == "__main__":
    main()
