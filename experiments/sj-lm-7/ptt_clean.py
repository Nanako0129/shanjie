"""PTT pages -> cleaned sentences for the SJ-LM-7 colloquial counts (docs/contracts/sj-lm-7.md section 2.4).
Body and pushes are kept apart (weights x1 and x5). A push line is capped in length, so one remark often runs over two or more
pushes: adjacent pushes by the same account within MERGE_MINUTES are joined before splitting (maintainer 2026-10-24). One unit
(an article's body, or all its pushes) gives at most UNIT_CAP sentences. Cleaning rules are p2clean's (exp/p2-data, copied next to this file).
Usage: python3 experiments/sj-lm-7/ptt_clean.py PTT_DIR OUT_DIR [--jobs N]   (PTT_DIR/<board>/<article>.html; writes OUT_DIR/body.tsv and
push.tsv: unit<TAB>sentence, deduplicated; prints counts only). --jobs cleans pages in N forked processes; the pages are deduplicated in the
same sorted order as with one process, so the output is byte-identical. The tests are test_ptt_clean.py."""
import argparse
import collections
import functools
import glob
import html
import json
import multiprocessing
import os
import re
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

MERGE_MINUTES = 3   # same account, adjacent, within this gap: one remark split by the push length limit
UNIT_CAP = 50       # sentences per unit (contract section 2.4)
# Display widths of a wrapped body line (W, F and ambiguous A count 2): in 3,000 crawled articles (2026-10-25) the 77-80 column lines number
# 9,752 and 5-7% of them end in sentence-final punctuation; 66-76 columns 31-51%, narrower 24%, so those breaks are the author's (research log).
WRAP_MIN, WRAP_MAX = 77, 80

PUSH = re.compile(r'<div class="push">.*?<span class="f3 hl push-userid">([^<]*)</span>\s*'
                  r'<span class="f3 push-content">(.*?)</span>\s*<span class="push-ipdatetime">([^<]*)</span>', re.S)
TIME = re.compile(r"(\d\d)/(\d\d)\s+(\d\d):(\d\d)")


def text(x):
    return html.unescape(re.sub(r"<[^>]+>", "", x))


def minutes(stamp):
    m = TIME.search(stamp)
    if not m:
        return None
    mo, d, h, mi = map(int, m.groups())
    return ((mo * 31 + d) * 24 + h) * 60 + mi   # ordering within one article only


def join(a, b):
    return a + (" " if a[-1:].isascii() and a[-1:].isalnum() and b[:1].isascii() and b[:1].isalnum() else "") + b


def merged_pushes(page):
    """[(user, minute, content)] in page order -> remarks, adjacent same-account pushes joined."""
    out = []
    for user, content, stamp in PUSH.findall(page):
        user, c, t = user.strip(), text(content).lstrip(":").strip(), minutes(stamp)
        if not c:
            continue
        if out and out[-1][0] == user and (t is None or out[-1][1] is None or 0 <= t - out[-1][1] <= MERGE_MINUTES):
            out[-1] = (user, t, join(out[-1][2], c))
        else:
            out.append((user, t, c))
    return [c for _, _, c in out]


def body(page):
    m = re.search(r'<div id="main-content"[^>]*>(.*)', page, re.S)
    if not m:
        return ""
    x = re.split(r'<div class="push">', m.group(1))[0]
    x = re.sub(r'<div class="article-metaline(-right)?">.*?</div>', "", x, flags=re.S)   # author, board, title, time; a stray extra </div> is only a tag
    x = re.split(r'<span class="f2">※ 發信站', x)[0]
    return paragraphs(text(x).split("\n--\n")[0])


QUOTE = (": ", "※ 引述")   # a quoted reply line (": " is how PTT prefixes the text being answered) or the line that names whom it quotes


def width(line):
    return sum(2 if unicodedata.east_asian_width(c) in "WFA" else 1 for c in line)   # PTT is a Big5 terminal: ambiguous (… ※ “ ”) is 2 too


def paragraphs(body_text):
    """PTT's web view wraps a long line at the 80-column screen: a line WRAP_MIN..WRAP_MAX columns wide (East Asian W, F and ambiguous A characters count 2)
    goes on in the next line and is joined to it; any other line break is the author's own and ends the piece (one per output line).
    Quoted reply lines are dropped (they are someone else's sentences, repeated) and end the piece like a blank line."""
    out, cur = [], []
    for line in body_text.split("\n"):
        line = line.rstrip()
        if line.startswith(QUOTE):
            line = ""                                     # a quote ends the piece: a wrap cannot continue across it
        if line.strip():
            cur.append(line.strip())
            if WRAP_MIN <= width(line) <= WRAP_MAX:
                continue
        if cur:
            out.append(cur)
        cur = []
    if cur:
        out.append(cur)
    return "\n".join(functools.reduce(join, p) for p in out)


def load_p2clean():
    import p2clean as p            # imported here so the pure helpers (and their tests) need neither OpenCC nor the evaluation guard
    p.bc.load_conv()               # fills TRAD_SIMP_ONLY, empty until then
    assert len(p.bc.TRAD_SIMP_ONLY) > 1000
    return p


def unit_sentences(P, texts, why):
    kept = []
    for x in texts:
        for s in P.split_sentences(x):
            r = P.reject(s, P.bc.TRAD_SIMP_ONLY)
            if r:
                why[r] += 1
            else:
                kept.append(s)
    if len(kept) > UNIT_CAP:
        why["unit_cap"] += len(kept) - UNIT_CAP
    return kept[:UNIT_CAP]


_P = None   # p2clean, set by main; forked workers inherit it


def _page(f):
    """One page: (unit, {kind: ([(sentence, dedupe key, han count)], rejection counts)}). Pure, so pages can be cleaned in any
    process; the key and the count are computed here so the parent only looks keys up."""
    page = open(f, encoding="utf-8", errors="replace").read()
    unit = os.path.basename(os.path.dirname(f)) + "/" + os.path.basename(f)[:-5]
    res = {}
    for kind in ("body", "push"):
        why = collections.Counter()
        kept = unit_sentences(_P, [body(page)] if kind == "body" else merged_pushes(page), why)
        res[kind] = ([(s, _P.dedupe_key(s), _P.han_count(s)) for s in kept], why)
    return unit, res


def _pages(files, jobs, chunksize):
    """_page over files, in their order. jobs > 1: a forked pool (the workers inherit _P); leaving early (an error, Ctrl-C) terminates it."""
    if jobs == 1:
        yield from map(_page, files)
        return
    with multiprocessing.get_context("fork").Pool(jobs) as pool:
        yield from pool.imap(_page, files, chunksize)     # imap returns results in the order of files


def main(src, out, jobs=1, chunksize=256):
    global _P
    if jobs < 1:
        raise ValueError(f"jobs must be at least 1, not {jobs}")
    _P = load_p2clean()
    os.makedirs(out, exist_ok=True)
    files = sorted(glob.glob(os.path.join(src, "*", "*.html")))
    # ponytail: both kinds' rows and keys stay in memory together (one read per page); fine on the 64 GB VM, split by kind if it is not.
    acc = {kind: (set(), collections.Counter(), [], [0]) for kind in ("body", "push")}
    for unit, res in _pages(files, jobs, chunksize):
        for kind, (sents, why) in res.items():
            seen, w, rows, han = acc[kind]
            w.update(why)
            for s, k, n in sents:
                if k not in seen:
                    seen.add(k)
                    rows.append(f"{unit}\t{s}\n")
                    han[0] += n
    stats = {}
    for kind, (_, why, rows, han) in acc.items():
        with open(os.path.join(out, f"{kind}.tsv"), "w", encoding="utf-8") as fh:
            fh.writelines(rows)
        stats[kind] = {"units": len(files), "sentences": len(rows), "han": han[0], "dropped_by": dict(why.most_common())}
    print(json.dumps(stats, ensure_ascii=False))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("src")
    ap.add_argument("out")
    ap.add_argument("--jobs", type=int, default=1)
    a = ap.parse_args()
    if a.jobs < 1:
        ap.error("--jobs must be at least 1")
    main(a.src, a.out, a.jobs)
