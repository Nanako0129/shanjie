#!/usr/bin/env python3
"""Measurement set of docs/contracts/english-next.md section 3.1: `e|w|reading` rows from the PTT measurement bucket (sha256(article id)[0] % 8 == 0,
the complement of build_english_next.in_build_bucket). Page parsing, cleaning, the English-word rule and the first-word segmentation are
build_english_next's own (imported), so an event here is exactly an event there.

Per article: every event (e before Chinese, w the first lexicon word of the run, e with an `E` row in --table) is collected from the body and the
merged pushes; one is picked with random.Random(f"{seed}:{board}/{article}") over the sorted events. A seeded sample of at most --cap rows follows
(random.Random(seed) over the rows sorted by board/article); the output is byte-sorted. w's reading is tools/readings.py's (space-separated syllables);
a row whose w has no reading is skipped and counted. Prints counts only. Fewer than MIN_ROWS rows is a STOP (contract section 4).

  python3 experiments/english-next/measure_rows.py --ptt DIR --table FILE --lexicon FILE... --out FILE [--seed 20261025] [--cap 3000] [--jobs N]
"""
import argparse
import glob
import multiprocessing as mp
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import build_english_next as B  # noqa: E402  (also puts experiments/sj-lm-7 and reference/proto on sys.path)
PC = B.PC

MIN_ROWS = 500
_G = {}


def table_es(path):
    with open(path, encoding="utf-8") as f:
        return {l.split("\t")[1] for l in f if l.startswith("E\t")}


def _init(lexicon, es):
    _G["words"], _G["es"] = B.load_words(lexicon), es
    _G["maxlen"] = max(map(len, _G["words"]))


def events(path):
    """(key, sorted [(e, w)]) of one measurement-bucket article, or None when it is in the build bucket."""
    board, aid = os.path.basename(os.path.dirname(path)), os.path.basename(path)[:-5]
    if B.in_build_bucket(aid):
        return None
    page = open(path, "rb").read().decode("utf-8", errors="replace")
    out = set()
    for t in [PC.body(page)] + PC.merged_pushes(page):
        out |= {(e, w) for e, w in B.pairs(t, _G["words"], _G["maxlen"]) if w and e in _G["es"]}
    return f"{board}/{aid}", sorted(out)


def pick(key, evs, seed):
    return random.Random(f"{seed}:{key}").choice(evs)


def build(ptt, es, lexicon, reader, seed=20261025, cap=3000, jobs=1):
    """(lines, report). `reader.read(w)` -> (syllables or None, notes, gaps), like tools/readings.Reader."""
    paths = sorted(glob.glob(os.path.join(ptt, "*", "*.html")))
    if jobs > 1:
        with mp.Pool(jobs, _init, (lexicon, es)) as pool:
            res = pool.map(events, paths, chunksize=64)
    else:
        _init(lexicon, es)
        res = [events(p) for p in paths]
    res = [r for r in res if r is not None]
    withev = sorted((k, evs) for k, evs in res if evs)
    chosen = [(k, *pick(k, evs, seed)) for k, evs in withev]
    skipped, rows = 0, []
    for k, e, w in chosen:
        syl = reader.read(w)[0]
        if syl:
            rows.append(f"{e}|{w}|{' '.join(syl)}")
        else:
            skipped += 1
    if len(rows) > cap:
        rows = random.Random(seed).sample(rows, cap)
    rows.sort(key=lambda r: r.encode())
    return rows, {"articles_in_bucket": len(res), "articles_with_events": len(withev), "events": sum(len(e) for _, e in withev),
                  "rows": len(rows), "skipped_no_reading": skipped}


def default_reader():
    import ime
    import readings
    return readings.make_reader(ime.Lexicon(os.path.join(ROOT, "data", "lexicon", "mcbpmf-data.txt")))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ptt", required=True)
    ap.add_argument("--table", required=True)
    ap.add_argument("--lexicon", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=20261025)
    ap.add_argument("--cap", type=int, default=3000)
    ap.add_argument("--jobs", type=int, default=os.cpu_count())
    a = ap.parse_args()
    rows, report = build(a.ptt, table_es(a.table), a.lexicon, default_reader(), a.seed, a.cap, a.jobs)
    print(report)
    if len(rows) < MIN_ROWS:
        sys.exit(f"STOP: {len(rows)} rows, fewer than {MIN_ROWS} (contract section 4)")
    with open(a.out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("".join(r + "\n" for r in rows))


if __name__ == "__main__":
    main()
