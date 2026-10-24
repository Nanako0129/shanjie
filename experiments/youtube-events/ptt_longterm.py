"""Long-term use of candidate strings in a PTT page directory (<dir>/<board>/M.<unix>.A.<hash>.html; docs/contracts/youtube-events.md part A).
Per string: articles containing it (whole page text), distinct boards, and the span in days between the earliest and latest article timestamp
(the unix seconds in the file name). Only boards whose own crawled pages span >= MIN_BOARD_SPAN_DAYS are counted.
long_term = span >= 180 days AND boards >= 5 AND articles >= 20 (net-slang thresholds). Pages are only read, never copied; output is counts.
Usage: python3 ptt_longterm.py PTT_DIR CANDIDATES.tsv OUT.tsv [--jobs N]"""
import collections
import html
import multiprocessing
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

DAY = 86400
MIN_BOARD_SPAN_DAYS = 180
MIN_SPAN_DAYS, MIN_BOARDS, MIN_ARTICLES = 180, 5, 20
NAME = re.compile(r"^M\.(\d+)\.A\.[0-9A-Fa-f]+\.html$")
_strings = None
_index = None       # first two characters -> strings, so a page is checked only for strings whose opening occurs in it


def board_hits(board_dir):
    """-> (board qualifies, {string: [articles, min ts, max ts]}) for one board directory."""
    files = [(int(m.group(1)), f) for f in os.listdir(board_dir) if (m := NAME.match(f))]
    if not files or max(t for t, _ in files) - min(t for t, _ in files) < MIN_BOARD_SPAN_DAYS * DAY:
        return False, {}
    hits = {}
    for ts, f in files:
        with open(os.path.join(board_dir, f), encoding="utf-8", errors="replace") as fh:
            text = html.unescape(re.sub(r"<[^>]+>", "", fh.read()))
        for bg in {text[i:i + 2] for i in range(len(text) - 1)} & _index.keys():
            for s in _index[bg]:
                if s in text:
                    h = hits.setdefault(s, [0, ts, ts])
                    h[0], h[1], h[2] = h[0] + 1, min(h[1], ts), max(h[2], ts)
    return True, hits


def _init(strings):
    global _strings, _index
    _strings = strings
    _index = collections.defaultdict(list)
    for s in strings:
        _index[s[:2]].append(s)


def scan(root, strings, jobs=1):
    """-> {string: {articles, boards, span_days, long_term}} over the qualifying boards of root."""
    boards = sorted(d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d)))
    paths = [os.path.join(root, b) for b in boards]
    if jobs > 1:
        with multiprocessing.Pool(jobs, _init, (strings,)) as p:
            results = p.map(board_hits, paths, chunksize=1)
    else:
        _init(strings)
        results = [board_hits(p) for p in paths]
    agg = {s: [0, 0, None, None] for s in strings}      # articles, boards, min ts, max ts
    for ok, hits in results:
        for s, (n, lo, hi) in hits.items():
            a = agg[s]
            a[0] += n
            a[1] += 1
            a[2] = lo if a[2] is None else min(a[2], lo)
            a[3] = hi if a[3] is None else max(a[3], hi)
    out = {}
    for s, (n, b, lo, hi) in agg.items():
        span = (hi - lo) // DAY if lo is not None else 0
        out[s] = {"articles": n, "boards": b, "span_days": span, "long_term": int(span >= MIN_SPAN_DAYS and b >= MIN_BOARDS and n >= MIN_ARTICLES)}
    return out


def main(root, cands, out, jobs=1):
    strings = sorted({r["string"] for r in common.read_tsv(cands)})
    res = scan(root, strings, jobs)
    common.write_tsv(out, ("string", "articles", "boards", "span_days", "long_term"), [{"string": s, **res[s]} for s in strings])
    print(f"{len(strings)} strings, {sum(r['long_term'] for r in res.values())} long-term")


if __name__ == "__main__":
    args = sys.argv[1:]
    jobs = int(args.pop(args.index("--jobs") + 1)) if "--jobs" in args else 1
    args = [a for a in args if a != "--jobs"]
    if len(args) != 3:
        sys.exit(__doc__)
    main(*args, jobs=jobs)
