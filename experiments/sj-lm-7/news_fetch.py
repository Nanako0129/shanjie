"""docs/contracts/news-corpus.md section 3.6: fetch news text politely and clean it into a `unit<TAB>paragraph` TSV.

  news_fetch.py press [--feed-url URL] [--dataset-id ID ...|--all-datasets] [--dataset-url URL ...] --out FILE.tsv
      Official endpoints only, never page scraping: the press-release feed (--feed-url) and data.gov.tw dataset downloads. A dataset id is
      resolved through --dataset-api (a template with {id}) to its download URLs; --dataset-url gives a download URL directly.
  news_fetch.py pages (--urls FILE | --sitemap URL ...) [--lastmod-from D] [--lastmod-to D] [--config FILE] --out FILE.tsv
      Generic article pages. robots.txt is read first and disallowed URLs are skipped. No outlet or site name lives in this file:
      exclusion markers and boilerplate patterns come from --exclude-marker / --boilerplate (repeatable) or --config (JSON:
      {"exclude_markers": [...], "boilerplate": [regex, ...]}).
      With a date window the sitemap's lastmod only drops entries last modified before --lastmod-from (a page cannot be published after it
      was modified); every fetched page is then judged by its own publish date (meta article:published_time, JSON-LD datePublished, or
      <time datetime>), and a page with no date or outside the window is dropped (no_date, out_of_window).

--out must end in .tsv: build_counts_text.py strips the unit column only for a .tsv name. The unit is a URL, a record id field, or a hash of
the record, never a title.
Common: one connection, at least 2 s between requests (more if robots.txt asks), a descriptive User-Agent (--user-agent). A failed URL is
counted by kind (failed_http_status, failed_network, failed_protocol, failed_decode, failed_parse, failed_bad_url, failed_no_text_field) and
skipped. Every unit that survives the per-article rules is appended to FILE.tsv.partial as it is fetched; FILE.tsv is written at the end,
after the pool-wide rule, and the partial file is removed.
Cleaning: field lines dropped; articles with an exclusion marker dropped; a clause that occurs >= 5 times across the pool is kept only at
its first occurrence. Output counts (numbers only) go to stdout.
"""
import argparse
import codecs
import collections
import csv
import hashlib
import html
import http.client
import io
import json
import os
import re
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


class StatusError(OSError):
    """A non-200 answer."""


class NoTextField(ValueError):
    """A downloaded record set with no record that has a text field."""


class BadURL(ValueError):
    pass


FETCH_ERRORS = (OSError, http.client.HTTPException, ValueError, ET.ParseError, csv.Error)


def kind_of(e):
    """Failure kind for the counters."""
    if isinstance(e, StatusError):
        return "http_status"
    if isinstance(e, NoTextField):
        return "no_text_field"
    if isinstance(e, UnicodeError):
        return "decode"
    if isinstance(e, (json.JSONDecodeError, ET.ParseError, csv.Error)):
        return "parse"
    if isinstance(e, http.client.HTTPException):
        return "protocol"
    if isinstance(e, OSError):
        return "network"
    return "bad_url"      # the remaining ValueErrors come from urlsplit / urljoin / the URL check


def decode_body(body, content_type=""):
    """Text of a response body. Order: charset in the Content-Type header, a BOM, strict UTF-8, strict cp950 (Big5). Never errors=replace:
    when nothing decodes strictly, UnicodeDecodeError. A header charset that does not decode the bytes (or is unknown) falls through."""
    m = re.search(r"charset\s*=\s*[\"']?([\w.:-]+)", content_type or "", re.I)
    if m:
        try:
            return body.decode(m.group(1))
        except (LookupError, UnicodeDecodeError):
            pass
    for bom, enc in ((codecs.BOM_UTF8, "utf-8-sig"), (codecs.BOM_UTF16_LE, "utf-16"), (codecs.BOM_UTF16_BE, "utf-16")):
        if body.startswith(bom):
            return body.decode(enc)
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return body.decode("cp950")


class Fetcher:
    """One connection, polite. opener(url, headers, timeout) -> (status, bytes, content_type) never raises on an HTTP status;
    clock/sleep are injectable."""

    def __init__(self, user_agent=USER_AGENT, interval=MIN_INTERVAL, opener=None, clock=time.monotonic, sleep=time.sleep):
        self.ua, self.interval, self.clock, self.sleep = user_agent, interval, clock, sleep
        self.opener = opener or _urlopen
        self.last = None
        self.robots = {}    # origin -> RobotFileParser, or None = origin skipped
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
                status, body, _ = self.raw(origin + "/robots.txt")
            except (OSError, http.client.HTTPException):
                status, body = 599, b""
            if status == 200:
                try:
                    rp.parse(decode_body(body).splitlines())
                except UnicodeDecodeError:
                    rp = None      # a robots.txt we cannot read is not one we can obey
                else:
                    delay = rp.crawl_delay(self.ua.split("/")[0])
                    if delay:
                        self.interval = max(self.interval, float(delay))
            elif status in (401, 403):   # as Python's RobotFileParser.read: access denied means disallow all
                rp.disallow_all = True
            elif 400 <= status < 500:    # RFC 9309: no robots.txt means no restriction
                rp.allow_all = True
            else:                        # 5xx or unreachable: do not crawl
                rp = None
            self.robots[origin] = rp
        rp = self.robots[origin]
        return rp is not None and rp.can_fetch(self.ua.split("/")[0], url)

    def fetch(self, url, check_robots=True):
        """(body bytes, content type). Raises Blocked, BadURL, StatusError (non-200), or the opener's own errors."""
        s = urlsplit(url)
        if s.scheme not in ("http", "https") or not s.netloc:
            raise BadURL("not an http(s) URL")
        if check_robots and not self.allowed(url):
            raise Blocked(url)
        status, body, ctype = self.raw(url)
        if status != 200:
            raise StatusError(f"HTTP {status}")
        return body, ctype

    def get(self, url, check_robots=True):
        return self.fetch(url, check_robots)[0]

    def text(self, url, check_robots=True):
        body, ctype = self.fetch(url, check_robots)
        return decode_body(body, ctype)


def _urlopen(url, headers, timeout):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
            return r.status, r.read(), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return e.code, b"", ""


# ---------- text extraction ----------

# Start (or end) tags that implicitly close an open <p> (HTML spec, "p element end tag may be omitted").
P_CLOSERS = {"address", "article", "aside", "blockquote", "details", "div", "dl", "fieldset", "figcaption", "figure", "footer", "form",
             "h1", "h2", "h3", "h4", "h5", "h6", "header", "hgroup", "hr", "main", "menu", "nav", "ol", "p", "pre", "search", "section",
             "table", "ul"}
BLOCK = P_CLOSERS | {"br", "li", "tr", "td", "th", "dt", "dd", "tbody", "thead", "caption", "body"}
SKIP = {"script", "style", "noscript", "template"}


class _Blocks(HTMLParser):
    """Text blocks: [(text, in_p, in_article)]. A block ends at any block-level tag; an open <p> ends at the next P_CLOSERS tag."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.cur, self.skip, self.p, self.art = [], [], 0, False, 0

    def flush(self):
        t = re.sub(r"\s+", " ", "".join(self.cur)).strip()
        if t:
            self.out.append((t, self.p, self.art > 0))
        self.cur = []

    def handle_starttag(self, tag, attrs):
        if tag in SKIP:
            self.skip += 1
        if tag in BLOCK:
            self.flush()
        if tag in P_CLOSERS:
            self.p = False
        if tag == "p":
            self.p = True
        self.art += tag == "article"

    def handle_endtag(self, tag):
        if tag in SKIP:
            self.skip = max(0, self.skip - 1)
        if tag in BLOCK:
            self.flush()
        if tag in P_CLOSERS:
            self.p = False
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


class _Dates(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta, self.time, self.ld, self._buf = None, None, [], None

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): v for k, v in attrs}
        if tag == "meta" and (a.get("property") or a.get("name") or "").lower() == "article:published_time":
            self.meta = self.meta or a.get("content")
        elif tag == "time" and a.get("datetime"):
            self.time = self.time or a["datetime"]
        elif tag == "script" and "ld+json" in (a.get("type") or "").lower():
            self._buf = []

    def handle_data(self, data):
        if self._buf is not None:
            self._buf.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self._buf is not None:
            self.ld.append("".join(self._buf))
            self._buf = None


def _find_key(obj, key):
    if isinstance(obj, dict):
        if isinstance(obj.get(key), str):
            return obj[key]
        obj = list(obj.values())
    for v in obj if isinstance(obj, list) else ():
        r = _find_key(v, key)
        if r:
            return r
    return None


def publish_date(page):
    """'YYYY-MM-DD' of the page's own publish date, or None. Order: meta article:published_time, JSON-LD datePublished, first <time datetime>."""
    d = _Dates()
    d.feed(page)
    cands = [d.meta]
    for blob in d.ld:
        try:
            cands.append(_find_key(json.loads(blob), "datePublished"))
        except ValueError:
            pass
    cands.append(d.time)
    for c in cands:
        m = re.match(r"\s*(\d{4})-(\d{2})-(\d{2})", c or "")
        if m:
            return "-".join(m.groups())
    return None


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


def in_window(date, lo, hi):
    """date: ISO date or datetime string; lo/hi: 'YYYY-MM-DD' or None (open). A missing date is judged by the caller, not here: True."""
    if not date or (not lo and not hi):
        return True
    d = date[:10]
    return (not lo or d >= lo) and (not hi or d <= hi)


# ---------- records from official downloads ----------

def records_from(body, content_type=""):
    """list[dict] from a JSON / CSV / XML download (sniffed): JSON list or the first list inside an object; XML: the most repeated element
    that has child elements; otherwise CSV. Text is decoded with decode_body (strict; Big5 fallback)."""
    head = body.lstrip(codecs.BOM_UTF8 + b" \t\r\n")[:1]
    if head == b"<":
        root = ET.fromstring(body)
        count = collections.Counter(e.tag for e in root.iter() if len(e))
        if not count:
            return []
        tag = count.most_common(1)[0][0]
        return [{c.tag.rsplit("}", 1)[-1]: "".join(c.itertext()) for c in e} for e in root.iter(tag)]
    text = decode_body(body, content_type).lstrip("﻿")
    if head in (b"[", b"{"):
        data = json.loads(text)
        while isinstance(data, dict):
            lists = [v for v in data.values() if isinstance(v, list) and v and isinstance(v[0], dict)]
            if not lists:
                return [data]
            data = lists[0]
        return [r for r in data if isinstance(r, dict)]
    return list(csv.DictReader(io.StringIO(text)))


TEXT_FIELDS = ["content", "Content", "body", "description", "內容", "內文", "新聞內容", "新聞稿內容", "全文", "摘要"]
ID_FIELDS = ["id", "ID", "guid", "link", "url", "序號", "編號"]      # never a title


def record_id(r, id_fields):
    for f in id_fields:
        if r.get(f) not in (None, ""):
            return str(r[f]).strip()
    return "h" + hashlib.sha256(json.dumps(r, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def record_units(records, prefix, text_fields=None, id_fields=None):
    """[(unit, [paragraph])]: the first text field a record has, split into paragraphs. The unit id is the first id field, else a hash of the
    record. Raises NoTextField when no record has a text field (a failure, not zero units)."""
    text_fields, id_fields = text_fields or TEXT_FIELDS, id_fields or ID_FIELDS
    out = []
    for r in records:
        field = next((f for f in text_fields if (r.get(f) or "").strip()), None)
        if field is None:
            continue
        body = str(r[field])
        out.append((f"{prefix}:{record_id(r, id_fields)}", html_paragraphs(html.unescape(body) if "&lt;" in body else body)))
    if not out:
        raise NoTextField(f"{len(records)} records, no text field")
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

def clean_unit(uid, paras, exclude_markers, pats, stats):
    """Per-article rules. None when the article is dropped (marker) or nothing is left; otherwise (uid, paragraphs)."""
    if any(m in p for m in exclude_markers for p in paras):
        stats["articles_excluded"] += 1
        return None
    good = []
    for p in paras:
        if FIELD_LINE.match(p) or any(r.search(p) for r in pats):
            stats["lines_dropped"] += 1
        else:
            good.append(p)
    return (uid, good) if good else None


def dedupe_pool(units, stats):
    """A clause of >= MIN_CLAUSE characters that occurs >= REPEAT times over the pool is kept at its first occurrence only."""
    count = collections.Counter(_key(c) for _, ps in units for p in ps for c in CLAUSE.findall(p))
    repeated = {k for k, v in count.items() if len(k) >= MIN_CLAUSE and v >= REPEAT}
    seen, out = set(), []
    for uid, ps in units:
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
    return out


def clean_pool(units, exclude_markers=(), boilerplate=()):
    """units: [(unit, [paragraph])] -> (kept units, counts dict): clean_unit on each, then dedupe_pool."""
    pats, stats = [re.compile(b) for b in boilerplate], collections.Counter()
    kept = [u for u in (clean_unit(uid, ps, exclude_markers, pats, stats) for uid, ps in units) if u]
    return dedupe_pool(kept, stats), dict(stats)


def _key(clause):
    return clause.strip().rstrip("。！？；，").strip()


def _flat(t):
    return re.sub(r"[\t\r\n]+", " ", t)


def write_unit(f, uid, paras):
    for p in paras:
        f.write(f"{_flat(uid)}\t{_flat(p)}\n")


# ---------- commands (generators of (unit, paragraphs); they count into stats) ----------

def load_config(a):
    cfg = json.load(open(a.config, encoding="utf-8")) if a.config else {}
    return list(cfg.get("exclude_markers", [])) + a.exclude_marker, list(cfg.get("boilerplate", [])) + a.boilerplate


def gen_press(a, f, stats):
    jobs = []     # (prefix, url)
    if a.feed_url:
        jobs.append(("feed", a.feed_url))
    for i in DATASET_IDS if a.all_datasets else a.dataset_id:
        try:
            for n, u in enumerate(dataset_downloads(f.get(a.dataset_api.format(id=i), check_robots=False))):
                jobs.append((f"d{i}" + (f"-{n}" if n else ""), u))
        except FETCH_ERRORS as e:
            stats["failed_" + kind_of(e)] += 1
    jobs += [(f"u{n}", u) for n, u in enumerate(a.dataset_url)]
    stats["downloads"] = len(jobs)
    for prefix, url in jobs:
        try:
            body, ctype = f.fetch(url, check_robots=False)
            units = record_units(records_from(body, ctype), prefix, a.text_field or None, a.id_field or None)
        except FETCH_ERRORS as e:
            stats["failed_" + kind_of(e)] += 1
            continue
        yield from units


def gen_pages(a, f, stats):
    urls = [l.strip() for l in open(a.urls, encoding="utf-8")] if a.urls else []
    urls = [u for u in urls if u and not u.startswith("#")]
    queue, seen_maps = list(a.sitemap), set()
    while queue:
        sm = queue.pop(0)
        if sm in seen_maps:
            continue
        seen_maps.add(sm)
        try:
            entries = sitemap_urls(f.get(sm))
        except Blocked:
            stats["blocked_by_robots"] += 1
            continue
        except FETCH_ERRORS as e:
            stats["failed_" + kind_of(e)] += 1
            continue
        for loc, lastmod, is_index in entries:
            try:
                u = urljoin(sm, loc)
                urlsplit(u)
            except ValueError:
                stats["failed_bad_url"] += 1
                continue
            if is_index:
                queue.append(u)
            elif in_window(lastmod, a.lastmod_from, None):   # lastmod < from: published before the window; lastmod > to proves nothing
                urls.append(u)
    for u in dict.fromkeys(urls):
        try:
            page = f.text(u)
            if a.lastmod_from or a.lastmod_to:
                d = publish_date(page)
                if d is None:
                    stats["no_date"] += 1
                    continue
                if not in_window(d, a.lastmod_from, a.lastmod_to):
                    stats["out_of_window"] += 1
                    continue
            paras = html_paragraphs(page)
        except Blocked:
            stats["blocked_by_robots"] += 1
            continue
        except FETCH_ERRORS as e:
            stats["failed_" + kind_of(e)] += 1
            continue
        yield u, paras


def main(argv=None, opener=None, clock=time.monotonic, sleep=time.sleep):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--user-agent", default=USER_AGENT)
    common.add_argument("--out", required=True, help="must end in .tsv")
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
    if not a.out.endswith(".tsv"):
        ap.error("--out must end in .tsv (build_counts_text.py strips the unit column only for a .tsv name)")
    if a.cmd == "pages" and not (a.urls or a.sitemap):
        ap.error("pages needs --urls or --sitemap")
    f = Fetcher(a.user_agent, MIN_INTERVAL, opener, clock, sleep)
    markers, boiler = load_config(a)
    pats, stats, kept = [re.compile(b) for b in boiler], collections.Counter(), []
    with open(a.out + ".partial", "w", encoding="utf-8") as part:
        for uid, paras in (gen_press if a.cmd == "press" else gen_pages)(a, f, stats):
            u = clean_unit(uid, paras, markers, pats, stats)
            if u:
                kept.append(u)
                write_unit(part, *u)
                part.flush()
    out_units = dedupe_pool(kept, stats)
    with open(a.out + ".tmp", "w", encoding="utf-8") as fo:
        for u in out_units:
            write_unit(fo, *u)
    os.replace(a.out + ".tmp", a.out)
    os.remove(a.out + ".partial")
    stats["requests"] = f.requests
    print("\t".join(f"{k}={v}" for k, v in sorted(stats.items())))   # numbers only


if __name__ == "__main__":
    main()
