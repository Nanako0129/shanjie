"""Candidate strings (2-8 Han characters) from video metadata and wiki events -> TSV: string, event, date, source.
Sources: quote (inside 「」『』《》“”【】), tag, title-run / chapter-run (Han runs split at punctuation, question words and function words),
wiki-link (linked article title), wiki-quote. Event = video title (chapter lines: "title / chapter text") or the wiki line; date = YYYY-MM-DD of the
video's publish date or the wiki line's date (may be YYYY-MM or YYYY).
Usage: python3 extract_candidates.py VIDEOS.json WIKI.json OUT.tsv   (either input may be - to skip)"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

MIN_LEN, MAX_LEN = 2, 8
# The channel's own name and its fixed series suffixes: removed from titles before splitting (longest first) and never a candidate.
STOP_PHRASES = ("峰哥 中秋烤肉", "峰哥中秋烤肉", "峰哥Fege", "峰哥 Fege", "峰哥", "Fege", "FEGE")
# Chapter boilerplate that is never an event word (exact match).
GENERIC = frozenset(("開場", "開頭", "結尾", "片頭", "片尾", "閒聊", "廣告", "贊助", "訂閱", "感謝", "預告", "總結", "結語"))
# Han runs are cut at these (question words, particles, conjunctions); a name that contains one is still found through its quotes, tags or wiki links.
SPLIT_WORDS = ("為什麼", "為甚麼", "是不是", "有沒有", "怎麼", "怎樣", "什麼", "甚麼", "如何", "到底", "竟然", "居然", "原來", "難道", "嗎", "呢", "吧", "啊", "呀",
               "的", "了", "是", "在", "和", "與", "跟", "被", "把", "就", "都", "也", "還", "又", "很", "最")
QUOTES = "「」『』《》“”【】"
QUOTED = re.compile("「([^」]*)」|『([^』]*)』|《([^》]*)》|“([^”]*)”|【([^】]*)】")
HAN = r"[一-鿿㐀-䶿]"
HAN_RUN = re.compile(HAN + "+")
CHAPTER = re.compile(r"^[^\w]*\(?((?:\d{1,2}:)?\d{1,2}:\d{2})\)?[\s\-–—:：.、]*(\S.*)$")
SPLIT = re.compile("|".join(map(re.escape, sorted(SPLIT_WORDS, key=len, reverse=True))))


def chapters(description):
    """Chapter lines `mm:ss text` / `hh:mm:ss text` (also `0:00 - text`, `(1:02) text`, leading bullets) -> [(seconds, text)]."""
    out = []
    for line in description.splitlines():
        m = CHAPTER.match(line.strip())
        if m:
            secs = 0
            for p in m.group(1).split(":"):
                secs = secs * 60 + int(p)
            out.append((secs, m.group(2).strip()))
    return out


def strip_stop(s):
    for p in sorted(STOP_PHRASES, key=len, reverse=True):
        s = s.replace(p, "|")
    return s


def han_runs(s):
    """2-8 character Han runs of s, split at non-Han characters and SPLIT_WORDS."""
    return [r for chunk in HAN_RUN.findall(strip_stop(s)) for r in SPLIT.split(chunk) if MIN_LEN <= len(r) <= MAX_LEN]


def quoted(s):
    """Han-only strings of 2-8 characters inside quote or bracket pairs (inner text with other characters is cut into runs)."""
    out = []
    for m in QUOTED.finditer(strip_stop(s)):
        inner = next(g for g in m.groups() if g is not None).strip()
        out += [inner] if re.fullmatch(HAN + "+", inner) and MIN_LEN <= len(inner) <= MAX_LEN else han_runs(inner)
    return out


def tag_string(tag):
    t = strip_stop(tag).replace(" ", "").strip()
    return t if re.fullmatch(HAN + "+", t) and MIN_LEN <= len(t) <= MAX_LEN else None


def wiki_title(link):
    t = re.sub(r"\s*[（(].*$", "", link).strip()      # drop the disambiguation suffix
    return t if re.fullmatch(HAN + "+", t) and MIN_LEN <= len(t) <= MAX_LEN else None


def candidates(videos, events):
    """-> [(string, event, date, source)], duplicates removed, stop phrases dropped."""
    rows = []
    for v in videos:
        date, title = v["published"][:10], v["title"]
        for s in quoted(title):
            rows.append((s, title, date, "quote"))
        for s in han_runs(title):
            rows.append((s, title, date, "title-run"))
        for tag in v.get("tags", []):
            if (s := tag_string(tag)):
                rows.append((s, title, date, "tag"))
        for _, text in chapters(v.get("description", "")):
            ev = f"{title} / {text}"
            rows += [(s, ev, date, "quote") for s in quoted(text)] + [(s, ev, date, "chapter-run") for s in han_runs(text)]
    for e in events:
        for link in e.get("links", []):
            if (s := wiki_title(link)):
                rows.append((s, e["text"], e["date"], "wiki-link"))
        rows += [(s, e["text"], e["date"], "wiki-quote") for s in quoted(e["text"])]
    seen, out = set(), []
    for r in rows:
        if r not in seen and r[0] not in GENERIC and not any(p in r[0] for p in STOP_PHRASES):
            seen.add(r)
            out.append(r)
    return out


def load(path):
    if path == "-":
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main(videos, wiki, out):
    rows = candidates(load(videos), load(wiki))
    common.write_tsv(out, ("string", "event", "date", "source"), [dict(zip(("string", "event", "date", "source"), r)) for r in rows])
    print(f"{len(rows)} rows, {len({r[0] for r in rows})} distinct strings")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    main(*sys.argv[1:])
