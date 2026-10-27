"""CE3 S2 (docs/contracts/chat-eval-3.md section 2.2): PTT pushes as chat clause rows, built from the 2026-10-25 snapshot.

  python3 build_ptt_chat.py SRC OUT --sets ctune cdev cjudge-1 [cjudge-2 ...] [--exclude ROWFILE ...] [--corrections FILE]
      SRC/<board>/<article>.html -> OUT/<set>.rows.txt (`context|sentence|reading`, the format lm_eval reads), OUT/<set>.sources.txt
      (`ptt` per row), OUT/<set>.sha256. Prints counts only. --draft writes <set>.draft.txt instead of freezing.

Only pushes are used (ptt_clean.merged_pushes_by: same account, adjacent, within 3 minutes, is one message). An article outside
2015-07..2026-08 is dropped as soon as its time line is parsed, before any push text is looked at. A message is one `<board>/<article>#<n>`
(n = its place among the merged pushes of the article); its set is the bucket of sha256(`ptt:` + that id) (e2common.bucket_set), so the
clauses of one message stay together. Filters as the S1 side: a URL, a personal-data or secret pattern, a code fence drops the message;
the rest is cut into 2-30 character Han segments, the context of the first is blank and of later ones the previous segment. A sentence
already seen (or in an --exclude file: the frozen rows of the S1 side and of the other chat sets) is dropped. Each set is then drawn
from its own bucket, whole messages at a time (seed 20261027), at most ACCOUNT_CAP rows per account: ctune 6,000, cdev 5,000, cjudge-k
3,000 from the judge pool (articles up to 2026-07) minus the sentences of the frozen cjudge-1..k-1 read back from OUT, so
asking for cjudge-2 later changes nothing already frozen, and a set frozen with other content is never rewritten. Readings come from
tools/readings.py; CHECK and unreadable rows are dropped and counted, and more than 10% CHECK stops the freeze. OUT also gets
<set>.origin.tsv (the drawn messages, private) and <set>.manifest.json."""
import argparse
import glob
import json
import os
import random
import re
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "sj-lm-7"))
import build_chatclause as bc  # noqa: E402
import e2common as c  # noqa: E402
import ptt_clean  # noqa: E402

ACCOUNT_CAP = 20
SIZES = {"ctune": 6000, "cdev": 5000}
CJUDGE_SIZE = 3000
TIME = re.compile(r'article-meta-tag">時間</span><span class="article-meta-value">\w{3} (\w{3}) +\d+ [\d:]+ (\d{4})<')
MONTHS = {m: f"{i:02d}" for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), 1)}


def article_month(page):
    """'YYYY-MM' of the article's time line (no locale: the English month name is looked up), else None."""
    m = TIME.search(page)
    return f"{m.group(2)}-{MONTHS[m.group(1)]}" if m and m.group(1) in MONTHS else None


def collect(src, exclude=frozenset()):
    """-> ({set: {message id: (account, month, [(context, sentence)])}}, counts). Sets: ctune, cdev, cjudge (the pool, up to CJUDGE_LAST)."""
    pools, n, seen = defaultdict(dict), Counter(), set()
    for f in sorted(glob.glob(os.path.join(src, "*", "*.html"))):
        page = open(f, encoding="utf-8", errors="replace").read()
        mon = article_month(page)
        if mon is None or not c.WINDOW[0] <= mon <= c.WINDOW[1]:
            n["articles_out_of_window" if mon else "articles_no_time"] += 1
            continue
        unit = os.path.basename(os.path.dirname(f)) + "/" + os.path.basename(f)[:-5]
        for k, (user, text) in enumerate(ptt_clean.merged_pushes_by(page)):
            n["messages"] += 1
            if bc.URL.search(text):
                n["url"] += 1
            elif bc.private_rule(text):
                n["private"] += 1
            elif bc.FENCE in text:
                n["code"] += 1
            else:
                mid = f"{unit}#{k}"
                name = c.bucket_set("ptt", mid)
                if name == "cjudge" and mon > c.CJUDGE_LAST:
                    n["cjudge_bucket_after_2026_07"] += 1
                    continue
                segs = c.segments(text)
                for i, s in enumerate(segs):
                    if s in exclude:
                        n["overlap"] += 1
                    elif s in seen:
                        n["duplicate"] += 1
                    else:
                        seen.add(s)
                        pools[name].setdefault(mid, (user, mon, []))[2].append((segs[i - 1] if i else "", s))
    return pools, n


def build(src, out, sets, exclude=frozenset(), corrections=None, readings=c.readings, draft=False):
    """Builds and freezes each set of `sets` (in the order ctune, cdev, cjudge-1, cjudge-2, ...) into `out`. A set is drawn from its own
    bucket alone, so asking for cjudge-2 later leaves ctune, cdev and cjudge-1 as they were; cjudge-k is the pool minus the messages and
    sentences of the frozen cjudge-1..k-1 read back from `out`. A set already frozen with other content stops the run (build_chatclause
    freeze_ce3). `exclude`: sentences of the other side and of the other chat sets (contract section 3)."""
    pools, counts = collect(src, exclude)
    os.makedirs(out, exist_ok=True)
    report = {"counts": dict(counts), "sets": {}}
    for name in sorted(sets, key=lambda n: (bc.ce3_name(n) or sys.exit(f"build_ptt_chat: unknown set {n}"))):
        kind, k = bc.ce3_name(name)
        prior_sents = bc.prior_cjudge(out, k) if kind == "cjudge" else set()
        items = []
        for mid, (user, _mon, rows) in pools[kind].items():
            rows = [r for r in rows if r[1] not in prior_sents]
            if rows:
                items.append((mid, rows, user))
        cap = CJUDGE_SIZE if kind == "cjudge" else SIZES[kind]
        take = c.draw_messages(items, cap, random.Random(c.CE3_SEED), ACCOUNT_CAP)
        sample = [(x, s, "ptt", mid) for mid, rows, _u in take for x, s in rows]
        st = bc.freeze_ce3(out, name, sample, sum(len(i[1]) for i in items), {"accounts": len({u for _m, _r, u in take})}, corrections, draft,
                           readings, exclude)
        report["sets"][name] = st
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("src")
    ap.add_argument("out")
    ap.add_argument("--sets", nargs="+", required=True)
    ap.add_argument("--exclude", nargs="*", default=[])
    ap.add_argument("--corrections")
    ap.add_argument("--draft", action="store_true", help="write <set>.draft.txt (readings marked CHECK) for proofreading, freeze nothing")
    a = ap.parse_args(argv)
    print(json.dumps(build(a.src, a.out, a.sets, bc.load_exclude(a.exclude), a.corrections, draft=a.draft), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
