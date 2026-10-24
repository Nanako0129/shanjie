"""Event-word check against Google News Taiwan RSS (docs/contracts/youtube-events.md part A).
For each (string, date) of the candidate TSV: search the quoted string, count the headlines that contain it and are dated within +-WINDOW_DAYS of
the date, and the distinct outlets (<source> element) of those headlines. Pass = >= MIN_HEADLINES headlines from >= MIN_OUTLETS outlets.
Only counts are written; headlines are never stored. Raw RSS responses are cached under CACHE_DIR (one file per request URL) and the network is
hit at most once per second. A date of YYYY-MM covers the month, YYYY the year (plus the window); an empty date is not checked.
The query carries `after:`/`before:` operators for the window so that old events are searched in their own time, not the latest 100 results.
Usage: python3 verify_news.py CANDIDATES.tsv OUT.tsv CACHE_DIR [--no-date-operators]"""
import calendar
import datetime as dt
import email.utils
import hashlib
import os
import sys
import urllib.parse
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

WINDOW_DAYS = 7
MIN_HEADLINES, MIN_OUTLETS = 3, 2
RSS = "https://news.google.com/rss/search?"


def window(date):
    """'YYYY-MM-DD' / 'YYYY-MM' / 'YYYY' -> (first day, last day) including the +-7 day margin; None if the date is not usable."""
    try:
        p = [int(x) for x in date.split("-")]
        if len(p) == 3:
            a = b = dt.date(*p)
        elif len(p) == 2:
            a, b = dt.date(p[0], p[1], 1), dt.date(p[0], p[1], calendar.monthrange(*p)[1])
        elif len(p) == 1 and p[0] > 1900:
            a, b = dt.date(p[0], 1, 1), dt.date(p[0], 12, 31)
        else:
            return None
    except (ValueError, TypeError):
        return None
    d = dt.timedelta(days=WINDOW_DAYS)
    return a - d, b + d


def url(string, win, operators=True):
    q = f'"{string}"' + (f" after:{win[0]} before:{win[1] + dt.timedelta(days=1)}" if operators else "")
    return RSS + urllib.parse.urlencode({"q": q, "hl": "zh-TW", "gl": "TW", "ceid": "TW:zh-Hant"})


def fetch(u, cache_dir, get=common.http_get):
    """Raw response bytes, from the cache directory if present."""
    path = os.path.join(cache_dir, hashlib.sha1(u.encode()).hexdigest() + ".xml")
    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read()
    data = get(u)
    os.makedirs(cache_dir, exist_ok=True)
    with open(path + ".part", "wb") as f:
        f.write(data)
    os.replace(path + ".part", path)
    return data


def count(xml, string, win):
    """-> (headlines containing string dated inside win, distinct outlets among them)."""
    n, outlets = 0, set()
    for item in ET.fromstring(xml).iter("item"):
        title, when, src = item.findtext("title") or "", item.findtext("pubDate"), item.findtext("source") or ""
        try:
            day = email.utils.parsedate_to_datetime(when).date()
        except (TypeError, ValueError):
            continue
        if string in title and win[0] <= day <= win[1]:
            n += 1
            outlets.add(src.strip())
    return n, len(outlets - {""})


def verify(string, date, cache_dir, operators=True, get=common.http_get):
    win = window(date)
    if not win:
        return {"headlines": "", "outlets": "", "pass": "nodate"}
    n, o = count(fetch(url(string, win, operators), cache_dir, get), string, win)
    return {"headlines": n, "outlets": o, "pass": int(n >= MIN_HEADLINES and o >= MIN_OUTLETS)}


def main(cands, out, cache_dir, operators=True):
    pairs = sorted({(r["string"], r["date"]) for r in common.read_tsv(cands)})
    rows = [{"string": s, "date": d, **verify(s, d, cache_dir, operators)} for s, d in pairs]
    common.write_tsv(out, ("string", "date", "headlines", "outlets", "pass"), rows)
    print(f"{len(rows)} (string, date) pairs, {sum(r['pass'] == 1 for r in rows)} pass")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--no-date-operators"]
    if len(args) != 3:
        sys.exit(__doc__)
    main(*args, operators="--no-date-operators" not in sys.argv)
