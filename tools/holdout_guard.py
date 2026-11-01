#!/usr/bin/env python3
"""Containment matching of held-out sentences against what the counting scripts see (docs/contracts/holdout-refresh.md section 8).

Matching runs on the counting layer: text -> build_counts.convert -> build_counts.HAN runs -> build_counts.fold (the build_lm MERGE
variant fold, applied to patterns and runs alike). A run that contains a held-out sentence whole (the sentence itself or its converted
form) is a hit. Matcher is experiments/s7f-corpus/clean.py's. The text pipelines are build_counts' (article_runs, text_runs) and
build_counts_text.lines, shared with the counting scripts and s7a prep.py.

  holdout_guard.py report [--rows FILE...] [--text FILE...] [--row-files FILE...] [--wiki DUMP --articles N [--skip M] [--procs P]]
      --rows       patterns (pool or held-out set): strictly 3 fields `context|sentence|reading`, sentence all Han (build_counts.HAN);
                   default: every .txt under eval/holdout/. Sentences shorter than clean.MIN_LEN (4) are skipped and counted.
                   Zero loaded patterns is an error. The loaded/skipped counts are always printed (`patterns<TAB>loaded<TAB>skipped`).
      --text       plain text like build_counts_text.lines(): .bz2 decompressed, last field of a .tsv; streamed, bad bytes replaced
      --row-files  sources in the CLI's parse_rows shape: 2 or 3 fields, sentence is field 2
      --wiki       the build_counts article path (TEMPLATE, MARKUP, SENT, convert, HAN), one pass over the dump on P processes;
                   --articles is required, --skip M drops the first M articles (they are still decompressed once)
    Line numbers count '\\n' only (a lone '\\r', U+2028/2029/0085 do not start a line; in --rows they are an error).
    A BOM is removed from the first line only. Prints, per source, `source<TAB>units<TAB>hit units`, then `rows<TAB>path:line` for
    every matched pattern row. Never prints sentence or row text. Errors carry only path, line number and field count.
  The counting scripts take the same matcher through `--holdout-guard [ROWS...]` (default off).
  --no-trad-tw-phrases (report; same name in build_counts.py and build_counts_text.py): scan the text as the counters count it with that flag, Traditional
      sentences without the OpenCC TWPhrases pass (SJ-LM-7 contract 2.2); the patterns then also include the sentence converted that way. Default off.
"""
import argparse
import collections
import glob
import itertools
import multiprocessing as mp
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("experiments/s2", "experiments/s7f-corpus", "reference/proto"):
    sys.path.insert(0, os.path.join(ROOT, *_p.split("/")))

import build_counts as bc  # noqa: E402
import build_counts_text as bct  # noqa: E402
import clean  # noqa: E402

BOM = "﻿"
MIN_LEN = clean.MIN_LEN
START = None   # multiprocessing start method for --wiki (tests set "spawn")
ODD_BREAKS = ("\r", " ", " ", "\u0085")


class GuardError(Exception):
    """Message holds a path, line number and field count only, never row text."""


def read_lines(path):
    """Streams the lines of a file split on '\\n' only; bad bytes become U+FFFD; BOM stripped from the first line."""
    with open(path, "rb") as f:
        for i, raw in enumerate(f):
            line = raw.decode("utf-8", errors="replace")
            if i == 0 and line.startswith(BOM):
                line = line[1:]
            yield line[:-1] if line.endswith("\n") else line


def parse_rows(path, strict):
    """Yields (line_no, sentence). strict: exactly 3 fields, sentence all Han. Otherwise 2 or 3, sentence is field 2 (CLI parse_rows)."""
    for n, line in enumerate(read_lines(path), 1):
        if line.endswith("\r"):
            line = line[:-1]
        if not line.strip() or line.startswith("#"):
            continue
        if strict:
            f = line.split("|")
            ok = len(f) == 3 and not any(c in line for c in ODD_BREAKS)
        else:
            f = line.split("|", 2)
            ok = len(f) in (2, 3)
        if not ok:
            raise GuardError(f"{path}:{n}: {len(f)} field(s), expected {'3, no line-break characters' if strict else '2 or 3'}")
        if not f[1]:
            continue
        if strict and not bc.HAN.fullmatch(f[1]):
            raise GuardError(f"{path}:{n}: {len(f)} field(s), sentence is not all Han")
        yield n, f[1]


def default_rows():
    return sorted(glob.glob(os.path.join(ROOT, "eval", "holdout", "**", "*.txt"), recursive=True))


def build_matcher(paths, conv, trad_tw_phrases=True):
    """-> (Matcher, {pattern: ["path:line", ...]}, loaded rows, skipped short rows). conv = build_counts.load_conv() tuple.
    Patterns are each sentence and its converted form, folded. Raises GuardError on a bad row or when nothing loads."""
    owners = collections.defaultdict(list)
    loaded = skipped = 0
    for p in paths or default_rows():
        for n, s in parse_rows(p, strict=True):
            if len(s) < MIN_LEN:
                skipped += 1
                continue
            loaded += 1
            forms = {bc.fold(s), bc.fold(bc.convert(s, *conv))}
            if not trad_tw_phrases:    # the text the counters see keeps its Traditional wording: match that form too
                forms.add(bc.fold(bc.convert(s, *conv, trad_tw_phrases=False)))
            for pat in forms:
                owners[pat].append(f"{p}:{n}")
    if not loaded:
        raise GuardError(f"0 patterns loaded ({skipped} skipped as shorter than {MIN_LEN} characters)")
    return clean.Matcher(owners), owners, loaded, skipped


def scan_units(units, matcher):
    """units: iterable of lists of Han runs. -> (n units, n units with a hit, set of matched patterns)."""
    n = hit = 0
    pats = set()
    for runs in units:
        n += 1
        found = False
        for r in runs:
            for p in matcher.find(bc.fold(r)):
                found = True
                pats.add(p)
        hit += found
    return n, hit, pats


_G = {}


def _gw_init(matcher, conv, trad_tw_phrases=True):
    _G["m"], _G["conv"], _G["tw"] = matcher, conv, trad_tw_phrases


def _wiki_scan(batch):
    return scan_units((list(bc.article_runs(x, _G["conv"], trad_tw_phrases=_G["tw"])) for x in batch), _G["m"])


def report(a, conv):
    tw = not a.no_trad_tw_phrases
    matcher, owners, loaded, skipped = build_matcher(a.rows, conv, tw)
    print(f"patterns\t{loaded}\t{skipped}")
    pats, results = set(), []

    def add(name, res):
        pats.update(res[2])
        results.append((name, res[:2]))
    for p in a.text:
        add(p, scan_units((bc.text_runs(l, conv, trad_tw_phrases=tw) for l in bct.lines(p, errors="replace")), matcher))
    for p in a.row_files:
        add(p, scan_units((bc.text_runs(s, conv, trad_tw_phrases=tw) for _, s in parse_rows(p, strict=False)), matcher))
    if a.wiki:
        arts = itertools.islice(bc.articles(a.skip + a.articles, dump=a.wiki), a.skip, None)
        n = hit = 0
        wiki_pats = set()
        with mp.get_context(START).Pool(a.procs, initializer=_gw_init, initargs=(matcher, conv, tw)) as pool:
            for dn, dh, dp in pool.imap_unordered(_wiki_scan, bc.batches(arts)):
                n += dn; hit += dh; wiki_pats |= dp
        add("wiki", (n, hit, wiki_pats))
    for name, (n, hit) in results:
        print(f"{name}\t{n}\t{hit}")
    rows = {r for p in pats for r in owners[p]}
    for r in sorted(rows, key=lambda r: (r.rsplit(":", 1)[0], int(r.rsplit(":", 1)[1]))):
        print(f"rows\t{r}")


def main(argv=None, conv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("report")
    r.add_argument("--rows", nargs="*", default=[])
    r.add_argument("--text", nargs="*", default=[])
    r.add_argument("--row-files", nargs="*", default=[])
    r.add_argument("--no-trad-tw-phrases", action="store_true")
    r.add_argument("--wiki")
    r.add_argument("--articles", type=int)
    r.add_argument("--skip", type=int, default=0)
    r.add_argument("--procs", type=int, default=max(1, (os.cpu_count() or 4) - 2))
    a = ap.parse_args(argv)
    if a.wiki and not a.articles:
        ap.error("--wiki requires --articles")
    try:
        report(a, conv if conv is not None else bc.load_conv())
    except (GuardError, OSError) as e:
        print(f"holdout_guard: {e}", file=sys.stderr)  # OSError text carries the path only
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
