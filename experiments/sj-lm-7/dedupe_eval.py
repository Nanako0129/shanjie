#!/usr/bin/env python3
"""Removes new-source sentences that match evaluation sets (docs/contracts/sj-lm-7.md section 2.4), with tools/holdout_guard.py's matcher.

  dedupe_eval.py --set LABEL=ROWS --set ... --source NAME=FILE --source ... --out DIR [--jobs N]

  --set     an evaluation set as row file(s): `context|sentence|reading`, exactly 3 fields and an all-Han sentence (holdout_guard.build_matcher).
            Give the same LABEL on several --set to make one set of them (the holdout's current shard and next/).
  --source  a sentence file: one sentence per line, or tab-separated with the sentence in the last field (`unit<TAB>sentence`,
            `source<TAB>id<TAB>sentence`). .gz/.bz2 are not read here.
  A source sentence is a hit for a set when one of its Han runs (build_counts.text_runs: convert, HAN) contains a set sentence whole, the sentence
  itself or its converted form, on build_counts.fold (holdout_guard's rule; sentences under 4 characters are not patterns). A hit for any set
  removes the sentence. The kept lines go to OUT/NAME unchanged and in order.
  Prints, per source and set, the number of source sentences that hit it (a sentence hitting two sets counts in both), then per source
  `total`, `removed`, `kept` and the SHA-256 of the output. No sentence or row text is printed; errors carry a path, line and field count only.
"""
import argparse
import collections
import hashlib
import multiprocessing as mp
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "tools"))
import holdout_guard as hg  # noqa: E402  (puts experiments/s2, s7f-corpus and reference/proto on sys.path)
import build_counts as bc  # noqa: E402

_W = {}


def _init(matchers, conv):
    _W["m"], _W["conv"] = matchers, conv


def _hits(chunk):
    """chunk: lines -> for each line the tuple of set labels it hits."""
    out = []
    for line in chunk:
        runs = bc.text_runs(line.rstrip("\n").split("\t")[-1], _W["conv"])
        out.append(tuple(label for label, m in _W["m"].items() if any(next(m.find(bc.fold(r)), None) is not None for r in runs)))
    return out


def chunks(path, size=20000):
    buf = []
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        for line in f:
            buf.append(line if line.endswith("\n") else line + "\n")
            if len(buf) == size:
                yield buf
                buf = []
    if buf:
        yield buf


def dedupe(sets, sources, out, conv, jobs=1):
    """sets: {label: [row files]}, sources: {name: file}. Writes out/name; returns {name: (total, removed, Counter per label, sha256)}."""
    matchers = {label: hg.build_matcher(paths, conv)[0] for label, paths in sets.items()}
    os.makedirs(out, exist_ok=True)
    result = {}
    pool = mp.Pool(jobs, initializer=_init, initargs=(matchers, conv)) if jobs > 1 else None
    if not pool:
        _init(matchers, conv)
    try:
        for name, path in sources.items():
            per, total, removed = collections.Counter({label: 0 for label in sets}), 0, 0
            dst = os.path.join(out, name)
            parts = list(chunks(path))
            with open(dst, "w", encoding="utf-8", newline="") as f:
                for chunk, hits in zip(parts, pool.imap(_hits, parts) if pool else map(_hits, parts)):
                    for line, h in zip(chunk, hits):
                        total += 1
                        per.update(h)
                        if h:
                            removed += 1
                        else:
                            f.write(line)
            result[name] = (total, removed, per, hashlib.sha256(open(dst, "rb").read()).hexdigest())
    finally:
        if pool:
            pool.close()
            pool.join()
    return result


def main(argv=None, conv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--set", action="append", required=True, metavar="LABEL=ROWS")
    ap.add_argument("--source", action="append", required=True, metavar="NAME=FILE")
    ap.add_argument("--out", required=True)
    ap.add_argument("--jobs", type=int, default=1)
    a = ap.parse_args(argv)
    sets, sources = collections.OrderedDict(), collections.OrderedDict()
    for x in a.set:
        label, _, path = x.partition("=")
        if not label or not path:
            ap.error("--set wants LABEL=ROWS")
        sets.setdefault(label, []).append(path)
    for x in a.source:
        name, _, path = x.partition("=")
        if not name or not path or name in sources or os.path.basename(name) != name:
            ap.error("--source wants distinct NAME=FILE values, NAME a file name")
        sources[name] = path
    try:
        res = dedupe(sets, sources, a.out, conv or bc.load_conv(), a.jobs)
    except hg.GuardError as e:
        sys.exit(f"dedupe_eval: {e}")
    for name, (total, removed, per, sha) in res.items():
        for label in sets:
            print(f"{name}\t{label}\t{per[label]}")
        print(f"{name}\ttotal\t{total}\tremoved\t{removed}\tkept\t{total - removed}\tsha256\t{sha}")


if __name__ == "__main__":
    main()
