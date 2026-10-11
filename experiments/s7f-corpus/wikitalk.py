"""S7f source 3: zhwiki talk pages (CC BY-SA 4.0) -> cleaned sentences + manifest.

  python wikitalk.py --dump zhwiki-20261001-pages-meta-current.xml.bz2 [more parts] --tune TUNEDIR --out DIR
      -> DIR/wikitalk.txt, DIR/wikitalk.manifest.json

Pages: namespace 1 (討論) and 3 (使用者討論), and namespace 4 pages whose title starts with `Wikipedia:互助客棧` or `Wikipedia:互助客栈` (zhwiki keeps
the village pump under the simplified title; no other policy or vote pages). Streams the XML (bz2 or plain) a page at a time.
Per page: templates (build_counts.TEMPLATE x3), comments, refs and tables removed. A paragraph is one wikitext line. A paragraph that
links a user page whose name ends in bot (or 機器人, or MediaWiki message delivery) is a bot notice and is dropped whole; signatures
(user / user-talk / contributions links, the {{unsigned}} text and the time in the dump's formats, looked for after the markup is gone)
are removed, and on a signed line so is the dash and a short custom signature after it; leading dashes go; redirect pages are dropped, and
so is a sentence that is only a greeting and a non-Han name; the rest of build_counts.MARKUP
turns links into their text; list and indent markers and headings go. Only paragraphs with ZERO simplified-only characters are kept
(no conversion), then clean.py's rules split and filter the sentences.
"""
import argparse
import bz2
import collections
import html
import os
import re

import clean

bc = clean.bc
TITLE_RE, NS_RE = re.compile(r"<title>(.*?)</title>"), re.compile(r"<ns>(\d+)</ns>")
TEXT_RE = re.compile(r"<text[^>]*>(.*?)</text>", re.S)
VILLAGE_PUMP = ("Wikipedia:互助客棧", "Wikipedia:互助客栈")     # the live pages use the simplified title
_USER = r"(?:User|U|用户|用戶|使用者)"
_TALK = r"(?:User[ _]talk|UT|用户讨论|用戶討論|使用者討論)"
_CONTRIB = r"(?:Special|特殊):(?:Contributions|Contribs|用户贡献|用戶貢獻|使用者貢獻)"
BOT = re.compile(rf"\[\[(?:{_USER}|{_TALK}):[^\]|]*(?:bot|機器人|机器人|message delivery)\s*[\]|]", re.I)
SIG_LINK = re.compile(rf"\[\[(?:{_TALK}:|{_USER}:|{_CONTRIB}/)[^\]]*\]\]", re.I)
# The signature times in the dump: `2026年10月1日 (三) 12:34 (UTC)` (also with a full-width colon), before about 2008
# `01:54 2006年3月14日 (UTC)`, and a weekday and time with no zone (`2007年7月23日 (一) 17:45`)
SIG_TIME = re.compile(r"(?:\d{1,2}[:：]\d{2}\s*\d{4}年\d{1,2}月\d{1,2}日|\d{4}年\d{1,2}月\d{1,2}日\s*(?:[（(][^)）]{1,3}[)）])?\s*"
                      r"\d{1,2}[:：]\d{2})\s*[（(](?:UTC|CST)(?:[+-]\d{1,2})?[)）]"
                      r"|\d{4}年\d{1,2}月\d{1,2}日\s*[（(][一二三四五六日][)）]\s*\d{1,2}[:：]\d{2}")
UNSIGNED = re.compile(r"(?:以上未簽名的|此條)留言(?:由.{0,40}?)?於.{0,40}?加入")     # the {{unsigned}} templates' text, substituted
EMPTY_PAREN = re.compile(r"[（(]\s*[）)]")
TRAIL_DASH = re.compile(r"\s*[-—–]{2,}\s*$")
SIG_TAIL = re.compile(r"\s*[-—–]+[^-—–]{0,20}$")     # on a signed line: the dash and a short custom signature (`--發言者：`)
GREETING = re.compile(r"^(?:您好|你好|Hello|Hi|嗨)[，,!！\s]*[^\u4e00-\u9fff]*$", re.I)   # `您好，Username`: a welcome's name sentence
REDIRECT = re.compile(r"\s*#\s*(?:REDIRECT|重定向)", re.I)
HEADING = re.compile(r"^=+.*=+$")
PAGE_MARKUP, LINE_MARKUP = bc.MARKUP[:3], bc.MARKUP[3:]     # comments, refs, tables span lines; the rest is per line


def pages(paths):
    """(ns, title, text) per <page>, text XML-unescaped."""
    for p in paths:
        f = bz2.open(p, "rt", encoding="utf-8") if p.endswith(".bz2") else open(p, encoding="utf-8")
        with f:
            buf = []
            for line in f:
                if "<page>" in line:
                    buf = []
                buf.append(line)
                if "</page>" in line:
                    page = "".join(buf)
                    ns, title, text = NS_RE.search(page), TITLE_RE.search(page), TEXT_RE.search(page)
                    if ns and title:
                        yield int(ns.group(1)), html.unescape(title.group(1)), html.unescape(text.group(1)) if text else ""
                    buf = []


def wanted(ns, title):
    return ns in (1, 3) or (ns == 4 and title.startswith(VILLAGE_PUMP))


def paragraphs(text, simp, counts):
    for _ in range(3):
        text = bc.TEMPLATE.sub("", text)
    for pat, rep in PAGE_MARKUP:
        text = pat.sub(rep, text)
    for line in text.split("\n"):
        if BOT.search(line):
            counts["para_bot"] += 1
            continue
        unlinked = SIG_LINK.sub("", line)
        signed, line = unlinked != line, unlinked
        for pat, rep in LINE_MARKUP:       # first, so a time inside <small> or bold markup is plain text when SIG_TIME looks
            line = pat.sub(rep, line)
        unsigned = UNSIGNED.sub("", SIG_TIME.sub("", line))
        signed, line = signed or unsigned != line, unsigned
        line = EMPTY_PAREN.sub("", line).rstrip()
        line = (SIG_TAIL.sub("", line) if signed else TRAIL_DASH.sub("", line)).lstrip(":*#; \t-—–").strip()
        if not line or HEADING.match(line):
            continue
        if not simp.isdisjoint(line):
            counts["para_simplified"] += 1
            continue
        counts["para_used"] += 1
        yield line


def run(paths, flt, counts):
    kept = []
    for ns, title, text in pages(paths):
        if not wanted(ns, title):
            continue
        if REDIRECT.match(text):
            counts["pages_redirect"] += 1
            continue
        counts[f"pages_ns{ns}"] += 1
        for para in paragraphs(text, flt.simp, counts):
            for s in clean.clean_text(para, flt, counts):    # per sentence: `您好，Name。謝謝…` splits into a name line and the rest
                if GREETING.match(s):
                    counts["sent_greeting"] += 1
                else:
                    kept.append(s)
    return kept


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", nargs="+", required=True); ap.add_argument("--out", required=True)
    clean.add_eval_args(ap)
    a = ap.parse_args(argv)
    ev, info = clean.eval_sentences(a.tune, a.eval)
    flt, counts = clean.Filter(clean.load_simp_only(), ev), collections.Counter()
    kept = run(a.dump, flt, counts)
    os.makedirs(a.out, exist_ok=True)
    clean.write_lines(os.path.join(a.out, "wikitalk.txt"), kept)
    inputs = {"dump": {os.path.basename(p): clean.sha256_file(p) for p in a.dump}}
    m = clean.source_manifest("zhwiki talk", inputs, kept, flt, counts, info)
    clean.write_manifest(os.path.join(a.out, "wikitalk.manifest.json"), m)
    print(f"wikitalk: {m['rows']} rows, {m['chars']} chars, removed {m['removed']}")


if __name__ == "__main__":
    main()
