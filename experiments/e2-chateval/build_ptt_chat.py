"""CE3 S2 (docs/contracts/chat-eval-3.md section 2.2): PTT pushes as chat clause rows, built from the 2026-10-25 snapshot.

  python3 build_ptt_chat.py SRC OUT --sets ctune cdev cjudge-1 [cjudge-2 ...] [--exclude ROWFILE ...] [--corrections FILE]
      SRC/<board>/<article>.html -> OUT/<set>.rows.txt (`context|sentence|reading`, the format lm_eval reads), OUT/<set>.sources.txt
      (`ptt` per row), OUT/<set>.sha256. Prints counts only.

Only pushes are used (ptt_clean.merged_pushes_by: same account, adjacent, within 3 minutes, is one message). An article outside
2015-07..2026-08 is dropped as soon as its time line is parsed, before any push text is looked at. A message is one `<board>/<article>#<n>`
(n = its place among the merged pushes of the article); its set is the bucket of sha256(`ptt:` + that id) (e2common.bucket_set), so the
clauses of one message stay together. Filters as the S1 side: a URL, a personal-data or secret pattern, a code fence drops the message;
the rest is cut into 2-30 character Han segments, the context of the first is blank and of later ones the previous segment. A sentence
already seen (or in an --exclude file) is dropped. Then per set at most ACCOUNT_CAP rows per account (a seeded sample), then
min(size, pool) rows by one seeded shuffle: ctune 6,000, cdev 5,000, and cjudge-k the k-th slice of 3,000 of the judge pool (articles up
to 2026-07), so the cjudge sets never repeat. Readings come from tools/readings.py; CHECK and unreadable rows are dropped (and counted)."""
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
    """-> ({set: [(account, month, context, sentence)]}, counts). Sets: ctune, cdev, cjudge (the pool, up to CJUDGE_LAST)."""
    pools, n, seen = defaultdict(list), Counter(), set()
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
                name = c.bucket_set("ptt", f"{unit}#{k}")
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
                        pools[name].append((user, mon, segs[i - 1] if i else "", s))
    return pools, n


def cap_accounts(rows, rng):
    """At most ACCOUNT_CAP rows per account, a seeded sample of the account's rows; the order of the rest is kept."""
    by = defaultdict(list)
    for i, r in enumerate(rows):
        by[r[0]].append(i)
    drop = set()
    for idx in by.values():
        if len(idx) > ACCOUNT_CAP:
            drop |= set(idx) - set(rng.sample(idx, ACCOUNT_CAP))
    return [r for i, r in enumerate(rows) if i not in drop]


def build(src, out, sets, exclude=frozenset(), corrections=None, readings=c.readings):
    pools, counts = collect(src, exclude)
    rng = random.Random(c.CE3_SEED)
    os.makedirs(out, exist_ok=True)
    report = {"counts": dict(counts), "sets": {}}
    capped = {name: cap_accounts(pools[name], rng) for name in ("ctune", "cdev", "cjudge")}
    for name in sets:
        ce = bc.ce3_name(name)
        if not ce:
            sys.exit(f"build_ptt_chat: unknown set {name}")
        rows = list(capped[ce[0]])
        random.Random(c.CE3_SEED).shuffle(rows)
        rows = rows[(ce[1] - 1) * CJUDGE_SIZE: ce[1] * CJUDGE_SIZE] if ce[0] == "cjudge" else rows[:SIZES[ce[0]]]
        frozen, _draft, st = bc.build_period([(x, s) for _u, _m, x, s in rows], bc.load_corrections(corrections), readings)
        text = "".join(f"{x}|{s}|{r}\n" for x, s, r in frozen)
        c.write_lf(os.path.join(out, f"{name}.rows.txt"), text)
        c.write_lf(os.path.join(out, f"{name}.sources.txt"), "ptt\n" * len(frozen))
        c.write_lf(os.path.join(out, f"{name}.sha256"), f"{c.sha256_bytes(text.encode('utf-8'))}  {name}.rows.txt\n")
        report["sets"][name] = dict(st, pool_after_account_cap=len(capped[ce[0]]), accounts=len({u for u, *_ in rows}))
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("src")
    ap.add_argument("out")
    ap.add_argument("--sets", nargs="+", required=True)
    ap.add_argument("--exclude", nargs="*", default=[])
    ap.add_argument("--corrections")
    a = ap.parse_args(argv)
    print(json.dumps(build(a.src, a.out, a.sets, bc.load_exclude(a.exclude), a.corrections), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
