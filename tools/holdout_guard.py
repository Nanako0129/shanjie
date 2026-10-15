#!/usr/bin/env python3
"""Containment matching of held-out sentences against what the counting scripts see (docs/contracts/holdout-refresh.md section 8).

Matching runs on the counting layer: text -> build_counts.convert -> build_counts.HAN runs. A run that contains a held-out sentence
whole (the sentence itself or its converted form) is a hit. Matcher is experiments/s7f-corpus/clean.py's.

  holdout_guard.py report [--rows FILE...] [--text FILE...] [--row-files FILE...] [--wiki DUMP --articles N --skip M]
      --rows       patterns (pool or held-out set): strictly 3 fields `context|sentence|reading`; default: every .txt under eval/holdout/
      --text       plain text, converted line by line
      --row-files  sources in the CLI's parse_rows shape: 2 or 3 fields, sentence is field 2
      --wiki       the build_counts article path (TEMPLATE, MARKUP, SENT, convert, HAN)
    Prints, per source, `source<TAB>units<TAB>hit units`, then `rows<TAB>path:line` for every matched pattern row.
    Never prints sentence or row text. Errors carry only path, line number and field count.
  The counting scripts take the same matcher through `--holdout-guard [ROWS...]` (default off).

Standard library plus the project's own build_counts / clean.
"""
import argparse
import collections
import glob
import itertools
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("experiments/s2", "experiments/s7f-corpus", "reference/proto"):
    sys.path.insert(0, os.path.join(ROOT, *_p.split("/")))

BOM = "﻿"


class GuardError(Exception):
    """Message holds a path, line number and field count only, never row text."""


def read_lines(path):
    with open(path, "rb") as f:
        data = f.read().decode("utf-8", errors="replace")
    if data.startswith(BOM):
        data = data[1:]
    lines = data.split("\n")
    return lines[:-1] if lines[-1] == "" else lines


def parse_rows(path, strict):
    """[(line_no, sentence)]. strict: exactly 3 fields. Otherwise 2 or 3, sentence is field 2 (CLI parse_rows)."""
    out = []
    for n, line in enumerate(read_lines(path), 1):
        line = line.rstrip("\r")
        if not line.strip() or line.startswith("#"):
            continue
        if strict:
            f = line.split("|")
            ok = len(f) == 3
        else:
            f = line.split("|", 2)
            ok = len(f) in (2, 3)
        if not ok:
            raise GuardError(f"{path}:{n}: {len(f)} field(s), expected {'3' if strict else '2 or 3'}")
        if f[1]:
            out.append((n, f[1]))
    return out


def default_rows():
    return sorted(glob.glob(os.path.join(ROOT, "eval", "holdout", "**", "*.txt"), recursive=True))


def build_matcher(paths, conv):
    """(Matcher, {pattern: ["path:line", ...]}) from 3-field row files; patterns are each sentence and its converted form."""
    import clean
    owners = collections.defaultdict(list)
    for p in paths or default_rows():
        for n, s in parse_rows(p, strict=True):
            for pat in {s, conv(s)}:
                owners[pat].append(f"{p}:{n}")
    return clean.Matcher(owners), owners


def han_runs(text, conv):
    import build_counts as bc
    return bc.HAN.findall(conv(text))


def article_runs(raw, conv):
    """build_counts.count_batch's per-article path (non --mw): unescape, TEMPLATE, MARKUP, SENT, convert, HAN."""
    import build_counts as bc
    t = raw.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").replace("&quot;", '"')
    for _ in range(3):
        t = bc.TEMPLATE.sub("", t)
    for pat, rep in bc.MARKUP:
        t = pat.sub(rep, t)
    for s in bc.SENT.findall(t):
        yield from bc.HAN.findall(conv(s))


def scan(units, matcher, owners, hit_rows):
    """units: iterable of lists of Han runs. Returns (n units, n units with a hit); matched pattern rows go into hit_rows."""
    n = hit = 0
    for runs in units:
        n += 1
        found = False
        for r in runs:
            for p in matcher.find(r):
                found = True
                hit_rows.update(owners[p])
        hit += found
    return n, hit


def report(a, conv):
    matcher, owners = build_matcher(a.rows, conv)
    hit_rows, results = set(), []
    for p in a.text:
        results.append((p, scan((han_runs(l, conv) for l in read_lines(p)), matcher, owners, hit_rows)))
    for p in a.row_files:
        results.append((p, scan((han_runs(s, conv) for _, s in parse_rows(p, strict=False)), matcher, owners, hit_rows)))
    if a.wiki:
        import build_counts as bc
        bc.DUMP = a.wiki
        arts = itertools.islice(bc.articles(a.skip + a.articles), a.skip, None)
        results.append(("wiki", scan((list(article_runs(x, conv)) for x in arts), matcher, owners, hit_rows)))
    for name, (n, hit) in results:
        print(f"{name}\t{n}\t{hit}")
    key = lambda r: (r.rsplit(":", 1)[0], int(r.rsplit(":", 1)[1]))
    for r in sorted(hit_rows, key=key):
        print(f"rows\t{r}")


def main(argv=None, conv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("report")
    r.add_argument("--rows", nargs="*", default=[])
    r.add_argument("--text", nargs="*", default=[])
    r.add_argument("--row-files", nargs="*", default=[])
    r.add_argument("--wiki")
    r.add_argument("--articles", type=int, default=0)
    r.add_argument("--skip", type=int, default=0)
    a = ap.parse_args(argv)
    if conv is None:
        import clean
        conv = clean.converter()
    try:
        report(a, conv)
    except (GuardError, OSError) as e:
        print(f"holdout_guard: {e}", file=sys.stderr)  # OSError text carries the path only
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
