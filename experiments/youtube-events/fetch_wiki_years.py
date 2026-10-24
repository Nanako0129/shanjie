"""zh.wikipedia `YYYY年臺灣` pages -> JSON list of events {date, text, links} (MediaWiki API action=parse, one request per second, CC BY-SA 4.0).
date is as precise as the page gives: YYYY-MM-DD, YYYY-MM (month heading only) or YYYY. A sub-bullet without its own date takes its parent's.
links are the article titles linked in the line (date, year, file and category links dropped).
Usage: python3 fetch_wiki_years.py FIRST_YEAR LAST_YEAR OUT.json   (raw wikitext is not stored)"""
import json
import os
import re
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

API = "https://zh.wikipedia.org/w/api.php"
STOP_SECTIONS = ("參見", "参见", "參考", "参考", "外部連結", "外部链接", "注釋", "註釋", "腳註", "脚注", "相關條目", "相关条目")
NOT_ARTICLE = re.compile(r"^(?:File|Image|Category|檔案|文件|分類|分类|圖像|圖片):|^\d{4}年(?:\d{1,2}月(?:\d{1,2}日)?)?$|^\d{1,2}月(?:\d{1,2}日)?$", re.I)
SEP = r"\s*[，,：:、\-–—]?\s*"


def fetch_wikitext(year, get=common.http_get):
    q = urllib.parse.urlencode({"action": "parse", "page": f"{year}年臺灣", "prop": "wikitext", "redirects": "1", "format": "json", "formatversion": "2"})
    return json.loads(get(API + "?" + q))["parse"]["wikitext"]


def drop_noise(s):
    s = re.sub(r"<!--.*?-->|<ref[^>/]*/>|<ref[^>]*>.*?</ref>", "", s, flags=re.S)
    return s


def clean(s):
    s = drop_noise(s)
    while True:
        t = re.sub(r"\{\{[^{}]*\}\}", "", s)
        if t == s:
            break
        s = t
    s = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", s)
    s = re.sub(r"\[https?://\S+\s+([^\]]*)\]|\[https?://\S+\]", r"\1", s)
    s = re.sub(r"<[^>]+>|'{2,}", "", s)
    return " ".join(s.split())


def links(s):
    out = []
    for t in re.findall(r"\[\[([^\]|#]+)", drop_noise(s)):
        t = t.strip()
        if t and not NOT_ARTICLE.match(t) and t not in out:
            out.append(t)
    return out


def parse(wikitext, year):
    """-> [{date, text, links}]"""
    events, month, skip, parent = [], None, False, None
    for line in wikitext.splitlines():
        h = re.match(r"^=+\s*(.*?)\s*=+\s*$", line)
        if h:
            m = re.match(r"^(\d{1,2})月$", clean(h.group(1)))
            month, parent = (int(m.group(1)) if m else None), None
            skip = any(w in h.group(1) for w in STOP_SECTIONS)
            continue
        b = re.match(r"^(\*+)\s*(.*)$", line)
        if skip or not b:
            continue
        depth, raw = len(b.group(1)), b.group(2)
        text = clean(raw)
        date = None
        if m := re.match(rf"^(\d{{1,2}})月(\d{{1,2}})日{SEP}(.*)$", text):
            date, text = f"{year}-{int(m.group(1)):02d}-{int(m.group(2)):02d}", m.group(3)
        elif m := re.match(rf"^(\d{{1,2}})月{SEP}(.*)$", text):
            date, text = f"{year}-{int(m.group(1)):02d}", m.group(2)
        elif month and (m := re.match(rf"^(\d{{1,2}})日{SEP}(.*)$", text)):
            date, text = f"{year}-{month:02d}-{int(m.group(1)):02d}", m.group(2)
        if date is None:
            date = parent if depth > 1 and parent else (f"{year}-{month:02d}" if month else str(year))
        elif depth == 1:
            parent = date
        if len(text) >= 4:
            events.append({"date": date, "text": text, "links": links(raw)})
    return events


def main(first, last, out):
    events = []
    for y in range(int(first), int(last) + 1):
        events += parse(fetch_wikitext(y), y)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(events, f, ensure_ascii=False, indent=1)
    print(f"{len(events)} events")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    main(*sys.argv[1:])
