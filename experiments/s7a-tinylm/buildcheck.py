"""S7a construction check and the record-only substring ratio (contract section 2.2).

  python buildcheck.py construction --coll colloquial-train.txt --out report.json [--charcount DIR/charcount.json]
  python buildcheck.py substr --work DIR --rows cvtune.txt wikitune.txt [--procs N] [--flags DIR]

construction: runs on the RAW sources (never on converted sentences: conversion changes characters, so the hash changes):
  wikitune articles start at index 300,000: the `articles` prep.py recorded in charcount.json must not exceed that
  (build_tune.py hard-codes the index, so the constant here is a copy; without --charcount the report says NOT CHECKED);
  for the cv-* files and cmn_sentences.tsv.bz2, in build_tune.py's order: is_tune(raw) false lines == lines of colloquial-train.txt. Either
  failure stops (exit 1): this count, or (with --charcount) more articles than 300,000. Source file hashes go in the report.
substr: share of each set's gold sentences that occur as a substring of DIR/runs-*.txt (S7a: record only, never a stop). With --flags
  (S7d) it also writes one 0/1 per row, which fusegrid.py --tune-keep uses to choose the tuning rows alpha/tau are selected on.
"""
import argparse
import bz2
import json
import mmap
import multiprocessing as mp
import os
import sys

import tinylm  # noqa: F401  (sys.path)
import build_counts as bc  # noqa: E402
from build_tune import is_tune  # noqa: E402  the very function that split cvtune from colloquial-train.txt
from prep import sha256_file  # noqa: E402
from lm_eval import rows_of  # noqa: E402

WIKI_TUNE_FIRST_INDEX = 300_000    # build_tune.py: `if i < 300_000: continue`


def raw_sources():
    """build_tune.py's file order: cv-* sorted, then cmn_sentences.tsv.bz2."""
    d = os.path.join(bc.SRC, "colloquial")
    return [os.path.join(d, f) for f in sorted(os.listdir(d)) if f.startswith("cv-")] + [os.path.join(d, "cmn_sentences.tsv.bz2")]


def raw_lines(path):
    f = bz2.open(path, "rt", encoding="utf-8") if path.endswith(".bz2") else open(path, encoding="utf-8")
    with f:
        for line in f:
            yield line.rstrip("\n").split("\t")[-1]


def construction_check(raws, n_train_lines, articles=None):
    """raws: iterable of RAW sentences. is_tune is applied to the raw text only.
    articles: the `articles` value prep.py recorded in charcount.json (what the training text really used); None = not recorded, said in the report."""
    total = tune = 0
    for raw in raws:
        total += 1
        tune += bool(is_tune(raw))
    nontune = total - tune
    reasons = []
    if nontune != n_train_lines:
        reasons.append(f"is_tune(raw) false lines {nontune} != colloquial-train.txt lines {n_train_lines}")
    if articles is not None and articles > WIKI_TUNE_FIRST_INDEX:
        reasons.append(f"prep used {articles} wiki articles; wikitune starts at index {WIKI_TUNE_FIRST_INDEX}")
    return {"wiki_articles_used": articles if articles is not None else "NOT CHECKED: no articles value recorded", "raw_total": total, "is_tune_true": tune, "is_tune_false": nontune, "train_lines": n_train_lines,
            "wikitune_first_index": WIKI_TUNE_FIRST_INDEX, "ok": not reasons, "reasons": reasons}


def count_lines(path):
    with open(path, encoding="utf-8") as f:
        return sum(1 for _ in f)


def cmd_construction(a):
    files = raw_sources()
    articles = None
    if a.charcount and os.path.isfile(a.charcount):
        articles = json.load(open(a.charcount, encoding="utf-8"))["report"].get("articles")
    rep = construction_check((r for p in files for r in raw_lines(p)), count_lines(a.coll), articles)
    rep["source_sha256"] = {os.path.basename(p): sha256_file(p) for p in files}
    rep["colloquial_train_sha256"] = sha256_file(a.coll)
    json.dump(rep, open(a.out, "w"), indent=1)
    print(json.dumps(rep, indent=1))
    if not rep["ok"]:
        sys.exit("buildcheck: CONSTRUCTION CHECK FAILED")


# ------------------------------------------------------------------ record-only: gold sentences inside the training text
def hits_in_files(sents, paths):
    """For each of sents, whether it occurs as a substring of any of the files (utf-8 bytes, mmap)."""
    maps = []
    for p in paths:
        f = open(p, "rb")
        maps.append((f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)))
    try:
        return [any(m.find(s.encode("utf-8")) >= 0 for _, m in maps) for s in sents]
    finally:
        for f, m in maps:
            m.close(); f.close()


def _chunk(args):
    return hits_in_files(*args)


def substring_hits(sents, paths, procs=1):
    """hits_in_files over a process pool, in the order of sents (an empty sentence is not a hit)."""
    if procs <= 1:
        return [bool(s) and h for s, h in zip(sents, hits_in_files(sents, paths))]
    step = max(1, len(sents) // (procs * 4))
    with mp.Pool(procs) as pool:
        hits = [h for part in pool.map(_chunk, [(sents[i:i + step], paths) for i in range(0, len(sents), step)]) for h in part]
    return [bool(s) and h for s, h in zip(sents, hits)]


def substring_ratio(sents, paths, procs=1):
    """(hits, number of non-empty sentences): the S7a record-only ratio."""
    hits = substring_hits(sents, paths, procs)
    return sum(hits), sum(1 for s in sents if s)


def cmd_substr(a):
    paths = [os.path.join(a.work, f"runs-{n}.txt") for n in ("wiki", "coll")]
    for rows in a.rows:
        golds = [r[0] for r in rows_of([rows])]      # the evaluation's own row reader: flag i is score.py's row i by construction
        hits = substring_hits(golds, paths, a.procs)
        hit, n = sum(hits), sum(1 for s in golds if s)
        print(f"substr {os.path.basename(rows)} gold sentences {n}, in training text {hit} ({100 * hit / max(1, n):.2f}%)")
        if a.flags:
            # S7d: one 0/1 per row in rows-file order (score.py's row i is line i), digits and newlines only
            os.makedirs(a.flags, exist_ok=True)
            out = os.path.join(a.flags, os.path.splitext(os.path.basename(rows))[0] + ".flags")
            with open(out, "w", newline="") as f:
                f.write("".join(f"{int(h)}\n" for h in hits))
            print(f"wrote {out} ({len(hits)} rows, {hit} ones)")


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("construction")
    c.add_argument("--coll", required=True); c.add_argument("--out", required=True)
    c.add_argument("--charcount", help="DIR/charcount.json written by prep.py runs; its `articles` is checked against the wikitune index")
    s = sub.add_parser("substr")
    s.add_argument("--work", required=True); s.add_argument("--rows", nargs="+", required=True)
    s.add_argument("--flags", help="S7d: also write DIR/<rows stem>.flags, 1 where the row's gold sentence is in the training text")
    s.add_argument("--procs", type=int, default=max(1, (os.cpu_count() or 4) - 2))
    a = ap.parse_args(argv)
    {"construction": cmd_construction, "substr": cmd_substr}[a.cmd](a)


if __name__ == "__main__":
    main()
