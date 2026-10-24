"""PTT pages -> cleaned sentences for the SJ-LM-7 colloquial counts (docs/contracts/sj-lm-7.md section 2.4).
Body and pushes are kept apart (weights x1 and x5). A push line is capped in length, so one remark often runs over two or more
pushes: adjacent pushes by the same account within MERGE_MINUTES are joined before splitting (maintainer 2026-10-24). One unit
(an article's body, or all its pushes) gives at most UNIT_CAP sentences. Cleaning rules are p2clean's (exp/p2-data, copied next to this file).
Usage: python3 experiments/sj-lm-7/ptt_clean.py PTT_DIR OUT_DIR   (PTT_DIR/<board>/<article>.html; writes OUT_DIR/body.tsv and push.tsv:
unit<TAB>sentence, deduplicated; prints counts only). The tests are test_ptt_clean.py."""
import collections
import functools
import glob
import html
import json
import os
import re
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

MERGE_MINUTES = 3   # same account, adjacent, within this gap: one remark split by the push length limit
UNIT_CAP = 50       # sentences per unit (contract section 2.4)
# Display widths of a wrapped body line: in 3,000 crawled articles (2026-10-25) the 77-80 column lines number 9,910 and 4-6% of them end in
# sentence-final punctuation; 76 columns and below the share is 17-46%, so those breaks are the author's (research log 2026-10-25).
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
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in line)


def paragraphs(body_text):
    """PTT's web view wraps a long line at the 80-column screen: a line WRAP_MIN..WRAP_MAX columns wide (full-width characters count 2)
    goes on in the next line and is joined to it; any other line break is the author's own and ends the piece (one per output line).
    Quoted reply lines are dropped (they are someone else's sentences, repeated)."""
    out, cur = [], []
    for line in body_text.split("\n"):
        line = line.rstrip()
        if line.startswith(QUOTE):
            continue
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


def main(src, out):
    P = load_p2clean()
    os.makedirs(out, exist_ok=True)
    stats = {}
    for kind in ("body", "push"):
        seen, n_units, why, rows = set(), 0, collections.Counter(), []
        for f in sorted(glob.glob(os.path.join(src, "*", "*.html"))):
            page = open(f, encoding="utf-8", errors="replace").read()
            unit = os.path.basename(os.path.dirname(f)) + "/" + os.path.basename(f)[:-5]
            n_units += 1
            for s in unit_sentences(P, [body(page)] if kind == "body" else merged_pushes(page), why):
                k = P.dedupe_key(s)
                if k not in seen:
                    seen.add(k)
                    rows.append(f"{unit}\t{s}\n")
        with open(os.path.join(out, f"{kind}.tsv"), "w", encoding="utf-8") as fh:
            fh.writelines(rows)
        stats[kind] = {"units": n_units, "sentences": len(rows), "han": sum(P.han_count(r.split("\t", 1)[1]) for r in rows),
                       "dropped_by": dict(why.most_common())}
    print(json.dumps(stats, ensure_ascii=False))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
